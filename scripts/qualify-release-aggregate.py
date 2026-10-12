#!/usr/bin/env python3
"""Fail-closed, independent RELEASE-1 offline candidate aggregate.

Green candidate evidence is not source-distribution, licensed-host or live
Bot API approval. Never infer permission from a green upstream workflow name.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def require(cond,msg):
    if not cond:raise ValueError(msg)

def read(path:Path):
    return json.loads(path.read_text(encoding="utf-8"))

def qualify(*,source_sha:str,stage:dict,image:dict,oci:dict,
            host:dict,online:dict,license:dict,policy:dict,
            runtime_cases:list[dict],inventory:dict)->dict:
    require(re.fullmatch(r"[0-9a-f]{40}",source_sha) is not None,
            "exact release source commit malformed")
    require(stage.get("source_commit")==source_sha and
            stage.get("engine_release_verified") is True and
            stage.get("bridge_verified") is True and
            stage.get("python_wheels_verified") is True,
            "source-locked combined stage evidence invalid")
    require(image.get("source_commit")==source_sha and
            image.get("engine_release_verified") is True and
            image.get("bridge_verified") is True and image.get("image_mode") is True,
            "installed image byte verification invalid")
    require(oci.get("source_commit")==source_sha and
            oci.get("sbom_attestation_present") is True and
            oci.get("provenance_attestation_present") is True and
            oci.get("offline_artifact_verified") is True,
            "OCI manifest/SBOM/provenance qualification missing")
    require(online.get("source_commit")==source_sha and
            online.get("harness_qualified") is True and
            online.get("containment_qualified_offline") is True and
            online.get("recovery_qualified_offline") is True and
            online.get("validated_run_count")==10 and
            not online.get("qualification_failures"),
            "actual image offline game/recovery qualification incomplete")
    require(host.get("destination_host_qualified") is False and
            host.get("actual_destination_host_id") is None,
            "unnamed CI worker unexpectedly certified as deployment host")
    require(license.get("status")=="REVIEW_REQUIRED" and
            license.get("publication_allowed") is False and
            license.get("legal_approval") is False,
            "license publication boundary changed")
    require(policy.get("public_bot_release") is False and
            policy.get("public_release_gate") is False,
            "qualification policy grants public authority")
    required=policy.get("required_proofs") or []
    require(len(required)>=8,"release requirement matrix weakened")
    required_cases=policy.get("integration_scenarios") or []
    seen={r.get("scenario"):r for r in runtime_cases}
    require(set(seen)==set(required_cases) and
            all(all(v.get(k) is True for k in (
                "root_read_only","network_none","nonroot_user",
                "bounded_resources","durable_journal_mount","separate_replay_mount"))
                and v.get("source_commit")==source_sha for v in runtime_cases),
            "real image scenario confinement incomplete")
    require(inventory.get("uid")==10001 and
            inventory.get("public_bot_release") is False and
            len(inventory.get("os_packages") or [])>=10,
            "actual OCI runtime package inventory missing")
    return {
        "schema_version":1,"profile_id":"release-1-offline-aggregate-v1",
        "source_commit":source_sha,
        "offline_candidate_verified":True,
        "release_stage_files":stage["inventory_files"],
        "qualified_image_scenarios":len(runtime_cases),
        "qualified_recovery_scenarios":online["validated_run_count"],
        "oci_manifest_digest":oci["oci_manifest_digest"],
        "oci_config_digest":oci["oci_config_digest"],
        "oci_archive_sha256":oci["oci_archive_sha256"],
        "ubuntu_package_inventory_recorded":True,
        "software_bill_of_materials_retained":True,
        "abi_and_package_review_qualified":False,
        "destination_host_qualified":False,
        "controller_license_approved":False,
        "public_distribution_authorized":False,
        "live_transport_qualified":False,
        "public_bot_release":False,
        "promotion_authorized":False,
        "release_blocked":True,
        "outstanding_requirements":[
            "exact package-version source review",
            "operator-selected destination host qualification",
            "combined GPL/AGPL/controller legal review",
            "live Lichess transport/credentials and canary authorization",
            "post-squash-merge source/image requalification"
        ]
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source-sha",required=True)
    p.add_argument("--evidence",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        exact=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
        require(exact==a.source_sha,"aggregate checkout not exact source head")
        stage=read(a.evidence/"stage-verdict.json")
        image=read(a.evidence/"installed-image-verdict.json")
        oci=read(a.evidence/"oci-verdict.json")
        host=read(a.evidence/"host-observation.json")
        online=read(a.evidence/"online-recovery-v2.json")
        inventory=read(a.evidence/"runtime-package-inventory.json")
        license_info=read(ROOT/"qualification/license-compliance-v1.json")
        policy=read(ROOT/"qualification/release-qualification-v1.json")
        runtimes=[read(a.evidence/"runtime"/(case+".json")) for case in policy["integration_scenarios"]]
        result=qualify(source_sha=a.source_sha,stage=stage,image=image,oci=oci,
                      host=host,online=online,license=license_info,
                      policy=policy,runtime_cases=runtimes,inventory=inventory)
        rc=0
    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as e:
        rc=2
        result={"schema_version":1,"source_commit":a.source_sha,
                "offline_candidate_verified":False,
                "promotion_authorized":False,"public_bot_release":False,
                "release_blocked":True,"qualification_error":str(e)}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(rc)

if __name__=="__main__":
    main()
