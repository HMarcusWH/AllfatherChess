#!/usr/bin/env python3
"""Run actual sealed Allfather + prepatched bridge against a local fake API.

Unlike ONLINE-4B's *source* simulator, this uses the bytes already installed
in the combined image, with read-only root, no outside network and durable
SQLite journal across a real supervised process restart.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
import chess

sys.path[:0] = ["/opt/bridge", "/opt/release-tools"]
from deploy.lichess import bridge_policy
from deploy.lichess.log_capture import shutdown_bridge, publish_log
from deploy.lichess.session_owner import SessionOwner
from tests.lichess.fake_lichess_api import FakeLichess
from tests.lichess.fault_plan import load_scenarios

ENGINE = Path("/opt/allfather")
BRIDGE = Path("/opt/bridge")
TOOLS = Path("/opt/release-tools")
PERSIST = Path("/var/lib/allfather")
RUNTIME = Path("/run/allfather")


def stop_all_isolated_processes():
    if os.getpid() != 1:
        raise RuntimeError("offline process cleanup requires the isolated container PID 1")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == 1:
            continue
        try:
            os.kill(int(entry.name), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    until = time.monotonic() + 5
    while time.monotonic() < until:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        live = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == 1:
                continue
            try:
                data = (entry / "stat").read_text()
                if data.split(") ", 1)[1][0] != "Z":
                    live.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not live:
            return
        time.sleep(.05)
    raise RuntimeError("engine or bridge descendants survived container fence")


def _complete(journal, server):
    evidence = server.evidence()
    db = journal.snapshot()
    game = next((g for g in db["games"]
                 if g["game_id"] == evidence.get("game_id")), None)
    return bool(
        evidence.get("complete") and game and game["terminal"] == 1
        and json.loads(game["history_json"]) == evidence["moves"]
        and all(any(i["game_id"] == evidence["game_id"]
                    and i["ply_before"] == move["ply"]-1
                    and i["move"] == move["uci"] and i["status"] == "CONFIRMED"
                    for i in db["intents"])
                for move in evidence["accepted"])
    )


def _start(cfg, owner, logpath):
    env = bridge_policy.sanitized_env()
    env.update(owner.environment())
    env["ALLFATHER_RECOVERY_AUDIT"] = str(owner.directory / "guard-audit.ndjson")
    fd = logpath.open("wb")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(BRIDGE / "lichess-bot.py"), "--config", str(cfg),
             "-v", "--disable_auto_logging"],
            cwd=BRIDGE, env=env, start_new_session=True,
            stdout=fd, stderr=subprocess.STDOUT,
        )
    except BaseException:
        fd.close()
        raise
    return proc, fd


def run(*, color: str, scenario_id: str, run_id: str):
    if os.getpid() != 1:
        raise RuntimeError("image smoke supervisor must be PID 1; no unrestricted signals")
    if os.environ.get("ONLINE_RELEASEx_ISOLATED") != "1":
        raise RuntimeError("release offline network-isolation gate missing")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,50}", run_id):
        raise RuntimeError("unsafe release smoke identifier")
    policy = json.loads((TOOLS / "qualification/release-image-v1.json").read_text())
    if policy["guard_transport"] != "loopback-only":
        raise RuntimeError("release image attempted non-offline API authority")
    scenarios = load_scenarios(TOOLS / "tests/fixtures/lichess/recovery_scenarios.json")
    # Copy the frozen scenario fixture, not a caller-injected fault action.
    if scenario_id not in ("R00", "R02", "R10"):
        raise RuntimeError("unapproved image fault scenario")
    fault = scenarios[scenario_id]
    if color not in fault["colors"]:
        raise RuntimeError("unsupported fixture color")
    game = FakeLichess(color, fault)
    output = PERSIST / run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError("image smoke refuses to reuse a prior game journal")
    output.mkdir(mode=0o700)
    outcome = {"schema_version": 1, "run_id": run_id, "scenario": scenario_id,
               "color": color, "observations": [], "runner_errors": [],
               "offline": True, "network_recovery_qualified": False,
               "public_bot_release": False, "ready": False}
    proc = None
    started = False
    try:
        game.start()
        started = True
        config = RUNTIME / (run_id + ".config.yml")
        bridge_policy.generate(BRIDGE, ENGINE, game.url, config)
        with SessionOwner(output / "state") as owner:
            for phase in range(2 if scenario_id == "R02" else 1):
                if phase:
                    stop_all_isolated_processes()
                    owner.new_epoch()
                subscriptions_before = sum(1 for row in game.sessions.snapshot()
                                            if row["kind"] == "game" and row["event"] == "subscribe")
                proc = None
                fd = None
                try:
                    proc, fd = _start(config, owner, output / f"phase{phase+1}.private")
                    if phase == 0:
                        if not game.event_stream_open.wait(30):
                            raise RuntimeError("missing bridge control stream")
                        game.issue_challenge()
                        if not game.game_stream_open.wait(30):
                            raise RuntimeError("missing accepted game stream")
                        if color == "black":
                            game.opponent_move()
                    else:
                        limit = time.monotonic() + 35
                        while time.monotonic() < limit:
                            count = sum(1 for row in game.sessions.snapshot()
                                        if row["kind"] == "game" and row["event"] == "subscribe")
                            if count > subscriptions_before:
                                break
                            if proc.poll() is not None:
                                raise RuntimeError("bridge exited before reopening game stream")
                            time.sleep(.05)
                        else:
                            raise RuntimeError("restarted bridge did not reopen authoritative stream")
                    began = time.monotonic()
                    activated = None
                    until = began + (100 if scenario_id == "R10" else 90)
                    observation = "timeout"
                    while time.monotonic() < until:
                        if game.fault.evidence()["activations"] and activated is None:
                            activated = time.monotonic()
                        if game.finished.is_set():
                            confirmation_deadline = time.monotonic() + 7
                            while time.monotonic() < confirmation_deadline and not _complete(owner.journal, game):
                                time.sleep(.05)
                            observation = ("complete_confirmed" if _complete(owner.journal, game)
                                           else "incomplete_terminal_reconciliation")
                            break
                        if phase == 0 and scenario_id == "R02" and activated is not None:
                            if time.monotonic()-activated > 1.2:
                                observation = "restart_after_lost_ack"
                                break
                        if scenario_id == "R10" and activated is not None:
                            if time.monotonic()-activated > 61:
                                observation = "rate_limited_suspension"
                                break
                        if proc.poll() is not None:
                            observation = "bridge_exited"
                            break
                        time.sleep(.05)
                    outcome["observations"].append({
                        "phase": phase + 1, "epoch": owner.epoch,
                        "observation": observation})
                finally:
                    if proc is not None:
                        shutdown_bridge(proc)
                    if fd is not None:
                        fd.close()
                    if proc is not None and proc.poll() is not None:
                        raw = output / f"phase{phase+1}.private"
                        publish_log(raw, output / f"phase{phase+1}.log",
                                    token=bridge_policy.TOKEN, process_exited=True)
                        raw.unlink()
                if game.finished.is_set():
                    break
            stop_all_isolated_processes()
            outcome["journal"] = owner.journal.snapshot()
            audit_path = output / "state" / "guard-audit.ndjson"
            if audit_path.is_file():
                outcome["guard_events"] = [json.loads(line) for line in
                                           audit_path.read_text().splitlines()]
            else:
                outcome["guard_events"] = []
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        outcome["runner_errors"].append(type(exc).__name__ + ": " + str(exc))
    except BaseException as exc:
        # Retain negative evidence, but never silently turn an unexpected
        # test-supervisor failure into a successful qualification result.
        outcome["runner_errors"].append(type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        if started:
            game.close()
        evidence = game.evidence()
        outcome["game"] = {
            k: evidence.get(k) for k in
            ("game_id", "color", "moves", "accepted", "complete", "outcome", "forbidden_attempts")
        }
        outcome["fault"] = evidence.get("fault")
        outcome["http"] = evidence.get("requests")
        outcome["commits"] = [r for r in evidence.get("transport_trace", [])
                              if r.get("event") == "board_commit"]
        board = chess.Board()
        for move in outcome["game"].get("moves") or []:
            if chess.Move.from_uci(move) not in board.legal_moves:
                outcome["runner_errors"].append("authoritative move illegally replayed")
                break
            board.push_uci(move)
        journal = outcome.get("journal") or {}
        last_epoch = outcome["observations"][-1]["epoch"] if outcome["observations"] else None
        second_epoch_commits = [
            i for i in journal.get("intents", [])
            if i.get("epoch") == last_epoch and i.get("status") == "CONFIRMED"
            and any(c.get("origin") == "allfather" and
                    c.get("game_id") == i.get("game_id") and
                    c.get("ply_before") == i.get("ply_before") and
                    c.get("uci") == i.get("move") for c in outcome["commits"])
        ]
        records = outcome["http"] or []
        cooldowns = journal.get("cooldown") or []
        rate = [i for i, r in enumerate(records) if r.get("status") == 429]
        rate_clean = False
        if len(rate) == 1 and cooldowns:
            previous = records[rate[0]]["monotonic_ms"]
            rate_clean = not any(
                0 <= r["monotonic_ms"] - previous < 60000
                for r in records[rate[0]+1:]
            )
        if scenario_id == "R10":
            confirmed = (rate_clean and
                         outcome["observations"][-1]["observation"] ==
                         "rate_limited_suspension" and not evidence.get("complete"))
        elif scenario_id == "R02":
            confirmed = (evidence.get("complete") and
                         len(outcome["observations"]) == 2 and
                         outcome["observations"][-1]["observation"] == "complete_confirmed"
                         and bool(second_epoch_commits))
        else:
            confirmed = (evidence.get("complete") and
                         outcome["observations"][-1]["observation"] == "complete_confirmed")
        allowed_posts = {"/api/token/test",
                         "/api/challenge/off3b001/accept",
                         "/api/challenge/off3b001/decline"}
        prefix = "/api/bot/game/off3b001/move/"
        bad_http = [r for r in records if
                    r.get("method") == "POST" and
                    (r.get("status") == 403 or
                     not (r.get("path") in allowed_posts or
                          (isinstance(r.get("path"), str) and
                           r["path"].startswith(prefix) and
                           r["path"][len(prefix):] not in ("", "0000"))))]
        if not isinstance(outcome["game"].get("forbidden_attempts"), list) or outcome["game"]["forbidden_attempts"]:
            outcome["runner_errors"].append("server recorded forbidden HTTP attempts")
        outcome["rate_cooldown_verified"] = rate_clean if scenario_id == "R10" else None
        outcome["fresh_second_epoch_commits"] = len(second_epoch_commits) if scenario_id == "R02" else None
        outcome["ready"] = bool(confirmed and not outcome["runner_errors"] and
                                not bad_http and (scenario_id == "R00" or
                                len(outcome["fault"]["activations"]) == 1))
        (output / "result.json").write_text(json.dumps(outcome, sort_keys=True,
                                                       indent=2, allow_nan=False)+"\n")
        print(json.dumps({"status": "OFFLINE_SMOKE_PASS" if outcome["ready"] else "OFFLINE_SMOKE_FAIL",
                          "run_id": run_id, "scenario": scenario_id, "color": color,
                          "errors": outcome["runner_errors"],
                          "observations": outcome["observations"],
                          "public_bot_release": False}, sort_keys=True))
    # NEVER return from finally: doing so suppresses an active exception.
    return 0 if outcome["ready"] else 2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--color", choices=("white", "black"), required=True)
    p.add_argument("--scenario", choices=("R00", "R02", "R10"), required=True)
    p.add_argument("--run-id", required=True)
    args = p.parse_args()
    return run(color=args.color, scenario_id=args.scenario, run_id=args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
