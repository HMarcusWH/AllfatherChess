#!/usr/bin/env python3
"""OFFLINE-4B real pinned-bridge safety/restart experiment.

The complete experiment, fake server, and durable SQLite state live inside
a --network none isolated container. The bridge source adaptation is applied
to a temporary copy after byte-exact verification. No public bot permissions.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.lichess import bridge_policy, recovery_overlay
from deploy.lichess.log_capture import publish_log, shutdown_bridge
from deploy.lichess.offline_session import bridge_command, stage
from deploy.lichess.session_owner import SessionOwner
from deploy.lichess.run_recovery import (
    digest, json_file, lines_file, preserve_replays,
)
from tests.lichess.fault_plan import load_scenarios
from tests.lichess.fake_lichess_api import FakeLichess

POLICY = ROOT / "qualification/online-recovery-v2.json"
CASES = ROOT / "tests/fixtures/lichess/recovery_scenarios.json"
RESTART_CASES = {"R02", "R03", "R08", "R13"}


def _contained_children() -> list[int]:
    if os.getpid() != 1:
        raise RuntimeError("offline recovery cannot restart bridge unless supervisor is PID 1")
    return [int(p.name) for p in Path("/proc").iterdir()
            if p.name.isdigit() and int(p.name) not in (1, os.getpid())]


def terminate_isolated_children() -> None:
    """Kill detached upstream engine pgrps before another owner epoch starts."""
    for pid in _contained_children():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        # PID 1 reaps orphaned children. Zombies are not active and need
        # not block restart, but live engine/bridge descendants do.
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        remaining = []
        for pid in _contained_children():
            try:
                stat = (Path("/proc") / str(pid) / "stat").read_text()
                if stat.split(") ", 1)[1][0] != "Z":
                    remaining.append(pid)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not remaining:
            return
        time.sleep(0.05)
    raise RuntimeError("previous isolated bridge process tree did not drain")


def _launch_bridge(work, owner, log_path, *, phase):
    env = bridge_policy.sanitized_env()
    env.update(owner.environment())
    env["ALLFATHER_RECOVERY_AUDIT"] = str(owner.directory / "recovery-events.ndjson")
    out = log_path.open("wb")
    try:
        proc = subprocess.Popen(
            bridge_command(work), cwd=work["bridge"],
            env=env, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        out.close()
        raise
    return proc, out


def _active_game_subscribers(server) -> int:
    if server.sessions is None:
        return 0
    return sum(1 for row in server.sessions.snapshot()
               if row["event"] == "subscribe" and row["kind"] == "game")


def experiment(*, scenario: dict, color: str, bridge: Path, release: Path,
               output: Path, source_sha: str, timeout: float):
    output.mkdir(parents=True, exist_ok=True)
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    state = {
        "schema_version": 1, "source_commit": source_sha,
        "scenario_id": scenario["id"], "color": color,
        "bridge_commit": None, "phases": [], "runner_errors": [],
        "network_recovery_qualified": False,
        "deployment_host_qualified": False, "public_bot_release": False,
    }
    server = None
    started = False
    owner = None
    partial_logs = []
    replay_rows = []
    beginning = time.monotonic()
    json_file(output / "fault-plan.json", scenario)
    attest = {p: digest(ROOT / p) for p in policy["attested_paths"]}
    json_file(output / "source-manifest.json",
              {"source_commit": source_sha, "files": attest})
    try:
        if os.getenv("ONLINE4B_ISOLATED_CONTAINER") != "1" or os.getpid() != 1:
            raise RuntimeError("ONLINE-4B requires PID1 in --network none container")
        if scenario["mode"] != "integration" or color not in scenario["colors"]:
            raise RuntimeError("unexpected ONLINE-4B scenario scope/color")
        server = FakeLichess(color, scenario)
        with tempfile.TemporaryDirectory(prefix="online4b-") as tmp:
            workroot = Path(tmp)
            stage_info = stage(release=release, bridge=bridge, workspace=workroot,
                               server_url=server.url)
            if stage_info["release_source"] != source_sha:
                raise RuntimeError("sealed engine source does not match CI exact HEAD")
            state["bridge_commit"] = stage_info["bridge_commit"]
            overlay = recovery_overlay.apply(stage_info["bridge"])
            json_file(output / "overlay-manifest.json", overlay)
            if overlay["upstream_commit"] != state["bridge_commit"]:
                raise RuntimeError("recovery overlay source lock identity mismatch")
            server.start()
            started = True
            with SessionOwner(output / "state") as owner:
                phases = 2 if scenario["id"] in RESTART_CASES else 1
                for phase in range(phases):
                    if phase > 0:
                        terminate_isolated_children()
                        owner.new_epoch()
                    before_subscribers = _active_game_subscribers(server)
                    phase_data = {
                        "phase": phase + 1, "epoch": owner.epoch,
                        "bridge_exit": None, "observation": "launching",
                        "game_subscriptions_before": before_subscribers,
                    }
                    state["phases"].append(phase_data)
                    proc = None
                    fd = None
                    raw_path = workroot / f"bridge-phase-{phase+1}.private"
                    try:
                        proc, fd = _launch_bridge(stage_info, owner, raw_path, phase=phase)
                        if phase == 0:
                            if not server.event_stream_open.wait(timeout=35):
                                raise RuntimeError("bridge never subscribed to global event stream")
                            server.issue_challenge()
                            if not server.game_stream_open.wait(timeout=35):
                                raise RuntimeError("bridge never accepted the synthetic challenge")
                            if color == "black":
                                server.opponent_move()
                        else:
                            deadline = time.monotonic() + 35
                            while time.monotonic() < deadline:
                                if _active_game_subscribers(server) > before_subscribers:
                                    break
                                if proc.poll() is not None:
                                    break
                                time.sleep(0.05)
                            else:
                                raise RuntimeError("restarted bridge did not subscribe to active game")
                            if _active_game_subscribers(server) <= before_subscribers:
                                raise RuntimeError("recovery worker did not reopen game stream")
                        phase_data["observation"] = "game_stream_observed"
                        wait_started = time.monotonic()
                        activated_at = None
                        while time.monotonic() - wait_started < timeout:
                            fired = server.fault.evidence()["activations"]
                            if fired and activated_at is None:
                                activated_at = time.monotonic()
                            # Test crash BEFORE the server's fast scripted
                            # opponent can cause a natural mate.
                            if scenario["id"] == "R13" and fired and phase == 0:
                                if proc.poll() is None:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                phase_data["observation"] = "injected_bridge_kill"
                                break
                            if server.finished.is_set():
                                phase_data["observation"] = "server_game_complete"
                                break
                            if phase == 0 and scenario["id"] in RESTART_CASES and fired:
                                if time.monotonic() - activated_at > 1.4:
                                    phase_data["observation"] = "restart_requested_after_fault"
                                    break
                            if phase == 0 and scenario["id"] == "R01" and fired:
                                if time.monotonic() - activated_at > 7:
                                    phase_data["observation"] = "unresolved_precommit_contained"
                                    break
                            if phase == 0 and scenario["id"] == "R10" and fired:
                                if time.monotonic() - activated_at > 61:
                                    phase_data["observation"] = "rate_limit_containment_window"
                                    break
                            if phase == 0 and scenario["id"] == "R12" and fired:
                                if time.monotonic() - activated_at > 6:
                                    phase_data["observation"] = "invalid_move_containment_window"
                                    break
                            if proc.poll() is not None:
                                phase_data["observation"] = "bridge_exited"
                                break
                            if phase > 0 and time.monotonic() - wait_started > 18:
                                phase_data["observation"] = "bounded_restarted_game"
                                break
                            time.sleep(0.05)
                        else:
                            phase_data["observation"] = "bounded_experiment_timeout"
                    except Exception as exc:
                        state["runner_errors"].append(f"phase{phase+1}: {type(exc).__name__}: {exc}")
                        phase_data["observation"] = "phase_error"
                    finally:
                        if proc is not None:
                            try:
                                shutdown_bridge(proc)
                            except Exception as exc:
                                state["runner_errors"].append("bridge shutdown: "+str(exc))
                            phase_data["bridge_exit"] = proc.poll()
                        if fd is not None:
                            fd.close()
                        if raw_path.is_file() and proc is not None and proc.poll() is not None:
                            try:
                                public_path = output / f"bridge-phase-{phase+1}.log"
                                info = publish_log(
                                    raw_path, public_path, token=bridge_policy.TOKEN,
                                    process_exited=True,
                                )
                                partial_logs.append({"phase": phase+1, "path": public_path.name,
                                                     "capture": info})
                            except Exception as exc:
                                state["runner_errors"].append("log capture: "+str(exc))
                        try:
                            copied = preserve_replays(stage_info["release"],
                                                      output / f"phase-{phase+1}")
                            replay_rows += [{"phase": phase+1, **record} for record in copied]
                        except Exception as exc:
                            state["runner_errors"].append("replay capture: "+str(exc))
                    if phase > 0 or server.finished.is_set():
                        break
                terminate_isolated_children()
                state["journal"] = owner.journal.snapshot()
                state["final_epoch"] = owner.epoch
                audit = owner.directory / "recovery-events.ndjson"
                if audit.is_file():
                    lines_file(output / "guard-events.ndjson",
                               [json.loads(v) for v in audit.read_text().splitlines()])
                else:
                    lines_file(output / "guard-events.ndjson", [])
    except Exception as exc:
        state["runner_errors"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        if started and server is not None:
            try:
                server.close()
            except Exception as exc:
                state["runner_errors"].append("fake server close: " + str(exc))
        game = server.evidence() if server is not None else {}
        state["elapsed_ms"] = round((time.monotonic() - beginning) * 1000)
        state["game_complete"] = bool(game.get("complete"))
        state["logging"] = partial_logs
        state["replay_index"] = replay_rows
        json_file(output / "result.json", state)
        json_file(output / "fault-activation.json",
                  game.get("fault") or {"scenario": scenario, "activations": []})
        json_file(output / "authoritative-game.json", {
            k: game.get(k) for k in ("game_id", "color", "challenge_accepted",
                                    "complete", "moves", "accepted", "outcome",
                                    "forbidden_attempts", "clock")
        })
        trace = game.get("transport_trace", [])
        json_file(output / "stream-sessions.json", game.get("stream_sessions", []))
        lines_file(output / "transport-trace.ndjson", trace)
        lines_file(output / "requests.ndjson", game.get("requests", []))
        lines_file(output / "server-commits.ndjson",
                   [x for x in trace if x.get("event") == "board_commit"])
        if game.get("pgn") is not None:
            (output / "game.pgn").write_text(game["pgn"]+"\n", encoding="utf-8")
        if "journal" not in state:
            state["journal"] = {"error": "snapshot unavailable"}
            json_file(output / "result.json", state)
        print(json.dumps({"scenario": scenario["id"], "color": color,
                          "phases": [p["observation"] for p in state["phases"]],
                          "errors": state["runner_errors"],
                          "activated": len((game.get("fault") or {}).get("activations", [])),
                          "game_complete": state["game_complete"]}, sort_keys=True))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", required=True)
    p.add_argument("--color", choices=("white", "black"), required=True)
    p.add_argument("--bridge", type=Path, required=True)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout", type=float, default=90)
    a = p.parse_args()
    scenario = load_scenarios(CASES)[a.scenario]
    experiment(scenario=scenario, color=a.color, bridge=a.bridge.resolve(),
               release=a.release.resolve(), output=a.output.resolve(),
               source_sha=a.source_sha, timeout=a.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
