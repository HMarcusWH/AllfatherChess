#!/usr/bin/env python3
"""Assemble an immutable RELEASE-1 offline candidate from exact qualified inputs.

No Docker, network, credentials, account upgrade or publication. This script
only assembles sealed engine / pinned+patched bridge / wheelhouse / offline
testing modules with a cryptographic per-file manifest.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.lichess import fetch_bridge, recovery_overlay

POLICY = "qualification/release-image-v1.json"
BRIDGE_LOCK = "deploy/lichess/bridge.lock.json"
ENGINE_MANIFEST = "build/online-release/release-manifest.json"

TOOL_FILES = (
    "deploy/release/entrypoint.py",
    "deploy/release/offline_smoke.py",
    "deploy/release/runtime-contract.json",
    "qualification/release-image-v1.json",
    "qualification/release-qualification-v1.json",
    "qualification/license-compliance-v1.json",
    "deploy/lichess/bridge_policy.py",
    "deploy/lichess/log_capture.py",
    "tests/lichess/fake_lichess_api.py",
    "tests/lichess/scripted_opponent.py",
    "tests/lichess/fault_plan.py",
    "tests/lichess/stream_sessions.py",
    "tests/lichess/transport_trace.py",
    "tests/fixtures/lichess/recovery_scenarios.json",
    "scripts/verify-release-candidate.py",
)
SOURCE_FILES = (POLICY, BRIDGE_LOCK, "qualification/release-qualification-v1.json",
                "qualification/license-compliance-v1.json",
                "deploy/release/ubuntu.sources", "deploy/release/Dockerfile",
                "deploy/release/Dockerfile.dockerignore",
                "scripts/build-release-candidate.py", *TOOL_FILES)


def fail(condition: bool, reason: str):
    if not condition:
        raise ValueError(reason)


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fd:
        for chunk in iter(lambda: fd.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def checksum_tree(root: Path) -> list[dict]:
    entries = []
    for item in sorted(root.rglob("*")):
        fail(not item.is_symlink(), "symbolic link in candidate " + str(item))
        if not item.is_file():
            continue
        name = item.relative_to(root).as_posix()
        fail(not any(part in (".git", "__pycache__") for part in Path(name).parts),
             "forbidden transient path in release: " + name)
        entries.append({"path": name, "bytes": item.stat().st_size,
                        "sha256": digest(item)})
    return entries


def assemble(root: Path, destination: Path, release: Path, bridge: Path,
             wheelhouse: Path) -> dict:
    root = root.resolve()
    fail((root / ".git").is_dir(), "requires a Git checkout")
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                   text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
                                   text=True).strip()
    fail(not subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        text=True).strip(), "dirty tracked source checkout")
    image_policy = json.loads((root / POLICY).read_text(encoding="utf-8"))
    fail(image_policy["scope"] == "offline-image-candidate-only" and
         image_policy["allow_real_credentials"] is False and
         image_policy["public_bot_release"] is False,
         "unapproved release policy")
    upstream_lock = json.loads((root / BRIDGE_LOCK).read_text(encoding="utf-8"))
    fetch_bridge.verify(bridge, upstream_lock)
    manifest = json.loads((release / ENGINE_MANIFEST).read_text(encoding="utf-8"))
    fail(manifest["source_commit"] == head and manifest["source_tree"] == tree,
         "sealed UCI release from a different Git checkout")
    probe = subprocess.run(
        [sys.executable, str(release / "deploy/bin/allfather-online"), "--check"],
        cwd=release, capture_output=True, text=True, timeout=45,
    )
    fail(probe.returncode == 0 and "offline seal verified" in probe.stdout,
         "sealed online UCI candidate failed its own integrity check")
    witness = json.loads((wheelhouse / "wheel-attestation.json").read_text(encoding="utf-8"))
    listed = witness["wheels"]
    actual = {file.name: digest(file) for file in sorted(wheelhouse.glob("*.whl"))}
    fail(listed == actual and len(actual) == 12,
         "runtime Python wheels differ from locked wheel attestation")
    fail(digest(wheelhouse / "runtime-wheels.lock") == witness["runtime_lock_sha256"],
         "Python runtime wheel lock drift")
    fail(not destination.exists() and not destination.is_symlink(),
         "candidate destination already exists; refuse overwrite")
    fail(destination.resolve().is_relative_to(root), "candidate outside repository")
    dest = destination
    try:
        shutil.copytree(release, dest / "allfather", symlinks=True)
        shutil.copytree(bridge, dest / "bridge",
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        shutil.copy2(root / "deploy/lichess/extra_game_handlers.py",
                     dest / "bridge/extra_game_handlers.py")
        overlay = recovery_overlay.apply(dest / "bridge")
        fail(overlay["upstream_commit"] == upstream_lock["commit"],
             "overlay source identity mismatch")
        shutil.copytree(wheelhouse, dest / "wheels")
        for name in TOOL_FILES:
            source = root / name
            fail(source.is_file() and not source.is_symlink(),
                 "missing or linked release tool: " + name)
            target = dest / "tools" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        result = {
            "schema_version": 1, "release_kind": "RELEASE-1-OFFLINE-CANDIDATE",
            "source_commit": head, "source_tree": tree,
            "online3a_manifest_sha256": digest(release / ENGINE_MANIFEST),
            "bridge_commit": upstream_lock["commit"],
            "overlay": overlay,
            "release_image_policy_sha256": digest(root / POLICY),
            "source_attestation": {name: digest(root / name) for name in SOURCE_FILES},
            "wheel_attestation_sha256": digest(wheelhouse / "wheel-attestation.json"),
            "runtime_wheel_lock_sha256": digest(wheelhouse / "runtime-wheels.lock"),
            "artifact_distribution_authorized": False,
            "network_recovery_qualified": False,
            "deployment_host_qualified": False,
            "public_bot_release": False,
            "files": checksum_tree(dest),
        }
        with (dest / "manifest.json").open("w", encoding="utf-8") as fd:
            json.dump(result, fd, sort_keys=True, indent=2, allow_nan=False)
            fd.write("\n")
        return result
    except BaseException:
        if dest.exists():
            shutil.rmtree(dest)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--release", type=Path, default=Path("dist/online3a-stage"))
    p.add_argument("--bridge", type=Path, default=Path("build/vendor/lichess-bot"))
    p.add_argument("--wheels", type=Path, default=Path("build/online3b-wheels"))
    p.add_argument("--output", type=Path, default=Path("build/release1-stage"))
    a = p.parse_args()
    root = a.root.resolve()
    resolve = lambda x: x if x.is_absolute() else root / x
    try:
        record = assemble(root, resolve(a.output), resolve(a.release),
                          resolve(a.bridge), resolve(a.wheels))
        print(json.dumps({"status": "STAGED", "source_commit": record["source_commit"],
                          "files": len(record["files"]),
                          "public_bot_release": False}, sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        p.exit(2, "RELEASE-1 assembly rejected: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
