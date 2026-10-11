"""Bounded, synchronous diagnostic capture for the offline bridge process.

Only the bridge's *diagnostic output* uses a regular file. The real Allfather
UCI stdout remains a POSIX pipe owned by python-chess.
"""
from __future__ import annotations
import hashlib
import os
import signal
import subprocess
from pathlib import Path

MAX_LOG_BYTES = 16 * 1024 * 1024


def shutdown_bridge(proc: subprocess.Popen, *, graceful_s: float = 15,
                    forced_s: float = 10) -> int:
    """Never return until the supervised bridge parent has exited."""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=graceful_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=forced_s)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("bridge parent failed bounded shutdown") from exc
    if proc.poll() is None:
        raise RuntimeError("bridge parent remains alive")
    return proc.returncode


def publish_log(raw_path: Path, output_path: Path, *,
                token: str, process_exited: bool) -> dict:
    """Sanitize a closed diagnostic file after the bridge parent exits."""
    if not process_exited:
        raise RuntimeError("cannot certify diagnostics before bridge exit")
    size = raw_path.stat().st_size
    if size == 0 or size > MAX_LOG_BYTES:
        raise RuntimeError("bridge diagnostic log empty or exceeds bounded size")
    content = raw_path.read_bytes().decode("utf-8", errors="replace")
    cleaned = content.replace(token, "[REDACTED]")
    if token in cleaned:
        raise RuntimeError("unredacted synthetic credential in diagnostics")
    output = cleaned.encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    return {
        "capture_complete": True,
        "bridge_process_exited": True,
        "raw_bytes": size,
        "sanitized_bytes": len(output),
        "sanitized_sha256": hashlib.sha256(output).hexdigest(),
        "log_capture_method": "direct_process_file_after_wait",
    }
