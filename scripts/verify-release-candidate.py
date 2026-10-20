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


def installed_mutable_replay(prefix: str, base: Path,
                             item: Path, *, installed: bool) -> bool:
    """Exclude ONLY files from the explicitly mounted runtime replay volume.

    Stage validation and immutable release paths remain strict. The replay
    subtree cannot hide changes to executable/code files. The replay volume
    must be on a different filesystem device from the read-only image root.
    """
    if prefix != "allfather" or not installed:
        return False
    relative = item.relative_to(base)
    if relative.parts[:2] != ("build", "replays-online-hybrid-v2"):
        return False
    replay_root = base / "build/replays-online-hybrid-v2"
    insist(replay_root.is_dir() and not replay_root.is_symlink()
           and replay_root.stat().st_dev != base.stat().st_dev,
           "unlisted replay files require separately mounted storage")
    return True


def verify(manifest: dict, roots: dict[str, Path],
           *, installed: bool, policy: dict) -> dict:
    insist(manifest.get("schema_version") == 1 and
           manifest.get("release_kind") == "RELEASE-1-OFFLINE-CANDIDATE",
           "unknown release manifest identity")
    for flag in ("artifact_distribution_authorized","public_bot_release",
                 "network_recovery_qualified","deployment_host_qualified"):
        insist(manifest.get(flag) is False, "release claimed unapproved authority: " + flag)
    frozen_bridge = roots["tools"] / "deploy/lichess/bridge.lock.json"
    insist(frozen_bridge.is_file() and not frozen_bridge.is_symlink(),
           "frozen upstream bridge lock absent from packaged tools")
    bridge_lock = read(frozen_bridge)
    insist(manifest.get("source_commit") and manifest.get("source_tree") and
           manifest.get("bridge_commit") == bridge_lock["commit"] and
           bridge_lock.get("live_api_permitted") is False and
           bridge_lock.get("scope") == "ONLINE-3B-OFFLINE-ONLY" and
           manifest.get("source_attestation", {}).get(
               "deploy/lichess/bridge.lock.json") == sha(frozen_bridge),
           "upstream bridge commit or source lock does not match pinned input")
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
                if installed_mutable_replay(prefix, base, item, installed=installed):
                    continue
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
    # RELEASE-2B-1: independent source/policy binding; a producer's
    # wheel-attestation success claim cannot create admission authority.
    source_name = "deploy/lichess/requirements.release.lock"
    selection_name = "qualification/release-python-wheels-v1.json"
    builder_name = "qualification/release-python-builder-v1.json"
    source = roots["tools"] / source_name
    selection = roots["tools"] / selection_name
    attested = manifest.get("source_attestation", {})
    insist(source.is_file() and selection.is_file() and
           sha(source) == attested.get(source_name) and
           sha(selection) == attested.get(selection_name),
           "selected Python source/selection inputs differ from source seal")
    selected = read(selection)
    insist(selected.get("scope") == "release-2b-python-wheel-candidate" and
           selected.get("wheel_selection_reviewed") is False and
           selected.get("public_bot_release") is False and
           selected.get("expected_wheel_count") == 12 and
           selected.get("committed_resolution_sha256") == sha(source) and
           selected.get("wheel_builder_policy_sha256") == attested.get(builder_name),
           "source-attested Python wheel lock policy drift")
    # The native stage's input declarations must match source-sealed bytes.
    for name in (
        "qualification/release-bootstrap-v1.json",
        "qualification/release-native-build-v1.json",
        "qualification/release-os-packages.lock",
        "qualification/release-os-apt-selections-v1.txt",
        "scripts/verify-release-dependency-build.py",
        "deploy/release/bootstrap.sources",
        "deploy/release/ubuntu.sources",
    ):
        local = roots["tools"] / name
        insist(local.is_file() and not local.is_symlink() and
               sha(local) == attested.get(name),
               "native dependency-stage source identity not verified: " + name)
    native = read(roots["tools"] / "qualification/release-native-build-v1.json")
    insist(native.get("lock_approved") is False and
           native.get("public_bot_release") is False and
           native.get("acquired_package_hashes_reviewed") is False,
           "native dependency policy illegally claims approval")
    attest = read(roots["wheels"] / "wheel-attestation.json")
    insist(attest.get("source_lock_sha256") == sha(source) and
           attest.get("builder_policy_sha256") == attested.get(builder_name) and
           attest.get("wheels") == selected.get("wheels") and
           attest.get("trusted_chess_sdist_sha256") == selected.get("chess_source_archive_sha256") and
           attest.get("built_chess_wheel_sha256") ==
                selected.get("wheels", {}).get("chess-1.11.2-py3-none-any.whl"),
           "wheels do not match source-attested candidate selection")
    source_text = source.read_text(encoding="utf-8")
    prefix = "chess==1.11.2 " + chr(92) + "\n    --hash=sha256:"
    original = prefix + selected["chess_source_archive_sha256"]
    replacement = prefix + selected["wheels"]["chess-1.11.2-py3-none-any.whl"]
    insist(source_text.count(original) == 1 and
           (roots["wheels"] / "runtime-wheels.lock").read_text(encoding="utf-8") ==
                source_text.replace(original, replacement),
           "runtime installer lock differs from committed selection")
    wheel_files = {p.name: sha(p) for p in roots["wheels"].glob("*.whl")}
    insist(len(wheel_files) == 12 and attest["wheels"] == wheel_files and
           sha(roots["wheels"] / "wheel-attestation.json") ==
           manifest["wheel_attestation_sha256"] and
           sha(roots["wheels"] / "runtime-wheels.lock") ==
           attest["runtime_lock_sha256"] ==
           manifest["runtime_wheel_lock_sha256"],
           "wheel inventory or hash-locked requirements mismatch")
    if installed:
        # The real image must inherit exactly the native stage already checked
        # by its source-controlled verifier; the probe alone is insufficient.
        import importlib.util
        native_spec = importlib.util.spec_from_file_location(
            "release2b_native",
            roots["tools"] / "scripts/verify-release-dependency-build.py")
        insist(native_spec is not None and native_spec.loader is not None,
               "native installed dependency verifier missing")
        native_module = importlib.util.module_from_spec(native_spec)
        native_spec.loader.exec_module(native_module)
        native_report = native_module.verify(
            root=roots["tools"],
            evidence=Path("/opt/allfather-release/native-evidence"),
            os_inventory=Path("/opt/allfather-release/os-packages.txt"))
        insist(native_report.get("verdict") == "PASS" and
               native_report.get("dependency_lock_approved") is False and
               native_report.get("public_bot_release") is False,
               "installed native dependency stage did not pass")
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
