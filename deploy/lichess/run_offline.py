#!/usr/bin/env python3
"""Supervise one pinned upstream lichess-bot + sealed Allfather game on localhost.

Must run inside a --network none, --pids-limit isolated container. Exiting PID 1
is the process-tree kill boundary because upstream UCI starts a separate pgrp.
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
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.lichess import bridge_policy, fetch_bridge
from tests.lichess.fake_lichess_api import FakeLichess


def game(*, color: str, release: Path, bridge: Path, output: Path, timeout: int = 540) -> dict:
    if os.getenv("ONLINE3B_ISOLATED_CONTAINER") != "1":
        raise RuntimeError("full game requires enforced isolated --network none container")
    output.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / "deploy/lichess/bridge.lock.json").read_text())
    fetch_bridge.verify(bridge, lock)
    seal = subprocess.run(
        [sys.executable, str(release / "deploy/bin/allfather-online"), "--check"],
        cwd=release, capture_output=True, text=True, timeout=40
    )
    if seal.returncode or "offline seal verified" not in seal.stdout:
        raise RuntimeError("Allfather release seal check failed: " + seal.stderr[:1000])
    server = FakeLichess(color)
    server.start()
    proc = None
    log_thread = None
    with tempfile.TemporaryDirectory(prefix="online3b-") as dirname:
        work = Path(dirname)
        # The source artifact is read-only in the container. Each individual
        # game gets a writable relocated *copy* so replay writes remain inside
        # this game's lifetime and never mutate the original release.
        copied_release = work / "release"
        shutil.copytree(release, copied_release)
        release = copied_release
        staged = work / "bridge"
        shutil.copytree(bridge, staged, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        shutil.copy2(ROOT / "deploy/lichess/extra_game_handlers.py",
                     staged / "extra_game_handlers.py")
        cfg = bridge_policy.generate(staged, release, server.url, work / "config.yml")
        if cfg["url"] != server.url:
            raise RuntimeError("bridge config does not match the simulated API")
        env = bridge_policy.sanitized_env()
        env["ONLINE3B_ISOLATED_CONTAINER"] = "1"
        started = time.monotonic()
        log_path = output / "bridge.log"
        try:
            with log_path.open("w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [sys.executable, str(staged / "lichess-bot.py"), "--config",
                     str(work / "config.yml"), "-v", "--disable_auto_logging"],
                    cwd=staged, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    start_new_session=True
                )
                def reader():
                    assert proc is not None and proc.stdout is not None
                    for line in proc.stdout:
                        log.write(line.replace(bridge_policy.TOKEN, "[REDACTED]"))
                        log.flush()
                log_thread = threading.Thread(target=reader, daemon=True)
                log_thread.start()
                if not server.event_stream_open.wait(timeout=90):
                    raise RuntimeError("bridge never opened Lichess event stream")
                server.issue_challenge()
                if not server.game_stream_open.wait(timeout=90):
                    raise RuntimeError("bridge did not accept challenge and open game stream")
                if color == "black":
                    server.opponent_move()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and not server.finished.is_set():
                    if proc.poll() is not None:
                        raise RuntimeError("bridge exited before natural game end")
                    time.sleep(0.1)
                if not server.finished.is_set():
                    raise RuntimeError("no naturally completed game before timeout")
                # Give the bridge enough time to consume the terminal gameState.
                time.sleep(0.8)
        finally:
            # Keep HTTP streams up while the bridge is asked to shutdown;
            # otherwise a premature 500/EOF can activate upstream restart.
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)
            server.close()
            if log_thread is not None:
                log_thread.join(timeout=4)
        evidence = server.evidence()
        evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        evidence["bridge_exit"] = proc.returncode if proc else None
        evidence["source_commit"] = lock["commit"]
        evidence["allfather_source_commit"] = json.loads(
            (release / "build/online-release/release-manifest.json").read_text()
        )["source_commit"]
        evidence["bridge_config_url"] = "loopback"
        evidence["container_process_cleanup_boundary"] = "pid1-exit"
        (output / "result.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        (output / "game.pgn").write_text(evidence["pgn"] + "\n")
        (output / "requests.ndjson").write_text(
            "".join(json.dumps(e, sort_keys=True) + "\n" for e in evidence["requests"])
        )
        if not evidence["complete"] or not evidence["accepted"]:
            raise RuntimeError("no independently recorded completed Allfather game")
        return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--color", choices=("white", "black"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=540)
    args = parser.parse_args()
    try:
        result = game(color=args.color, release=args.release.resolve(),
                      bridge=args.bridge.resolve(), output=args.output.resolve(),
                      timeout=args.timeout)
        print(json.dumps({"color": result["color"], "complete": result["complete"],
                          "outcome": result["outcome"], "moves": len(result["moves"])}))
    except Exception as exc:
        print("ONLINE-3B game refused: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
