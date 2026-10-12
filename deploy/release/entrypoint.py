#!/usr/bin/env python3
"""RELEASE-1 offline inspection gateway. No live bot entrypoint is implemented."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ENGINE = Path("/opt/allfather")
TOOLS = Path("/opt/release-tools")
RELEASE = Path("/opt/allfather-release")
STATE = Path("/var/lib/allfather")
RUN = Path("/run/allfather")
TMP = Path("/tmp")
REPLAY = ENGINE / "build/replays-online-hybrid-v2"


def _die(reason: str):
    raise RuntimeError("RELEASE-1 offline entrypoint refused: " + reason)


def _verify_configuration():
    if os.geteuid() != 10001 or os.getegid() != 10001:
        _die("runtime must use UID/GID 10001")
    if os.environ.get("LICHESS_BOT_TOKEN"):
        _die("real bot credential is never permitted in RELEASE-1")
    if os.environ.get("ONLINE4B_ISOLATED_CONTAINER") != "1" and os.environ.get("ALLFATHER_RELEASE_MODE") == "offline-smoke":
        _die("offline simulator runtime gate absent")
    verifier = TOOLS / "scripts/verify-release-candidate.py"
    result = subprocess.run([sys.executable, str(verifier), "--installed"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=55, cwd=ENGINE)
    if result.returncode:
        _die("source/image byte verification failed: " + result.stderr[:500])
    record = json.loads(result.stdout)
    if record.get("public_bot_release") is not False:
        _die("unapproved release authority")
    os_packages = RELEASE / "os-packages.txt"
    python_packages = RELEASE / "python-packages.txt"
    if not os_packages.is_file() or not python_packages.is_file():
        _die("runtime dependency inventory missing")
    packages = os_packages.read_text(encoding="utf-8").splitlines()
    if not all(any(line.startswith(name + "=") or line.startswith(name + ":amd64=")
                   for line in packages)
               for name in ("python3", "libopenblas0-pthread", "libprotobuf32t64")):
        _die("native runtime package inventory incomplete")
    return record


def _mounts_ready():
    """Ensure the persistent DB cannot silently land on ephemeral/root FS."""
    root = os.stat("/")
    persistent = os.stat(STATE)
    volatile = os.stat(RUN)
    scratch = os.stat(TMP)
    replay = os.stat(REPLAY)
    if (root.st_dev == persistent.st_dev or root.st_dev == volatile.st_dev
        or root.st_dev == scratch.st_dev or root.st_dev == replay.st_dev):
        _die("distinct persistent/tmpfs mount contract not satisfied")
    for where in (STATE, RUN, TMP, REPLAY):
        if where.is_symlink() or not where.is_dir() or not os.access(where, os.W_OK | os.X_OK):
            _die("runtime volume absent, linked or unwritable: " + str(where))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "offline-smoke"), nargs="?", default="verify")
    parser.add_argument("--color", choices=("white", "black"), default="white")
    parser.add_argument("--scenario", choices=("R00", "R02", "R10"), default="R00")
    parser.add_argument("--run-id", default="release1-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            record = _verify_configuration()
            print(json.dumps({"status": "RELEASE-1-VERIFIED-OFFLINE-ONLY",
                              "source_commit": record["source_commit"],
                              "files": record["verified_files"],
                              "public_bot_release": False}, sort_keys=True))
            return 0
        os.environ["ALLFATHER_RELEASE_MODE"] = "offline-smoke"
        if os.environ.get("ONLINE_RELEASEx_ISOLATED") != "1":
            _die("no offline-only network-isolation acknowledgement")
        _mounts_ready()
        _verify_configuration()
        os.chdir(ENGINE)
        os.execv(sys.executable, [
            sys.executable, str(TOOLS / "deploy/release/offline_smoke.py"),
            "--color", args.color, "--scenario", args.scenario, "--run-id", args.run_id,
        ])
        return 2
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
