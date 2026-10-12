#!/usr/bin/env python3
"""Independently verify RELEASE-1 staged/install trees and offline claim limits.

A self-reported success boolean does not replace per-file checks. This
verifies bytes and structure; publisher authentication is a separate gate.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

DIGEST = re.compile(r"^[0-9a-f]{64}$")
RELEASE_PREFIXES = ("allfather/", "bridge/", "wheels/", "tools/")


def insist(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(name: str):
    if not isinstance(name, str):
        raise ValueError("invalid manifest name")
    p = PurePosixPath(name)
    insist(not p.is_absolute() and name == str(p) and
           ".." not in p.parts and "\\" not in name and
           name.startswith(RELEASE_PREFIXES),
           "unsafe or noncanonical release path: " + repr(name))
    return p


def stage_roots(stage: Path):
    stage = stage.resolve()
    return {prefix: stage / prefix for prefix in ("allfather", "bridge", "wheels", "tools")}


def image_roots():
    return {
        "allfather": Path("/opt/allfather"),
        "bridge": Path("/opt/bridge"),
        "tools": Path("/opt/release-tools"),
        "wheels": Path("/opt/allfather-release/wheels"),
    }


def verify(manifest: dict, roots: dict[str, Path],
           *, installed: bool, policy: dict) -> dict:
    insist(manifest.get("schema_version") == 1 and
           manifest.get("release_kind") == "RELEASE-1-OFFLINE-CANDIDATE",
           "unknown release manifest identity")
    for flag in ("artifact_distribution_authorized","public_bot_release",
                 "network_recovery_qualified","deployment_host_qualified"):
        insist(manifest.get(flag) is False, "release claimed unapproved authority: " + flag)
    insist(manifest.get("source_commit") and manifest.get("source_tree") and
           manifest.get("bridge_commit") ==
           read(roots["tools"] / "qualification/release-image-v1.json").get("bridge_commit",
           manifest["bridge_commit"]), "incomplete source/bridge metadata")
    expected_policy = roots["tools"] / "qualification/release-image-v1.json"
    insist(expected_policy.is_file() and sha(expected_policy) ==
           manifest["release_image_policy_sha256"], "frozen policy digest mismatch")
    insist(policy["public_bot_release"] is False and
           policy["allow_real_credentials"] is False and
           policy["guard_transport"] == "loopback-only" and
           policy["scope"] == "offline-image-candidate-only",
           "release policy allows unapproved live operation")
    files = manifest.get("files")
    insist(isinstance(files, list) and bool(files),
           "release file manifest empty")
    listed = [x.get("path") for x in files]
    insist(listed == sorted(set(listed)), "release manifest not sorted or duplicate paths")
    known = set()
    for record in files:
        item = _safe_name(record["path"])
        prefix = item.parts[0]
        target = roots[prefix] / Path(*item.parts[1:])
        base = roots[prefix].resolve()
        insist(not target.is_symlink() and target.is_file() and
               target.resolve().is_relative_to(base), "linked/missing release file: " + str(item))
        insist(type(record.get("bytes")) is int and
               record["bytes"] == target.stat().st_size and
               isinstance(record.get("sha256"), str) and
               DIGEST.fullmatch(record["sha256"]) is not None and
               sha(target) == record["sha256"], "release bytes drift: " + str(item))
        known.add(str(item))
    actual = set()
    for prefix, base in roots.items():
        insist(base.is_dir() and not base.is_symlink(),
               "release component directory missing: " + prefix)
        for item in base.rglob("*"):
            insist(not item.is_symlink(), "symlinked release path: " + str(item))
            if item.is_file():
                actual.add(prefix + "/" + item.relative_to(base).as_posix())
    insist(actual == known, "release tree has unlisted/missing files: " +
           repr(sorted(actual.symmetric_difference(known))[:8]))
    seal = read(roots["allfather"] / "build/online-release/release-manifest.json")
    insist(seal.get("source_commit") == manifest["source_commit"] and
           seal.get("source_tree") == manifest["source_tree"] and
           sha(roots["allfather"] / "build/online-release/release-manifest.json") ==
           manifest["online3a_manifest_sha256"],
           "sealed engine identity drift")
    lock = read(roots["tools"] / "qualification/release-image-v1.json")
    overlay = read(roots["bridge"] / "recovery-overlay-manifest.json")
    insist(overlay == manifest.get("overlay") and
           overlay.get("mode") == "OFFLINE-4B-ONLY" and
           overlay.get("upstream_commit") == manifest["bridge_commit"] and
           overlay.get("public_bot_release") is False,
           "bridge overlay identity drift")
    for rel, value in overlay["patched_upstream"].items():
        insist(sha(roots["bridge"] / rel) == value["patched_sha256"],
               "patched upstream source bytes drift: " + rel)
    for name, value in overlay["guard_modules"].items():
        path = "deploy/lichess/" + name
        insist(sha(roots["bridge"] / path) == value,
               "recovery guard module bytes drift: " + name)
    attest = read(roots["wheels"] / "wheel-attestation.json")
    wheel_files = {p.name: sha(p) for p in roots["wheels"].glob("*.whl")}
    insist(len(wheel_files) == 12 and attest["wheels"] == wheel_files and
           sha(roots["wheels"] / "wheel-attestation.json") ==
           manifest["wheel_attestation_sha256"] and
           sha(roots["wheels"] / "runtime-wheels.lock") ==
           attest["runtime_lock_sha256"] ==
           manifest["runtime_wheel_lock_sha256"],
           "wheel inventory or hash-locked requirements mismatch")
    executable = roots["allfather"] / "deploy/bin/allfather-online"
    check = subprocess.run([sys.executable, str(executable), "--check"],
                           cwd=roots["allfather"], timeout=50,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                           capture_output=True, text=True)
    insist(check.returncode == 0 and "offline seal verified" in check.stdout,
           "real sealed UCI launcher rejected the packaged binaries")
    return {
        "schema_version": 1, "installed": installed,
        "source_commit": manifest["source_commit"],
        "verified_files": len(files), "engine_seal_verified": True,
        "bridge_patch_verified": True, "wheel_bytes_verified": True,
        "image_runtime_dependency_qualified": False,
        "destination_host_qualified": False,
        "public_bot_release": False, "release_promotion_authorized": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--stage", type=Path)
    group.add_argument("--installed", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    try:
        if args.installed:
            roots = image_roots()
            manifest = read(Path("/opt/allfather-release/manifest.json"))
        else:
            stage = args.stage.resolve()
            roots = stage_roots(stage)
            manifest = read(stage / "manifest.json")
        policy = read(roots["tools"] / "qualification/release-image-v1.json")
        result = verify(manifest, roots, installed=args.installed, policy=policy)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        p.exit(2, "RELEASE-1 verification refused: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
