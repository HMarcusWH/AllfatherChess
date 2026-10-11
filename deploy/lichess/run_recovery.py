#!/usr/bin/env python3
"""Run one ONLINE-4A fault experiment against the real pinned bridge and Allfather.

Diagnostic, OFFLINE ONLY. Preserve truthful partial evidence even when the
bridge exits, a stream breaks, or the game never terminates naturally.
"""
from __future__ import annotations

import argparse
import hashlib
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
sys.path.insert(0, str(ROOT))

from deploy.lichess import bridge_policy
from deploy.lichess.log_capture import publish_log, shutdown_bridge
from deploy.lichess.offline_session import bridge_command, stage
from tests.lichess.fault_plan import load_scenarios
from tests.lichess.fake_lichess_api import FakeLichess

CASES = ROOT / "tests/fixtures/lichess/recovery_scenarios.json"
SOURCE_PATHS = (
    "deploy/lichess/bridge.lock.json",
    "deploy/lichess/bridge_policy.py",
    "deploy/lichess/log_capture.py",
    "deploy/lichess/offline_session.py",
    "deploy/lichess/run_recovery.py",
    "tests/lichess/fake_lichess_api.py",
    "tests/lichess/fault_plan.py",
    "tests/lichess/transport_trace.py",
    "tests/lichess/stream_sessions.py",
    "tests/fixtures/lichess/recovery_scenarios.json",
    "qualification/online-recovery-v1.json",
    "scripts/qualify-online-recovery.py",
    ".github/workflows/online-recovery.yml",
    "tests/lichess/test_fault_plan.py",
    "tests/lichess/test_stream_sessions.py",
    "tests/lichess/test_recovery_http.py",
    "tests/lichess/test_recovery_evidence.py",
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fd:
        for chunk in iter(lambda: fd.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def lines_file(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                            for row in rows), encoding="utf-8")


def preserve_replays(release: Path, output: Path) -> list[dict]:
    """Keep bounded manifests, not huge engine telemetry, in diagnostic CI."""
    index = []
    source = release / "build/replays-online-hybrid-v2"
    if not source.is_dir():
        return index
    for manifest in sorted(source.glob("*/manifest.json"))[:128]:
        if manifest.stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError("oversized replay manifest refused")
        rid = manifest.parent.name
        if not rid or "/" in rid or rid in (".", ".."):
            raise RuntimeError("unsafe replay run id")
        dest = output / "replays" / rid / "manifest.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest, dest)
        doc = json.loads(dest.read_text(encoding="utf-8"))
        row = {"run_id": rid, "manifest": f"replays/{rid}/manifest.json",
               "sha256": digest(dest), "generation": doc.get("generation"),
               "position_id": (doc.get("position") or {}).get("position_id"),
               "outward_decision": doc.get("outward_decision")}
        route = manifest.parent / "route.json"
        if route.is_file() and route.stat().st_size <= 2 * 1024 * 1024:
            target = output / "replays" / rid / "route.json"
            shutil.copyfile(route, target)
            row["route_sha256"] = digest(target)
        index.append(row)
    return index


def experiment(*, scenario: dict, color: str, bridge: Path, release: Path,
               output: Path, source_sha: str, timeout: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    server = None
    started = False
    proc = None
    errors = []
    experiment_start = time.monotonic()
    state = {"scenario_id": scenario["id"], "color": color,
             "source_commit": source_sha, "bridge_commit": None,
             "complete": False, "bridge_exit": None,
             "observation": "not_started", "runner_errors": errors,
             "network_recovery_qualified": False, "public_bot_release": False}
    log_info = None
    replay_index = []
    json_file(output / "fault-plan.json", scenario)
    json_file(output / "source-manifest.json", {
        "source_commit": source_sha,
        "files": {p: digest(ROOT / p) for p in SOURCE_PATHS},
    })
    try:
        if os.getenv("ONLINE4A_ISOLATED_CONTAINER") != "1":
            raise RuntimeError("ONLINE-4A requires --network none isolated container")
        if scenario["mode"] != "integration" or color not in scenario["colors"]:
            raise RuntimeError("scenario is not an authorized real-bridge experiment")
        server = FakeLichess(color, scenario)
        with tempfile.TemporaryDirectory(prefix="online4a-") as dirname:
            work = Path(dirname)
            raw_log = work / "bridge.private.raw"
            prepared = None
            try:
                prepared = stage(release=release, bridge=bridge, workspace=work,
                                 server_url=server.url)
                state["bridge_commit"] = prepared["bridge_commit"]
                if prepared["release_source"] != source_sha:
                    raise RuntimeError("released engine source differs from exact job commit")
                server.start()
                started = True
                env = bridge_policy.sanitized_env()
                env["ONLINE4A_ISOLATED_CONTAINER"] = "1"
                env["ONLINE3B_ISOLATED_CONTAINER"] = "1"
                with raw_log.open("wb") as fd:
                    try:
                        proc = subprocess.Popen(
                            bridge_command(prepared), cwd=prepared["bridge"],
                            env=env, stdout=fd, stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        if not server.event_stream_open.wait(timeout=35):
                            raise RuntimeError("bridge never opened global event stream")
                        server.issue_challenge()
                        if not server.game_stream_open.wait(timeout=35):
                            raise RuntimeError("bridge never accepted challenge and opened game stream")
                        if color == "black":
                            server.opponent_move()
                        state["observation"] = "game_stream_started"
                        end = time.monotonic() + timeout
                        activated_at = None
                        while time.monotonic() < end:
                            if server.finished.is_set():
                                state["observation"] = "server_game_complete"
                                time.sleep(0.8)
                                break
                            if proc.poll() is not None:
                                state["observation"] = "bridge_exited"
                                break
                            activation = server.fault.evidence()["activations"]
                            if activation and activated_at is None:
                                activated_at = time.monotonic()
                            if (scenario["action"] == "terminate_bridge"
                                    and activation and proc.poll() is None):
                                os.killpg(proc.pid, signal.SIGKILL)
                                state["observation"] = "fault_triggered_bridge_kill"
                                break
                            if activated_at is not None and scenario["action"] != "none":
                                hold = 61 if scenario["action"] == "rate_limit_429" else 12
                                if time.monotonic() - activated_at >= hold:
                                    state["observation"] = "bounded_postfault_observation"
                                    state["postfault_observed_seconds"] = round(hold, 3)
                                    break
                            time.sleep(0.05)
                        else:
                            state["observation"] = "experiment_timeout"
                    finally:
                        if proc is not None:
                            try:
                                shutdown_bridge(proc)
                            except Exception as exc:
                                errors.append("bridge shutdown: " + str(exc))
                            state["bridge_exit"] = proc.poll()
            finally:
                if started:
                    try:
                        server.close()
                        started = False
                    except Exception as exc:
                        errors.append("server close: " + str(exc))
                if prepared is not None:
                    try:
                        replay_index = preserve_replays(prepared["release"], output)
                    except Exception as exc:
                        errors.append("replay evidence: " + str(exc))
                if raw_log.is_file() and proc is not None and proc.poll() is not None:
                    try:
                        log_info = publish_log(
                            raw_log, output / "bridge.log", token=bridge_policy.TOKEN,
                            process_exited=True,
                        )
                    except Exception as exc:
                        errors.append("diagnostic log: " + str(exc))
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
    finally:
        if server is not None:
            if started:
                try:
                    server.close()
                except Exception as exc:
                    errors.append("server close after error: " + str(exc))
            evidence = server.evidence()
        else:
            evidence = {}
        state["complete"] = bool(evidence.get("complete"))
        state["elapsed_ms"] = round((time.monotonic() - experiment_start) * 1000)
        state["logging"] = log_info
        state["authoritative_game"] = {
            k: evidence.get(k) for k in ("game_id", "color", "challenge_accepted",
                                        "moves", "accepted", "clock", "outcome", "complete",
                                        "forbidden_attempts")
        }
        json_file(output / "result.json", state)
        json_file(output / "fault-activation.json",
                  evidence.get("fault") or {"scenario": scenario, "activations": [],
                                           "endpoint_counts": {}})
        json_file(output / "authoritative-game.json", state["authoritative_game"])
        json_file(output / "stream-sessions.json", evidence.get("stream_sessions", []))
        json_file(output / "replay-index.json", replay_index)
        trace = evidence.get("transport_trace", [])
        lines_file(output / "transport-trace.ndjson", trace)
        lines_file(output / "http-attempts.ndjson",
                   [x for x in trace if x["event"] == "move_attempt"])
        lines_file(output / "server-commits.ndjson",
                   [x for x in trace if x["event"] == "board_commit"])
        lines_file(output / "delivered-events.ndjson",
                   [x for x in trace if x["event"] in ("stream_emit", "stream_partial_chunk")])
        if evidence.get("pgn") is not None:
            (output / "game.pgn").write_text(evidence["pgn"] + "\n", encoding="utf-8")
        # All observational failures remain visible. The *independent*
        # verifier decides whether the harness is qualified.
        print(json.dumps({"scenario": scenario["id"], "color": color,
                          "observation": state["observation"],
                          "activations": len((evidence.get("fault") or {}).get("activations", [])),
                          "errors": errors}, sort_keys=True))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", required=True)
    p.add_argument("--color", choices=("white", "black"), required=True)
    p.add_argument("--bridge", type=Path, required=True)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout", type=float, default=90)
    args = p.parse_args()
    rows = load_scenarios(CASES)
    if args.scenario not in rows:
        p.error("unknown frozen scenario")
    experiment(scenario=rows[args.scenario], color=args.color,
               bridge=args.bridge.resolve(), release=args.release.resolve(),
               output=args.output.resolve(), source_sha=args.source_sha,
               timeout=args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
