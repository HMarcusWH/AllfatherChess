#!/usr/bin/env python3
"""Retrieve only the locked lichess-bot Git revision; no submodules or live API."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "deploy/lichess/bridge.lock.json"
DEFAULT = ROOT / "build/vendor/lichess-bot"


def cmd(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, stderr=subprocess.PIPE, text=True).strip()


def verify(location: Path, lock: dict) -> None:
    if not (location / ".git").exists():
        raise ValueError("pinned bridge Git checkout missing")
    if cmd("git", "rev-parse", "HEAD", cwd=location) != lock["commit"]:
        raise ValueError("bridge HEAD differs from locked commit")
    if cmd("git", "status", "--porcelain", "--untracked-files=no", cwd=location):
        raise ValueError("pinned bridge checkout has modified tracked files")
    if cmd("git", "remote", "get-url", "origin", cwd=location) != lock["origin"]:
        raise ValueError("bridge remote identity differs from lock")
    for path, oid in lock["tracked_blobs"].items():
        if not (location / path).is_file() or (location / path).is_symlink():
            raise ValueError("missing or linked bridge path: " + path)
        if cmd("git", "hash-object", path, cwd=location) != oid:
            raise ValueError("bridge tracked blob mismatch: " + path)
    modes = cmd("git", "ls-files", "-s", cwd=location).splitlines()
    if any(line.startswith("120000 ") or line.startswith("160000 ") for line in modes):
        raise ValueError("pinned bridge has symlinks or Git submodules")
    if lock["scope"] != "ONLINE-3B-OFFLINE-ONLY" or lock["live_api_permitted"] is not False:
        raise ValueError("bridge scope is not offline-only")


def fetch(location: Path, lock: dict) -> None:
    if location.exists():
        verify(location, lock)
        return
    location.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(location)], check=True)
    try:
        subprocess.run(["git", "-C", str(location), "remote", "add", "origin", lock["origin"]], check=True)
        subprocess.run(["git", "-C", str(location), "fetch", "--quiet", "--depth", "1",
                        "origin", lock["commit"]], check=True, timeout=180)
        subprocess.run(["git", "-C", str(location), "checkout", "--quiet", "--detach",
                        "FETCH_HEAD"], check=True)
        verify(location, lock)
    except Exception:
        import shutil
        shutil.rmtree(location, ignore_errors=True)
        raise


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--checkout", type=Path, default=DEFAULT)
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args()
    lock = json.loads(LOCK.read_text())
    location = args.checkout.resolve()
    if not location.is_relative_to((ROOT / "build").resolve()):
        raise SystemExit("bridge checkout must be under build/")
    try:
        if args.verify_only:
            verify(location, lock)
        else:
            fetch(location, lock)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print("ONLINE-3B bridge source refused: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"scope": lock["scope"], "commit": lock["commit"], "verified": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
