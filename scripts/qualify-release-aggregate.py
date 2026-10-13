#!/usr/bin/env python3
"""Independent RELEASE-1 admission; a successful build is NOT release promotion.

This report is allowed to certify an offline candidate only after exact
native/wheel lock hashes are reviewed and pinned; live, host, licensing and
distribution authorization are deliberately impossible in this milestone.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GIT_SHA1_HEX=re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX=re.compile(r"^[0-9a-f]{64}$")


def is_sha256_hex(value, *, prefixed=False):
    if not isinstance(value,str):
        return False
    if prefixed:
        if not value.startswith("sha256:"):
            return False
        value=value[len("sha256:"):]
    return SHA256_HEX.fullmatch(value) is not None


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as fd:
        for block in iter(lambda:fd.read(1<<20),b""):
            h.update(block)
    return h.hexdigest()


def require(ok,reason):
    if not ok:
        raise ValueError(reason)


def check_smoke_evidence(path:Path, key:str) -> dict:
    """Execute the independent host verifier, never trust the producer's ready."""
    module_path=ROOT/"scripts/qualify-release-smoke.py"
    spec=importlib.util.spec_from_file_location("release1_smoke_independent",module_path)
    require(spec is not None and spec.loader is not None,
            "independent packaged-smoke verifier missing")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.verify_file(path,key)


def qualify(*, stage:Path, oci:Path, oci_archive:Path, host:Path, smoke_dir:Path,
            os_inventory:Path, wheel_inventory:Path, source_sha:str, root:Path=ROOT):
    source= subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],
                                    text=True).strip()
    require(source == source_sha and GIT_SHA1_HEX.fullmatch(source) is not None,
            "incorrect release source revision")
    image_policy=load(root/"qualification/release-image-v1.json")
    qualification=load(root/"qualification/release-qualification-v1.json")
    licenses=load(root/"qualification/license-compliance-v1.json")
    manifest=load(stage/"manifest.json")
    oci_report=load(oci)
    host_report=load(host)
    runtime_wheels=load(stage/"wheels/wheel-attestation.json")
    require(manifest["source_commit"]==source and
            manifest["source_tree"] == subprocess.check_output([
               "git","-C",str(root),"rev-parse","HEAD^{tree}"],
               text=True).strip(), "candidate source/tree mismatch")
    require(manifest["release_image_policy_sha256"]==
            digest(root/"qualification/release-image-v1.json"),
            "candidate policy mismatch")
    require(oci_report.get("integrity_verified") is True and
            all(oci_report.get(field) is True for field in (
                "oci_layers_verified","oci_diff_ids_verified",
                "oci_application_identity_verified",
                "oci_attestation_references_verified",
                "oci_attestation_subjects_verified",
                "oci_sbom_verified","oci_provenance_verified")) and
            oci_report.get("public_bot_release") is False and
            oci_report.get("publisher_signature_verified") is False,
            "OCI attestation or verified application binding invalid")
    for key in ("oci_image_manifest_digest","oci_config_digest"):
        require(is_sha256_hex(oci_report.get(key),prefixed=True),
                "invalid OCI "+key)
    require(is_sha256_hex(oci_report.get("oci_archive_sha256")),
            "invalid OCI archive SHA-256 digest")
    require(oci_archive.is_file() and not oci_archive.is_symlink() and
            digest(oci_archive) == oci_report["oci_archive_sha256"],
            "OCI archive identity does not match independently verified report")
    require(host_report["destination_host_qualified"] is False and
            host_report["test_host_observed"] is True,
            "host admission attempted to promote CI runner")
    require(licenses["artifact_distribution_authorized"] is False
            and licenses["legal_review_completed"] is False
            and licenses["controller_distribution_license_approved"] is False,
            "licensing gate unexpectedly opened")
    require(qualification["artifact_distribution_authorized"] is False
            and qualification["release_promotion_authorized"] is False,
            "promotion gate unexpectedly opened")
    tests={}
    for key in ("r00-white","r00-black","r02-white","r10-white"):
        target=smoke_dir/key/"result.json"
        require(target.is_file(), "release-image smoke scenario missing: "+key)
        result=load(target)
        independent=check_smoke_evidence(target,key)
        require(independent.get("independent_qualified") is True and
                independent.get("run_id") == key and
                independent.get("public_bot_release") is False,
                "independent chess/HTTP evidence failed: "+key)
        require(result.get("offline") is True and
                result.get("ready") is True and
                result.get("runner_errors")==[] and
                result.get("public_bot_release") is False,
                "release-image smoke failed: "+key)
        game=result.get("game") or {}
        require(game.get("forbidden_attempts")==[],
                "release image simulator recorded forbidden API attempts: "+key)
        requests=result.get("http")
        require(isinstance(requests,list),
                "release-image complete HTTP audit missing: "+key)
        allow={"/api/token/test","/api/challenge/off3b001/accept",
               "/api/challenge/off3b001/decline"}
        prefix="/api/bot/game/off3b001/move/"
        require(all(isinstance(r,dict) and
                    (r.get("method")!="POST" or
                     (r.get("path") in allow or
                      (isinstance(r.get("path"),str)
                       and r["path"].startswith(prefix)
                       and r["path"][len(prefix):] not in ("","0000")))
                     and r.get("status")!=403)
                    for r in requests),
                "release image HTTP contains prohibited POST: "+key)
        if key=="r10-white":
            require(result.get("rate_cooldown_verified") is True,
                    "release-image 429 cooldown witness absent")
        if key=="r02-white":
            require(type(result.get("fresh_second_epoch_commits")) is int and
                    result["fresh_second_epoch_commits"]>0,
                    "release image restart has no fresh confirmed move")
        if key!="r10-white":
            require(result["game"]["complete"] is True,
                    "offline completed-game requirement not met: "+key)
        else:
            require(result["game"]["complete"] is False,
                    "rate-limit test unexpectedly marked game as complete")
        tests[key]={"passed":True,"independent_evidence":independent,
                    "accepted_moves":len(result["game"]["accepted"] or []),
                    "phase_count":len(result["observations"])}
    wheel_hash=digest(stage/"wheels/runtime-wheels.lock")
    wheel_inv=digest(wheel_inventory)
    native_hash=digest(os_inventory)
    checked={
        "os_lock":bool(image_policy["os_package_lock_complete"] and
          image_policy.get("exact_os_package_inventory_sha256")==native_hash),
        "wheel_lock":bool(image_policy["wheel_lock_complete"] and
          image_policy.get("exact_runtime_wheel_lock_sha256")==wheel_hash and
          image_policy.get("exact_runtime_wheel_inventory_sha256")==wheel_inv),
    }
    source_contents={
        "schema_version":1,"source_commit":source,
        "image_manifest_digest":oci_report["oci_image_manifest_digest"],
        "oci_image_config_digest":oci_report["oci_config_digest"],
        "oci_archive_sha256":oci_report["oci_archive_sha256"],
        "base_image":image_policy["base_image"],
        "apt_snapshot":image_policy["apt_snapshot"],
        "native_inventory_sha256":native_hash,
        "wheel_requirements_sha256":wheel_hash,
        "wheel_inventory_sha256":wheel_inv,
        "wheel_bytes":runtime_wheels["wheels"],
        "smoke":tests,"image_integrity_qualified":True,
        "frozen_dependency_lock_qualified":all(checked.values()),
        "dependency_lock_checks":checked,
        "offline_candidate_qualified":all(checked.values()),
        "license_approved":False,"destination_host_qualified":False,
        "network_recovery_qualified":False,
        "artifact_distribution_authorized":False,
        "public_bot_release":False,"release_promotion_authorized":False,
        "release_blocked":True,
        "blockers":[name for name,ok in checked.items() if not ok]
          + ["controller_license_review","source_availability_review",
             "destination_host_qualification","live_transport_qualification",
             "operator_live_play_approval"],
    }
    return source_contents


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stage",type=Path,required=True)
    p.add_argument("--oci",type=Path,required=True)
    p.add_argument("--oci-archive",type=Path,required=True)
    p.add_argument("--host",type=Path,required=True)
    p.add_argument("--smoke-dir",type=Path,required=True)
    p.add_argument("--os-inventory",type=Path,required=True)
    p.add_argument("--wheel-inventory",type=Path,required=True)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        report=qualify(stage=a.stage,oci=a.oci,oci_archive=a.oci_archive,host=a.host,
                       smoke_dir=a.smoke_dir,os_inventory=a.os_inventory,
                       wheel_inventory=a.wheel_inventory,source_sha=a.source_sha)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items() if k not in ("wheel_bytes","smoke")},
                         sort_keys=True))
    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
        p.exit(2,"RELEASE-1 aggregate verification refused: "+str(exc)+"\n")


if __name__=="__main__":
    main()
