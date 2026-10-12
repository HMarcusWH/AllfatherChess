#!/usr/bin/env python3
"""Independent RELEASE-1 payload verifier (staged tree or installed OCI image).

This is *not* a signature verifier, destination-host qualification, legal
clearance or authorization to contact Lichess.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseRefusal(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ReleaseRefusal(message)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fd:
        for block in iter(lambda: fd.read(1<<20), b""):
            h.update(block)
    return h.hexdigest()


def entry(path: Path):
    require(path.is_file() and not path.is_symlink(),
            "linked or missing release entry: " + str(path))
    return {"bytes": path.stat().st_size, "sha256": sha(path)}


def verify(*, manifest_path: Path, image_mode: bool = False,
           stage: Path | None = None, root: Path = ROOT) -> dict:
    require(manifest_path.is_file() and not manifest_path.is_symlink(),
            "combined release manifest missing")
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(doc.get("schema_version") == 1 and
            doc.get("profile_id") == "release-1-offline-oci-v1" and
            doc.get("scope") == "no-network-no-distribution",
            "combined release has unsupported authority")
    boundary = doc.get("claim_boundary")
    require(boundary == {
        "offline_candidate_assembled": True,
        "host_qualified": False, "license_approved": False,
        "network_recovery_qualified": False,
        "public_distribution_authorized": False, "public_bot_release": False,
    }, "combined release claim boundary changed")
    require(re.fullmatch(r"[0-9a-f]{40}", doc.get("source_commit", "")) is not None
            and re.fullmatch(r"[0-9a-f]{40}", doc.get("source_tree", "")) is not None,
            "release source identity invalid")
    require(re.fullmatch(r"docker\.io/library/ubuntu:24\.04@sha256:[0-9a-f]{64}",
                         doc.get("base_image", "")) is not None,
            "release base not digest locked")
    require(re.fullmatch(r"\d{8}T\d{6}Z", doc.get("ubuntu_snapshot", "")) is not None,
            "Ubuntu snapshot not locked")
    if image_mode:
        roots = {
            "engine": Path("/opt/allfather"),
            "bridge": Path("/opt/bridge"),
            "wheelhouse": Path("/opt/release/wheelhouse"),
        }
    else:
        require(stage is not None, "stage mode requires --stage")
        roots = {name: stage / name for name in ("engine", "bridge", "wheelhouse")}
        require(manifest_path.resolve() == (stage/"combined-manifest.json").resolve(),
                "stage manifest not at canonical path")
    inventory = doc.get("file_inventory")
    require(isinstance(inventory, list) and len(inventory) > 200,
            "combined inventory empty or truncated")
    listed = {}
    for item in inventory:
        name = item.get("path")
        require(isinstance(name, str) and name and "\\" not in name and
                not name.startswith("/") and ".." not in Path(name).parts,
                "noncanonical payload path")
        prefix, slash, suffix = name.partition("/")
        require(bool(slash) and prefix in roots and bool(suffix),
                "unexpected payload root")
        target = roots[prefix] / suffix
        require(target.resolve().is_relative_to(roots[prefix].resolve()),
                "payload escaped its immutable root")
        require(name not in listed, "duplicate manifest entry: "+name)
        seen = entry(target)
        require(item == {"path": name, **seen},
                "combined payload changed or incomplete: "+name)
        listed[name] = True
    real = []
    for name, directory in roots.items():
        require(directory.is_dir() and not directory.is_symlink(), "payload directory absent")
        for file in directory.rglob("*"):
            if file.is_file() or file.is_symlink():
                require(not file.is_symlink(), "payload symlink refused")
                real.append(name+"/"+file.relative_to(directory).as_posix())
    require(set(real) == set(listed), "unexpected or missing files in OCI application")
    engine_manifest = roots["engine"] / "build/online-release/release-manifest.json"
    require(sha(engine_manifest) == doc["engine_release_manifest_sha256"],
            "sealed engine manifest hash changed")
    engine = json.loads(engine_manifest.read_text(encoding="utf-8"))
    require(engine.get("source_commit") == doc["source_commit"] and
            engine.get("source_tree") == doc["source_tree"],
            "sealed engine belongs to another commit/tree")
    wheel_attestation = roots["wheelhouse"] / "wheel-attestation.json"
    lock = roots["wheelhouse"] / "runtime-wheels.lock"
    require(sha(wheel_attestation) == doc["wheel_attestation_sha256"] and
            sha(lock) == doc["wheel_lock_sha256"], "Python dependency lock changed")
    wheel_doc = json.loads(wheel_attestation.read_text(encoding="utf-8"))
    wheelset = {f.name: sha(f) for f in roots["wheelhouse"].glob("*.whl")}
    require(wheel_doc.get("wheels") == wheelset and len(wheelset) == 12,
            "wheel byte inventory drift")
    overlay = doc.get("overlay")
    require(isinstance(overlay, dict) and overlay.get("mode") == "OFFLINE-4B-ONLY"
            and overlay.get("upstream_commit") == doc.get("bridge_commit")
            and overlay.get("public_bot_release") is False,
            "recovery bridge overlay authority drift")
    for name, expected in overlay.get("patched_upstream", {}).items():
        require(sha(roots["bridge"] / name) == expected["patched_sha256"],
                "patched bridge file substituted: " + name)
    require(len(overlay.get("patched_upstream", {})) == 3 and
            len(overlay.get("guard_modules", {})) == 4,
            "upstream overlay incomplete")
    for name, expected in overlay["guard_modules"].items():
        require(sha(roots["bridge"] / "deploy/lichess" / name) == expected,
                "recovery guard module changed: "+name)
    require(not (roots["bridge"]/"config.yml").exists() and
            not (roots["bridge"] / ".git").exists(), "live config/git objects packaged")
    if not image_mode:
        policy_path = root / "qualification/release-image-v1.json"
        license_path = root / "qualification/license-compliance-v1.json"
        qualification_path = root / "qualification/release-qualification-v1.json"
        require(sha(policy_path) == doc["image_policy_sha256"]
                and sha(license_path) == doc["licensing_policy_sha256"]
                and sha(qualification_path) == doc["qualification_policy_sha256"],
                "source-controlled release input drift")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        require(doc["base_image"] == policy["base_image"] and
                doc["ubuntu_snapshot"] == policy["ubuntu_snapshot"],
                "image APT/base lock differs from release manifest")
        require(subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],
                    text=True).strip() == doc["source_commit"],
                "stage source not exact checkout head")
    return {
        "schema_version": 1, "source_commit": doc["source_commit"],
        "source_tree": doc["source_tree"], "profile": doc["profile_id"],
        "inventory_files": len(listed), "engine_release_verified": True,
        "bridge_verified": True, "python_wheels_verified": True,
        "image_mode": image_mode, "host_qualified": False,
        "license_approved": False, "network_recovery_qualified": False,
        "public_bot_release": False, "release_blocked": True,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", type=Path)
    p.add_argument("--image", action="store_true")
    p.add_argument("--manifest", type=Path)
    p.add_argument("--report", type=Path)
    a = p.parse_args()
    if a.image == bool(a.stage):
        p.error("choose exactly one --image or --stage")
    manifest = a.manifest or (
        Path("/opt/release/combined-manifest.json") if a.image
        else a.stage / "combined-manifest.json")
    try:
        result = verify(manifest_path=manifest, stage=a.stage, image_mode=a.image)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        p.exit(2, "RELEASE-1 verification refused: "+str(exc)+"\n")
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,sort_keys=True))


if __name__ == "__main__":
    main()
