#!/usr/bin/env python3
"""Fail-closed consistency check for CURRENT_STATUS and active source policies.

This is a source-derived *mechanism/current-status* contract, not a new
qualification result, actual host observation or permission to release.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONTRACT="qualification/current-status-v1.json"


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError("current status drift: "+message)


def read(root: Path, relative: str) -> dict:
    path=root/relative
    require(path.is_file() and not path.is_symlink() and
            path.resolve().is_relative_to(root.resolve()),
            "missing/linked policy "+relative)
    document=json.loads(path.read_text(encoding="utf-8"))
    require(type(document) is dict,"invalid policy document")
    return document


def check(root: Path = ROOT) -> dict:
    spec=read(root,CONTRACT)
    require(spec.get("schema_version")==1 and
            spec.get("status_kind")=="current-repository-mechanism-state" and
            spec.get("historical_promotion_pr")==64 and
            spec.get("offline_assurance_pr")==71,
            "unrecognized current status contract")
    expected=spec.get("canonical_lc0_matrix_profile")
    require(type(expected) is str and expected=="b4-p0-c256k-cold",
            "canonical b4 profile identity changed without new contract")
    params=spec.get("canonical_lc0_options")
    require(type(params) is dict and params=={
        "Backend":"blas","NNCacheSize":262144,
        "MinibatchSize":4,"MaxPrefetch":0,"AdaptivePrefetch":False},
        "canonical LC0 parameter contract altered")
    selected=read(root,spec["canonical_selection"])
    lc0=selected.get("selected",{}).get("lc0",{})
    require(selected.get("status")=="selected" and
            lc0.get("matrix_profile")==expected and
            lc0.get("backend")==params["Backend"] and
            lc0.get("nn_cache_size")==params["NNCacheSize"] and
            lc0.get("minibatch_size")==params["MinibatchSize"] and
            lc0.get("max_prefetch")==params["MaxPrefetch"] and
            lc0.get("adaptive_prefetch") is False,
            "canonical engine-opt selection no longer matches b4")
    catalog=read(root,spec["canonical_resource_catalog"])
    profiles=[p for p in catalog.get("profiles",[])
              if type(p) is dict and p.get("family")=="lc0"]
    require(len(profiles)==1 and
            catalog.get("qualification_snapshot",{}).get("qualification_disposition")==
               "QUALIFIED_EXACT_HOST_ONLY",
            "canonical LC0 resource catalog missing/changed")
    option_list=profiles[0].get("options")
    require(type(option_list) is list,
            "canonical resource profile options missing")
    options={row.get("name"):row.get("value") for row in option_list
             if type(row) is dict and row.get("phase") is None}
    for key,value in params.items():
        require(options.get(key)==value,
                "canonical resource option differs from engine selection")
    runtime=read(root,spec["canonical_runtime"])
    instance=runtime.get("instances",{}).get("lc0-shadow",{})
    runtime_options=instance.get("options",{})
    require(runtime.get("hybrid_authority",{}).get("enabled") is True and
            runtime.get("hybrid_authority",{}).get("policy")==
                "clocked_staged_preanchor_v1" and
            all(runtime_options.get(key)==value
                for key,value in params.items()),
            "canonical hybrid runtime differs from selected profile")
    image=read(root,spec["release_image_policy"])
    qualification=read(root,spec["release_qualification_policy"])
    legal=read(root,spec["distribution_policy"])
    for field in ("public_bot_release","network_recovery_qualified",
                  "deployment_host_qualified","playing_strength_qualified",
                  "os_package_lock_complete","wheel_lock_complete"):
        require(image.get(field) is False,
                "image policy unexpectedly raised "+field)
    for field in ("public_bot_release","offline_candidate_qualified",
                  "destination_host_qualified","artifact_distribution_authorized",
                  "live_transport_qualified","release_promotion_authorized"):
        require(qualification.get(field) is False,
                "release policy unexpectedly raised "+field)
    require(legal.get("controller_distribution_license_approved") is False and
            legal.get("artifact_distribution_authorized") is False and
            legal.get("legal_review_completed") is False,
            "controller license review was promoted")
    for field in ("public_bot_release","offline_candidate_qualified",
                  "playing_strength_qualified","artifact_distribution_authorized",
                  "destination_host_qualified","network_recovery_qualified"):
        require(spec.get(field) is False, "status contract promoted "+field)
    summary=root/spec["summary_file"]
    require(summary.is_file() and not summary.is_symlink(),
            "current status document missing/linked")
    header="\n".join(summary.read_text(encoding="utf-8").splitlines()[:22])
    for token in (expected,"PR #64","PR #71",
                  "current canonical","offline-only",
                  "no public bot release"):
        require(token in header, "CURRENT_STATUS header missing "+token)
    return {
        "schema_version":1,
        "canonical_lc0_matrix_profile":expected,
        "current_selection_matches_catalog":True,
        "current_runtime_options_bound":True,
        "status_document_synchronized":True,
        "source_claims_only":True,
        "offline_candidate_qualified":False,
        "playing_strength_qualified":False,
        "public_bot_release":False,
        "artifact_distribution_authorized":False
    }


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=ROOT)
    args=p.parse_args()
    try:
        print(json.dumps(check(args.root.resolve()),sort_keys=True))
        return 0
    except (OSError,KeyError,TypeError,ValueError,
            json.JSONDecodeError) as exc:
        p.exit(2,"current source/status check refused: "+type(exc).__name__+"\n")


if __name__=="__main__":
    raise SystemExit(main())
