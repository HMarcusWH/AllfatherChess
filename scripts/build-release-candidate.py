#!/usr/bin/env python3
"""RELEASE-1: stage an exact-head, OFFLINE-ONLY combined OCI input tree.

Never publish the stage or image while source-distribution review is open.
Do not alter upstream or repackage an unsealed/replaced engine.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_POLICY = ROOT / "qualification/release-image-v1.json"
COMPLIANCE = ROOT / "qualification/license-compliance-v1.json"
QUALIFICATION = ROOT / "qualification/release-qualification-v1.json"


def require(ok, error):
    if not ok:
        raise ValueError(error)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_file(root: Path, path: Path) -> dict:
    require(path.is_file() and not path.is_symlink() and
            path.resolve().is_relative_to(root.resolve()),
            "non-file/linked/escaped payload: " + str(path))
    name = path.relative_to(root).as_posix()
    require(not any(piece in (".git", ".env", "__pycache__") for piece in Path(name).parts),
            "forbidden payload path: " + name)
    require(not (name.endswith(".key") or name.endswith(".pem") or
                 name.endswith(".pyc") or name.endswith(".p12") or
                 name.endswith("config.yml")), "credential/config candidate: " + name)
    return {"path": name, "bytes": path.stat().st_size, "sha256": digest(path)}


def inventory(stage: Path) -> list[dict]:
    paths = [p for p in stage.rglob("*") if p.is_file() or p.is_symlink()]
    return [clean_file(stage, p) for p in sorted(paths) if p.name != "combined-manifest.json"]


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args],
                                   text=True, stderr=subprocess.PIPE).strip()


def parse_inputs():
    policy = json.loads(IMAGE_POLICY.read_text(encoding="utf-8"))
    license_info = json.loads(COMPLIANCE.read_text(encoding="utf-8"))
    qual = json.loads(QUALIFICATION.read_text(encoding="utf-8"))
    require(policy["profile_id"] == "release-image-v1" and
            policy["scope"] == "offline-oci-candidate-only"
            and policy["platform"] == "linux/amd64" and
            re.fullmatch(r"docker\.io/library/ubuntu:24\.04@sha256:[0-9a-f]{64}",
                         policy["base_image"]) is not None, "unfrozen OCI base")
    require(re.fullmatch(r"\d{8}T\d{6}Z", policy["ubuntu_snapshot"]) is not None,
            "Ubuntu snapshot ID malformed")
    for flag in ("public_distribution_authorized", "public_bot_release",
                 "live_transport_qualified","destination_host_qualified",
                 "controller_license_approved","package_version_reviewed"):
        require(policy[flag] is False, "unsupported promotion in release policy: "+flag)
    require(license_info["publication_allowed"] is False and
            license_info["legal_approval"] is False and
            license_info["status"] == "REVIEW_REQUIRED",
            "combined license has not been authorized")
    require(qual["public_bot_release"] is False and
            qual["public_release_gate"] is False, "release gate overclaims authority")
    snapshot = (ROOT / "deploy/release/ubuntu.sources").read_text(encoding="utf-8")
    require(snapshot.count("Snapshot: " + policy["ubuntu_snapshot"]) == 2 and
            "trusted=yes" not in snapshot and snapshot.count("Signed-By: ") == 2,
            "APT sources are not bound to the frozen signed snapshot")
    docker = (ROOT / "deploy/release/Dockerfile").read_text(encoding="utf-8")
    require("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}" in docker and
            "COPY build/release1-stage/engine/" in docker and
            "COPY build/release1-stage/bridge/" in docker,
            "combined Dockerfile does not use the selected locked release inputs")
    return policy, license_info, qual


def load_seal():
    spec = importlib.util.spec_from_file_location(
        "release1_engine_seal", ROOT / "scripts/release-manifest.py")
    require(spec and spec.loader, "ONLINE-3A verifier unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assemble(*, release: Path, bridge: Path, wheels: Path, stage: Path) -> dict:
    policy, license_info, qual = parse_inputs()
    require(stage.resolve().is_relative_to((ROOT / "build").resolve()),
            "release stage must remain under ignored build/")
    require(not stage.exists() and not stage.is_symlink(), "release stage already exists")
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    require(not git("status", "--porcelain", "--untracked-files=no"),
            "dirty tracked sources are not a release input")
    source_manifest = load_seal().verify(release, release / "build/online-release/release-manifest.json")
    require(source_manifest["source_commit"] == commit and
            source_manifest["source_tree"] == tree, "engine seal not from exact head/tree")
    from deploy.lichess import fetch_bridge, recovery_overlay
    upstream_lock = json.loads((ROOT / "deploy/lichess/bridge.lock.json").read_text(encoding="utf-8"))
    fetch_bridge.verify(bridge, upstream_lock)
    require(upstream_lock["live_api_permitted"] is False,
            "upstream lock unexpectedly allows live API")
    wheels_attest = json.loads((wheels / "wheel-attestation.json").read_text(encoding="utf-8"))
    provided = {p.name: digest(p) for p in wheels.glob("*.whl")}
    require(wheels_attest["wheels"] == provided and len(provided) == 12,
            "runtime wheel byte attestation mismatch")
    require(digest(wheels / "runtime-wheels.lock") == wheels_attest["runtime_lock_sha256"],
            "runtime wheel hash lock drift")
    require(wheels_attest["trusted_chess_sdist_sha256"] ==
            "a8b43e5678fdb3000695bdaa573117ad683761e5ca38e591c4826eba6d25bb39",
            "untrusted chess wheel origin")
    require((ROOT / "deploy/lichess/requirements.in").is_file(),
            "pinned bridge requirements input absent")
    try:
        shutil.copytree(release, stage / "engine")
        shutil.copytree(bridge, stage / "bridge", ignore=shutil.ignore_patterns(
            ".git","__pycache__","*.pyc"))
        shutil.copytree(wheels, stage / "wheelhouse")
        shutil.copyfile(ROOT / "deploy/lichess/extra_game_handlers.py",
                        stage / "bridge/extra_game_handlers.py")
        overlay = recovery_overlay.apply(stage / "bridge")
        payload = inventory(stage)
        require(len(payload) > 200, "combined release payload unexpectedly incomplete")
        manifest = {
            "schema_version": 1,
            "profile_id": "release-1-offline-oci-v1",
            "scope": "no-network-no-distribution",
            "source_commit": commit, "source_tree": tree,
            "engine_release_manifest_sha256": digest(
                stage / "engine/build/online-release/release-manifest.json"),
            "bridge_commit": upstream_lock["commit"],
            "overlay": overlay,
            "base_image": policy["base_image"],
            "ubuntu_snapshot": policy["ubuntu_snapshot"],
            "wheel_attestation_sha256": digest(stage / "wheelhouse/wheel-attestation.json"),
            "wheel_lock_sha256": digest(stage / "wheelhouse/runtime-wheels.lock"),
            "file_inventory": payload,
            "licensing_policy_sha256": digest(COMPLIANCE),
            "qualification_policy_sha256": digest(QUALIFICATION),
            "image_policy_sha256": digest(IMAGE_POLICY),
            "claim_boundary": {
                "offline_candidate_assembled": True,
                "host_qualified": False, "license_approved": False,
                "network_recovery_qualified": False,
                "public_distribution_authorized": False,
                "public_bot_release": False,
            },
        }
        (stage / "combined-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--release", type=Path, default=ROOT / "dist/online3a-stage")
    p.add_argument("--bridge", type=Path, default=ROOT / "build/vendor/lichess-bot")
    p.add_argument("--wheels", type=Path, default=ROOT / "build/online3b-wheels")
    p.add_argument("--stage", type=Path, default=ROOT / "build/release1-stage")
    args = p.parse_args()
    try:
        result = assemble(release=args.release.resolve(), bridge=args.bridge.resolve(),
                          wheels=args.wheels.resolve(), stage=args.stage)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        p.exit(2, "RELEASE-1 staging refused: " + str(exc) + "\n")
    print(json.dumps({"profile": result["profile_id"],
                      "source_commit": result["source_commit"],
                      "files": len(result["file_inventory"]),
                      "public_distribution_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    main()
