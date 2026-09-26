"""Transparent, campaign-owned UCI recorder. Does not invent/replace moves or clocks.

The child has a private process group. Evidence is queued before forwarding, not
written to disk on the response path. Loss, timeout and forced cleanup are failures.
"""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import queue
import resource
import select
import signal
import subprocess
import sys
import threading
import time
import uuid
import json

from .common import ROOT, cpu_delta, file_record, load, process_group, require, runtime_config, save


def run(spec: dict) -> int:
    root = Path(spec["root"]).resolve()
    sessions = Path(spec["sessions"]).resolve()
    require(sessions.is_relative_to(root / "build"), "sessions must be inside build/")
    session = sessions / (f"{time.time_ns()}-{uuid.uuid4().hex}")
    session.mkdir(parents=True, exist_ok=False)
    source = load(root / "config/allfather.online-hybrid.validation.json")
    arm = spec["arm"]
    if arm.startswith("allfather-"):
        config = runtime_config(source, arm, root, session / "replays")
        save(session / "runtime.json", config)
        command = [sys.executable, "-m", "controller", "--config", str(session / "runtime.json")]
    else:
        name = {"stockfish": "stockfish-anchor", "reckless": "reckless-shadow", "lc0": "lc0-shadow"}[arm]
        command = [str(root / source["instances"][name]["binary"])]
    environment = os.environ.copy()
    environment.update(spec.get("environment", {}))
    # Subreaper permits deterministic accounting/reaping of accidental orphan descendants.
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(36, 1, 0, 0, 0) == 0, "cannot enable Linux child subreaper")
    errors: list[str] = []
    lock = threading.RLock()
    q: queue.Queue = queue.Queue(maxsize=100_000)
    seq = game = search = 0
    pending: dict | None = None
    metrics: list[dict] = []
    started_ns = time.monotonic_ns()
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    own_before = time.process_time_ns()
    process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                               bufsize=0)
    pgid = process.pid
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(sys.stdout.fileno(), False)
    save(session / "session.json", {"schema_version": 1, "status": "running", "arm": arm,
        "session_id": session.name, "pid": process.pid, "pgid": pgid,
        "started_ns": started_ns, "command": command, "spec": spec})

    def pipe_write(fd: int, raw: bytes, timeout: float = 3.0) -> None:
        view = memoryview(raw)
        deadline = time.monotonic() + timeout
        while view:
            try:
                count = os.write(fd, view)
                if count <= 0:
                    raise BrokenPipeError("pipe write made no progress")
                view = view[count:]
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                    raise TimeoutError("qualification proxy pipe stalled")

    def child_write(raw: bytes) -> None:
        pipe_write(process.stdin.fileno(), raw)

    def record(direction: str, line: str, **extra) -> None:
        nonlocal seq
        with lock:
            seq += 1
            event = {"seq": seq, "ns": time.monotonic_ns(), "direction": direction,
                     "line": line, "game": game, "search": search, **extra}
            try:
                q.put_nowait(event)
            except queue.Full:
                if "trace overflow" not in errors:
                    errors.append("trace overflow")

    def logger() -> None:
        try:
            with (session / "uci.jsonl").open("x", encoding="utf-8") as output:
                while True:
                    event = q.get()
                    if event is None:
                        break
                    output.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
        except Exception as exc:
            errors.append(f"trace write failed: {type(exc).__name__}: {exc}")

    log_thread = threading.Thread(target=logger, daemon=True)
    log_thread.start()

    def reader(stream, direction: str) -> None:
        nonlocal pending
        try:
            while True:
                raw = stream.readline(1_048_577)
                if not raw:
                    return
                if len(raw) > 1_048_576 or not raw.endswith(b"\n"):
                    errors.append(f"oversized or unterminated {direction} line")
                line = raw.rstrip(b"\r\n").decode("utf-8", errors="strict")
                with lock:
                    if direction == "out" and line.startswith("bestmove"):
                        received_ns = time.monotonic_ns()
                        after = process_group(pgid)
                        if pending is None:
                            errors.append("unsolicited/duplicate bestmove")
                        else:
                            metrics.append({**pending, "line": line,
                                "received_ns": received_ns,
                                "cpu_ms_observed": cpu_delta(pending["before"], after),
                                "after": after})
                            pending = None
                    record(direction, line)
                    if direction == "out":
                        # Real pipe to the child and to Fastchess; no PTY/stdout logfile.
                        pipe_write(sys.stdout.fileno(), raw)
        except Exception as exc:
            errors.append(f"{direction} reader: {type(exc).__name__}: {exc}")

    readers = [threading.Thread(target=reader, args=(stream, direction), daemon=True)
               for stream, direction in ((process.stdout, "out"), (process.stderr, "err"))]
    for thread in readers:
        thread.start()
    interrupted = False
    quit_seen = False

    def interrupted_handler(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted_handler)
    try:
        buffer = b""
        while process.poll() is None and not quit_seen:
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.1)
            if not ready:
                continue
            data = os.read(sys.stdin.fileno(), 65536)
            if not data:
                errors.append("runner stdin closed without quit")
                break
            buffer += data
            require(len(buffer) <= 1_048_576, "oversized command")
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.rstrip(b"\r").decode("utf-8", errors="strict")
                with lock:
                    if line == "ucinewgame":
                        if pending is not None:
                            errors.append("ucinewgame while search pending")
                        game += 1
                    if line == "go" or line.startswith("go "):
                        if pending is not None:
                            errors.append("overlapping go")
                        search += 1
                        pending = {"search": search, "game": game,
                                   "sent_ns": time.monotonic_ns(), "before": process_group(pgid)}
                    record("in", line)
                    child_write(raw + b"\n")
                    if line == "quit":
                        quit_seen = True
                if quit_seen:
                    break
        if not quit_seen:
            errors.append("child exited or runner disconnected before normal quit")
    except (KeyboardInterrupt, Exception) as exc:
        interrupted = True
        errors.append(f"proxy interrupted: {type(exc).__name__}: {exc}")
    finally:
        # Never let cancellation interrupt the cleanup/reaping block a second time.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if process.poll() is None and not quit_seen:
                child_write(b"quit\n")
            process.wait(timeout=20)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            errors.append("child did not shut down normally")
        leaked = process_group(pgid)
        if leaked:
            errors.append("live child group after shutdown grace")
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            errors.append("child did not exit after SIGKILL")
        for thread in readers:
            thread.join(timeout=5)
            if thread.is_alive():
                errors.append("reader failed to close")
        if pending is not None:
            errors.append("go has no terminal bestmove")
        until = time.monotonic() + 5
        while time.monotonic() < until:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    time.sleep(.01)
                else:
                    continue
            except ChildProcessError:
                break
        try:
            q.put(None, timeout=5)
        except queue.Full:
            errors.append("trace did not drain")
        log_thread.join(timeout=10)
        if log_thread.is_alive():
            errors.append("trace writer did not finish")
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        report = {"schema_version": 1, "status": "failed" if errors else "completed",
            "arm": arm, "session_id": session.name, "started_ns": started_ns,
            "ended_ns": time.monotonic_ns(), "pid": process.pid, "pgid": pgid,
            "returncode": process.returncode, "errors": errors, "leaked_before_cleanup": leaked,
            "remaining_after_cleanup": process_group(pgid), "command": command, "spec": spec,
            "search_metrics": metrics, "event_count": seq,
            "resources": {
                "reaped_subtree_cpu_ms": ((usage.ru_utime + usage.ru_stime) -
                    (children_before.ru_utime + children_before.ru_stime)) * 1000,
                "proxy_cpu_ms": (time.process_time_ns() - own_before) / 1e6,
                "scope": "session, including startup/cleanup and reaped descendants; not per-game CPU",
                "cpu_complete": not leaked and process.returncode == 0 and not interrupted,
                "memory_scope": "RSS endpoint observations, not an exact group peak"}}
        if (session / "uci.jsonl").is_file():
            report["transcript"] = file_record(session / "uci.jsonl", session)
        if (session / "runtime.json").is_file():
            report["runtime"] = file_record(session / "runtime.json", session)
        save(session / "session.json", report)
    return 0 if not errors and process.returncode == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    return run(load(args.spec))


if __name__ == "__main__":
    raise SystemExit(main())
