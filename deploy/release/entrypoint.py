#!/usr/bin/env python3
"""RELEASE-1 candidate entrypoint: inspect or OFFLINE tests only.

There is deliberately NO live-bot mode, network host override, BOT upgrade,
token importer or general shell launcher. The CI runtime uses --network none.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ENGINE = Path("/opt/allfather")
BRIDGE = Path("/opt/bridge")
VALIDATOR = Path("/opt/release/verify_release.py")
MANIFEST = Path("/opt/release/combined-manifest.json")


def refuse(message):
    raise SystemExit("RELEASE-1 offline entrypoint refused: " + message)


def verify():
    if os.geteuid() != 10001:
        refuse("runtime must execute with qualified nonroot UID 10001")
    for item in (ENGINE, BRIDGE, MANIFEST, VALIDATOR):
        if not item.exists() or item.is_symlink():
            refuse("missing or substituted immutable artifact")
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--image"],
        capture_output=True, text=True, timeout=45,
    )
    if proc.returncode:
        refuse("combined manifest verification rejected: " + proc.stderr[:900])
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        refuse("independent manifest verifier did not return valid JSON")
    if report.get("public_bot_release") is not False or report.get("engine_release_verified") is not True:
        refuse("offline image verification overclaimed authority")
    return report


def inventory():
    os_packages = subprocess.run(
        ["dpkg-query", "-W", "--showformat=${Package}=${Version}\n"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.splitlines()
    python_packages = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=json",
         "--disable-pip-version-check"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout
    return {"schema_version": 1,
            "profile_id": "release-runtime-inventory-v1",
            "uid": os.geteuid(), "os_packages": sorted(os_packages),
            "python_packages": json.loads(python_packages),
            "python_version": sys.version.split()[0],
            "public_bot_release": False}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("verify", "uci-check", "inventory", "offline-case"),
                   nargs="?", default="verify")
    p.add_argument("--scenario")
    p.add_argument("--color", choices=("white","black"))
    p.add_argument("--output", type=Path)
    p.add_argument("--timeout", type=float, default=90)
    a = p.parse_args()
    report = verify()
    if a.mode == "verify":
        print(json.dumps(report,sort_keys=True))
        return
    if a.mode == "inventory":
        print(json.dumps(inventory(),sort_keys=True))
        return
    if a.mode == "uci-check":
        proc = subprocess.run(
            [sys.executable, str(ENGINE / "deploy/bin/allfather-online"), "--check"],
            cwd=ENGINE, capture_output=True, text=True, timeout=45,
        )
        if proc.returncode or "offline seal verified" not in proc.stdout:
            refuse("offline engine seal check failed")
        print(json.dumps({"uci_seal_qualified": True,
                          "public_bot_release": False},sort_keys=True))
        return
    if a.mode == "offline-case":
        if os.getenv("ONLINE4B_ISOLATED_CONTAINER") != "1" or \
           os.getenv("RELEASE1_OFFLINE_ISOLATED") != "1":
            refuse("offline-case requires the explicit test-container isolation flags")
        if not (a.scenario and a.color and a.output):
            refuse("offline-case requires scenario/color/output")
        if not a.output.resolve().is_relative_to(Path("/var/lib/allfather").resolve()):
            refuse("offline test output must be under durable /var/lib/allfather")
        runner = Path("/work/deploy/lichess/run_recovery_v2.py")
        if not runner.is_file() or runner.is_symlink():
            refuse("read-only mounted exact-source offline test runner absent")
        argv = [
            sys.executable, str(runner),
            "--prepatched-image",
            "--bridge", str(BRIDGE),
            "--release", str(ENGINE),
            "--scenario", a.scenario, "--color", a.color,
            "--source-sha", report["source_commit"],
            "--output", str(a.output.resolve()),
            "--timeout", str(a.timeout),
        ]
        os.execv(sys.executable, argv)
    refuse("unsupported execution mode")


if __name__ == "__main__":
    main()
