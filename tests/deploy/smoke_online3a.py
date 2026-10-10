#!/usr/bin/env python3
"""Real offline UCI integration: independent legality, pipe transport, bounded cleanup."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import chess

BESTMOVE = re.compile(r"^bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000)(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?$")
CLOCK_REQUEST = "go wtime 60000 btime 60000 winc 1000 binc 1000"

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def run(root, stdout_path, stderr_path):
    root = root.resolve()
    cmd = [sys.executable, str(root / "deploy/bin/allfather-online")]
    evidence = {"schema_version": 1, "passed": False, "clock_request": CLOCK_REQUEST}
    transcript, messages = [], queue.Queue()
    proc = reader = None
    try:
        seal = subprocess.run(cmd + ["--check"], cwd=root, text=True, capture_output=True, timeout=35)
        if seal.returncode or "offline seal verified" not in seal.stdout:
            raise RuntimeError("relocated seal rejected: " + seal.stderr)
        with stderr_path.open("w", encoding="utf-8") as error_file:
            proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=error_file,
                                    text=True, bufsize=1, start_new_session=True)
            assert proc.stdin is not None and proc.stdout is not None
            def collect():
                try:
                    for text in proc.stdout:
                        line = text.rstrip("\r\n")
                        transcript.append(line)
                        messages.put(line)
                finally:
                    messages.put(None)
            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            def send(line):
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            def expect(label, predicate, timeout=35):
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("timeout awaiting " + label)
                    try:
                        line = messages.get(timeout=remaining)
                    except queue.Empty as e:
                        raise RuntimeError("timeout awaiting " + label) from e
                    if line is None:
                        raise RuntimeError("unexpected EOF while awaiting " + label)
                    if "Allfather startup failure" in line or "Traceback" in line:
                        raise RuntimeError("controller failure: " + line)
                    if line.startswith("bestmove ") and label != "bestmove":
                        raise RuntimeError("unsolicited terminal bestmove: " + line)
                    if predicate(line):
                        return line
            send("uci")
            expect("uciok", lambda s: s == "uciok")
            send("isready")
            expect("readyok", lambda s: s == "readyok")
            send("ucinewgame")
            send("position startpos")
            start = time.monotonic()
            send(CLOCK_REQUEST)
            line = expect("bestmove", lambda s: s.startswith("bestmove "))
            elapsed = round((time.monotonic() - start) * 1000, 3)
            parsed = BESTMOVE.fullmatch(line)
            if parsed is None or parsed.group(1) == "0000":
                raise RuntimeError("invalid/null bestmove: " + line)
            move = chess.Move.from_uci(parsed.group(1))
            if move not in chess.Board().legal_moves:
                raise RuntimeError("illegal startpos bestmove: " + line)
            send("isready")
            expect("post-search readyok", lambda s: s == "readyok")
            send("quit")
            proc.stdin.close()
            if proc.wait(timeout=25) != 0:
                raise RuntimeError("nonzero controller shutdown")
            reader.join(timeout=3)
            if reader.is_alive():
                raise RuntimeError("stdout reader did not drain")
            for line_type, target in (("uciok", 1), ("readyok", 2)):
                if transcript.count(line_type) != target:
                    raise RuntimeError("incorrect count for " + line_type)
            if sum(s.startswith("bestmove ") for s in transcript) != 1:
                raise RuntimeError("duplicate/missing bestmove")
            evidence.update(bestmove=parsed.group(1), legal_startpos_move=True,
                            go_to_bestmove_ms=elapsed, clean_shutdown=True)
        negative_path = stdout_path.with_name("online3a-negative-stdout.txt")
        with negative_path.open("w+", encoding="utf-8") as regular:
            refused = subprocess.run(cmd, cwd=root, text=True, input="uci\nquit\n",
                                     stdout=regular, stderr=subprocess.PIPE, timeout=35)
            regular.seek(0)
            negative_output = regular.read()
        if refused.returncode != 2 or "POSIX pipe/FIFO" not in negative_output or "uciok" in negative_output:
            raise RuntimeError("regular-file stdout was not rejected")
        evidence["regular_file_stdout_refused"] = True
        evidence["passed"] = True
    except Exception as exc:
        evidence["error"] = str(exc)
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)
        if reader is not None:
            reader.join(timeout=3)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("\n".join(transcript) + ("\n" if transcript else ""), encoding="utf-8")
    return evidence

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--stdout", type=Path, required=True)
    p.add_argument("--stderr", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    stdout, stderr = args.stdout.resolve(), args.stderr.resolve()
    stderr.parent.mkdir(parents=True, exist_ok=True)
    result = run(args.root, stdout, stderr)
    result["manifest_sha256"] = sha256(args.root.resolve() / "build/online-release/release-manifest.json")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
