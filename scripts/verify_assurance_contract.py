#!/usr/bin/env python3
"""Independently reconstruct necessary boolean implications in offline verdicts.

A green bit is NOT authorization. The JSON contract enumerates prerequisites;
these verifications reject internally inconsistent positive claims after all
individual source, game, journal and OCI validators have run.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/"qualification/assurance-contract-v1.json"


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError("assurance claim contract: "+reason)


def validate_contract() -> dict:
    contract=json.loads(POLICY.read_text(encoding="utf-8"))
    require(contract.get("schema_version")==1 and
            contract.get("public_bot_release") is False and
            contract.get("authority",{}).get("public_distribution") is False,
            "policy permits public authority")
    for claim in ("harness_qualified","containment_qualified_offline",
                  "recovery_qualified_offline","offline_smoke_qualified",
                  "oci_sbom_verified","oci_provenance_verified",
                  "image_integrity_qualified","offline_candidate_qualified"):
        item=contract.get("claims",{}).get(claim)
        require(type(item) is dict and type(item.get("requires")) is list
                and bool(item["requires"]), "missing dependencies for "+claim)
    return contract


def verify_report(report: dict, kind: str) -> dict:
    validate_contract()
    require(type(report) is dict and
            report.get("public_bot_release") is False and
            report.get("network_recovery_qualified") is not True and
            report.get("artifact_distribution_authorized") is not True,
            "release authority was raised")
    if kind=="online4a":
        valid=report.get("harness_qualified")
        rows=report.get("scenario_results")
        failures=report.get("harness_failures")
        if valid:
            require(type(rows) is list and type(failures) is list and not failures and
                    report.get("required_run_count")==len(rows) and
                    report.get("validated_run_count")==len(rows) and
                    all(type(r) is dict and r.get("harness_integrity") is True
                        for r in rows), "ONLINE-4A harness lacks full evidence")
            require(report.get("recovery_behavior_qualified") is False,
                    "unsafe unguarded bridge was called recovered")
        return {"claim":"harness_qualified","consistent":True}
    if kind=="online4b":
        rows=report.get("scenario_results")
        failures=report.get("qualification_failures")
        harness=report.get("harness_qualified") is True
        if harness:
            require(type(rows) is list and type(failures) is list and not failures and
                    len(rows)==report.get("required_run_count") and
                    len(rows)==report.get("validated_run_count") and
                    all(type(r) is dict and r.get("harness_valid") is True
                        for r in rows), "ONLINE-4B harness lacks full evidence")
        if report.get("containment_qualified_offline") is True:
            require(harness and all(r.get("status") in (
                    "NORMAL_COMPLETION","SAFE_CONTINUATION","RECOVERED",
                    "SAFE_SUSPENSION") for r in rows),
                    "ONLINE-4B containment has unsafe or unverified outcome")
        if report.get("recovery_qualified_offline") is True:
            require(report.get("containment_qualified_offline") is True,
                    "offline recovery without containment")
            needed=("R02","R03","R08","R13")
            require(all(any(r.get("scenario")==name and
                            r.get("status")=="RECOVERED" and
                            r.get("recovered") is True for r in rows)
                        for name in needed), "missing fresh recovery witness")
        return {"claim":"recovery_qualified_offline","consistent":True}
    if kind=="release_smoke":
        if report.get("offline_smoke_qualified") is True:
            cases=report.get("verified_cases")
            require(type(cases) is dict and set(cases)=={
                "r00-white","r00-black","r02-white","r10-white"} and
                report.get("failures")==[] and
                all(type(row) is dict and
                    row.get("independent_qualified") is True and
                    row.get("persisted_sqlite_verified") is True
                    for row in cases.values()),
                "RELEASE-1 smoke lacks independent chess/SQLite evidence")
        return {"claim":"offline_smoke_qualified","consistent":True}
    if kind=="release_aggregate":
        require(report.get("release_blocked") is True and
                report.get("release_promotion_authorized") is False and
                report.get("license_approved") is False and
                report.get("destination_host_qualified") is False,
                "RELEASE-1 no-publication boundary changed")
        if report.get("image_integrity_qualified") is True:
            import re
            evidence=report.get("oci_evidence")
            require(type(evidence) is dict and
                    all(evidence.get(key) is True for key in (
                        "integrity_verified","oci_layers_verified",
                        "oci_diff_ids_verified",
                        "oci_application_identity_verified",
                        "oci_attestation_references_verified",
                        "oci_attestation_subjects_verified",
                        "oci_sbom_verified","oci_provenance_verified")) and
                    type(report.get("oci_archive_sha256")) is str and
                    re.fullmatch(r"[0-9a-f]{64}",
                                 report["oci_archive_sha256"]) is not None and
                    type(report.get("image_manifest_digest")) is str and
                    re.fullmatch(r"sha256:[0-9a-f]{64}",
                                 report["image_manifest_digest"]) is not None,
                    "image integrity missing OCI archive, SBOM or provenance proof")
            smoke=report.get("smoke")
            require(type(smoke) is dict and set(smoke)=={
                "r00-white","r00-black","r02-white","r10-white"} and
                all(type(row) is dict and row.get("passed") is True and
                    type(row.get("independent_evidence")) is dict and
                    row["independent_evidence"].get("independent_qualified") is True
                    for row in smoke.values()),
                    "image integrity lacks verified packaged games")
        if report.get("offline_candidate_qualified") is True:
            require(not report.get("blockers") and
                    report.get("frozen_dependency_lock_qualified") is True and
                    report.get("license_approved") is True and
                    report.get("destination_host_qualified") is True and
                    report.get("network_recovery_qualified") is True,
                    "candidate green without authorization prerequisites")
        return {"claim":"image_integrity_qualified","consistent":True}
    raise ValueError("unknown assurance verdict kind")


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--kind",required=True,choices=(
        "online4a","online4b","release_smoke","release_aggregate"))
    ap.add_argument("--report",type=Path,required=True)
    args=ap.parse_args()
    verdict=verify_report(json.loads(args.report.read_text()),args.kind)
    print(json.dumps(verdict,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
