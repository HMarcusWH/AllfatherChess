#!/usr/bin/env python3
"""Aggregate exact-head ENGINE-OPT-V2 evidence without confusing validity with promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from tools.engine_opt.domain import (
    candidate_bundle_identity,
    require_same_execution_domain,
    validate_execution_domain,
)
from tools.engine_opt.matrix_qualification import qualify_lc0_matrix


class QualificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"{path}: cannot load JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path}: JSON root must be an object")
    return value


def find_one(root: Path, pattern: str) -> Path:
    found = sorted(root.glob(pattern))
    require(len(found) == 1, f"{pattern}: expected one file, found {len(found)}")
    return found[0]


def current_source(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--selection",
        type=Path,
        default=ROOT / "qualification/engine-opt-v2-selection.json",
    )
    parser.add_argument(
        "--candidate-report-pattern",
        default="engine-opt-v2-candidate/**/test-results/engine-opt-v2/report.json",
    )
    parser.add_argument("--candidate-mode", action="store_true")
    args = parser.parse_args()

    artifact_root = args.root.resolve()
    repo = ROOT
    source = current_source(repo)
    selection_path = args.selection if args.selection.is_absolute() else repo / args.selection
    selection = load(selection_path)

    invalid: list[str] = []
    qualification_failures: list[dict[str, str]] = []
    details: dict[str, Any] = {}

    def invalid_gate(name: str, work) -> None:
        try:
            details[name] = work()
        except Exception as exc:
            invalid.append(f"{name}: {type(exc).__name__}: {exc}")

    def fail(code: str, message: str) -> None:
        qualification_failures.append({"code": code, "message": message})

    def check_domain() -> dict[str, Any]:
        path = find_one(
            artifact_root,
            "engine-opt-v2-profile-domain/**/execution-domain.json",
        )
        domain = validate_execution_domain(
            load(path),
            expected_source_commit=source,
        )
        return domain

    def check_candidate() -> dict[str, Any]:
        report = load(find_one(artifact_root, args.candidate_report_pattern))
        require(report.get("source_commit") == source, "candidate report source is not exact head")
        require(report.get("passed") is True, "candidate identity report did not pass")
        require(report.get("candidate_identity_valid") is True, "candidate identity is not valid")
        candidate = report.get("candidate_bundle")
        require(isinstance(candidate, dict), "candidate bundle identity missing from report")
        require(candidate.get("source_commit") == source, "candidate bundle source is not exact head")
        require(
            report.get("bundle_manifest_sha256") == candidate.get("build_manifest_sha256"),
            "candidate report/build-manifest identity is internally inconsistent",
        )
        if args.candidate_mode:
            require(report.get("canonical_profile_changed") is False,
                    "candidate report claims the canonical profile changed")
            overlay=report.get("candidate_overlay") or {}
            expected_overlay={
                "selection_sha256":sha256(selection_path),
                "evidence_sha256":sha256(repo/"qualification/engine-opt-v2-candidate-evidence.json"),
                "reference_runtime_sha256":sha256(repo/"config/allfather.online-engine-opt-v2.candidate.json"),
                "hybrid_runtime_sha256":sha256(repo/"config/allfather.online-hybrid-v2.candidate.validation.json"),
                "g3_policy_sha256":sha256(repo/"qualification/online-hybrid-v2-candidate.json"),
                "g3_witnesses_sha256":sha256(repo/"qualification/online-hybrid-v2-candidate-witnesses.json"),
                "g3_discovery_source_sha256":sha256(repo/"qualification/g3-candidate-discovery-source.json"),
            }
            require(overlay==expected_overlay,
                    "candidate report overlay hashes differ from source-controlled candidate inputs")
        return {
            "bundle": candidate,
            "bundle_manifest_sha256": report.get("bundle_manifest_sha256"),
            "evidence_sha256": report.get("evidence_sha256"),
            "candidate_overlay": report.get("candidate_overlay"),
            "promotion_ready": report.get("promotion_ready"),
            "promotion_requires": report.get("promotion_requires"),
        }

    def check_ab() -> dict[str, Any]:
        root = artifact_root / "engine-opt-v2-constituent-ab"
        lc0 = load(find_one(root, "**/lc0-derived-vs-pristine.json"))
        reckless = load(find_one(root, "**/reckless-derived-vs-pristine.json"))
        stockfish = load(find_one(root, "**/stockfish-pgo-vs-no-pgo.json"))
        sf_hash = load(find_one(root, "**/stockfish-hash-matrix.json"))
        rr_hash = load(find_one(root, "**/reckless-hash-matrix.json"))
        for label, doc in (
            ("LC0 A/B", lc0),
            ("Reckless A/B", reckless),
            ("Stockfish PGO A/B", stockfish),
            ("Stockfish hash", sf_hash),
            ("Reckless hash", rr_hash),
        ):
            require((doc.get("source") or {}).get("commit") == source, f"{label} source is not exact head")
            require(not doc.get("errors"), f"{label} contains execution errors")

        lc0_agreement = (lc0.get("comparison") or {}).get("bestmove_agreement")
        lc0_ratio = float(lc0["comparison"]["wall_ratio_right_over_left"])
        rr_agreement = (reckless.get("comparison") or {}).get("bestmove_agreement")
        sf_agreement = (stockfish.get("comparison") or {}).get("bestmove_agreement")
        rr_left = float(reckless["comparison"]["left_median_wall_ms"])
        rr_right = float(reckless["comparison"]["right_median_wall_ms"])
        sf_left = float(stockfish["comparison"]["left_median_wall_ms"])
        sf_right = float(stockfish["comparison"]["right_median_wall_ms"])
        sf16 = float(sf_hash["summaries"]["16"]["median_wall_ms"])
        sf_best = min(float(row["median_wall_ms"]) for row in sf_hash["summaries"].values())
        rr16 = float(rr_hash["summaries"]["16"]["median_wall_ms"])
        rr_best = min(float(row["median_wall_ms"]) for row in rr_hash["summaries"].values())

        if lc0_agreement != 1.0:
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "derived LC0 disagrees with pristine control")
        if not (0.85 <= lc0_ratio <= 1.15):
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "derived LC0 left the disabled-feature runtime band")
        if rr_agreement != 1.0 or rr_left > 1.10 * rr_right:
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "derived Reckless failed its frozen A/B gate")
        if sf_agreement != 1.0 or sf_left > 1.05 * sf_right:
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "PGO Stockfish failed its frozen A/B gate")
        if sf16 > 1.03 * sf_best:
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "Stockfish Hash=16 left the 3% efficiency band")
        if rr16 > 1.03 * rr_best:
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "Reckless Hash=16 left the 3% efficiency band")

        return {
            "lc0_bestmove_agreement": lc0_agreement,
            "lc0_pristine_over_derived_wall_ratio": lc0_ratio,
            "reckless_bestmove_agreement": rr_agreement,
            "stockfish_pgo_bestmove_agreement": sf_agreement,
            "stockfish_hash16_over_best": sf16 / sf_best,
            "reckless_hash16_over_best": rr16 / rr_best,
        }

    def check_lc0() -> dict[str, Any]:
        matrix_name = "candidate-lc0.json" if args.candidate_mode else "canonical-lc0.json"
        matrix = load(
            find_one(
                artifact_root,
                f"engine-opt-v2-profile-domain/**/matrix/{matrix_name}",
            )
        )
        result = qualify_lc0_matrix(
            matrix=matrix,
            selection=selection,
            expected_source_commit=source,
            expected_execution_domain=details["execution_domain"],
            candidate_bundle=details["candidate"]["bundle"],
        )
        for row in result["qualification_failures"]:
            fail(row["code"], row["message"])
        return result["details"]

    def check_local1_and_g3() -> dict[str, Any]:
        report_path = find_one(
            artifact_root,
            "engine-opt-v2-profile-domain/**/local1/campaign/report.json",
        )
        local = load(report_path)
        manifest = load(report_path.parent / "manifest.json")
        require(local.get("passed") is True, f"LOCAL-1-v2 did not pass: {local.get('errors')}")
        require(local.get("execution_scope") == "required_local1", "LOCAL-1-v2 scope is not required_local1")
        require(local.get("validated_games") == 28, "LOCAL-1-v2 did not validate all 28 required games")
        require((manifest.get("source") or {}).get("commit") == source, "LOCAL-1-v2 source is not exact head")

        local_domain = validate_execution_domain(
            local.get("execution_domain"),
            expected_source_commit=source,
        )
        manifest_domain = validate_execution_domain(
            manifest.get("execution_domain"),
            expected_source_commit=source,
        )
        require_same_execution_domain(
            {
                "profile_domain": details["execution_domain"],
                "local1_report": local_domain,
                "local1_manifest": manifest_domain,
            },
            expected_source_commit=source,
        )

        candidate = details["candidate"]["bundle"]
        require(local.get("candidate_bundle") == candidate, "LOCAL-1-v2 candidate binding mismatch")
        require(manifest.get("candidate_bundle") == candidate, "LOCAL-1-v2 manifest candidate binding mismatch")

        g3_path = report_path.parent / "prerequisites" / "g3-v2.json"
        require(g3_path.is_file(), "retained G3-v2 prerequisite report missing")
        g3 = load(g3_path)
        require(g3.get("evidence_valid") is True, "G3-v2 evidence is invalid")
        require(g3.get("source_commit") == source, "G3-v2 source is not exact head")
        require(g3.get("candidate_bundle") == candidate, "G3-v2 candidate binding mismatch")
        if args.candidate_mode:
            overlay=(details.get("candidate") or {}).get("candidate_overlay") or {}
            contracts=g3.get("contracts") or {}
            require(
                contracts.get("policy_sha256")==overlay.get("g3_policy_sha256")
                and contracts.get("selection_sha256")==overlay.get("selection_sha256")
                and contracts.get("reference_runtime_sha256")==overlay.get("reference_runtime_sha256")
                and contracts.get("hybrid_runtime_sha256")==overlay.get("hybrid_runtime_sha256")
                and contracts.get("witnesses_sha256")==overlay.get("g3_witnesses_sha256"),
                "G3-v2 report is not bound to the source-controlled candidate overlay",
            )
        g3_domain = validate_execution_domain(
            g3.get("execution_domain"),
            expected_source_commit=source,
        )
        require_same_execution_domain(
            {
                "profile_domain": details["execution_domain"],
                "g3_v2": g3_domain,
                "local1": local_domain,
            },
            expected_source_commit=source,
        )

        if g3.get("authority_qualified") is not True:
            fail("NOT_QUALIFIED_AUTHORITY", "G3-v2 produced valid evidence but no frozen non-anchor HYBRID witness")
        else:
            positive = g3.get("positive_case") or {}
            require(
                positive.get("authority") == "HYBRID"
                and positive.get("emitted_move")
                and positive.get("emitted_move") != positive.get("anchor_move"),
                "G3-v2 qualified flag lacks a genuine non-anchor witness",
            )

        resource_docs = [
            load(path)
            for path in sorted(
                (report_path.parent / "prerequisites" / "g3-v2-replays").glob("**/resource.json")
            )
        ]
        require(resource_docs, "retained G3-v2 evidence contains no resource reports")
        explore: list[float] = []
        verify: list[float] = []
        for doc in resource_docs:
            for stage in doc.get("stages") or []:
                if stage.get("instance") != "lc0-shadow" or not stage.get("complete"):
                    continue
                cpu = stage.get("cpu_ms")
                require(
                    isinstance(cpu, (int, float))
                    and not isinstance(cpu, bool)
                    and math.isfinite(float(cpu))
                    and float(cpu) >= 0,
                    "G3-v2 LC0 stage CPU measurement is missing or invalid",
                )
                if stage.get("phase") == "EXPLORE":
                    explore.append(float(cpu))
                elif stage.get("phase") in {"VERIFY", "VERIFY_EXTENSION", "STAGED_VERIFY"}:
                    verify.append(float(cpu))
        if g3.get("authority_qualified") is True:
            require(explore and verify,
                    "qualified G3-v2 witness lacks LC0 EXPLORE/VERIFY measurements")

        estimates = ((selection.get("selected") or {}).get("resource_estimates_ms") or {})
        explore_reserved = float((estimates.get("explore") or {}).get("lc0"))
        verify_reserved = float((estimates.get("verify") or {}).get("lc0"))
        if explore and max(explore) > explore_reserved:
            fail("NOT_QUALIFIED_RESOURCE_BOUND", "LC0 EXPLORE CPU exceeded the frozen reservation")
        if verify and max(verify) > verify_reserved:
            fail("NOT_QUALIFIED_RESOURCE_BOUND", "LC0 VERIFY CPU exceeded the frozen reservation")

        return {
            "execution_domain": local_domain,
            "campaign_id": local.get("campaign_id"),
            "validated_games": local.get("validated_games"),
            "authority_counts": local.get("authority_counts"),
            "actual_anchor_overrides": local.get("actual_anchor_overrides"),
            "g3_authority_qualified": g3.get("authority_qualified"),
            "g3_positive_case": g3.get("positive_case"),
            "lc0_explore_cpu_ms_max": max(explore) if explore else None,
            "lc0_verify_cpu_ms_max": max(verify) if verify else None,
            "lc0_explore_reserved_ms": explore_reserved,
            "lc0_verify_reserved_ms": verify_reserved,
        }

    invalid_gate("execution_domain", check_domain)
    invalid_gate("candidate", check_candidate)
    invalid_gate("constituent_ab", check_ab)
    if not invalid:
        invalid_gate("lc0", check_lc0)
    if not invalid:
        invalid_gate("local1_g3", check_local1_and_g3)

    evidence_valid = not invalid
    profile_qualified = bool(evidence_valid and not qualification_failures)

    if not evidence_valid:
        disposition = "INVALID_EVIDENCE"
    elif profile_qualified:
        scope = details["execution_domain"]["binding_scope"]
        disposition = (
            "QUALIFIED_REUSABLE_DOMAIN"
            if scope == "reusable_host_domain"
            else "QUALIFIED_EXACT_HOST_ONLY"
        )
    else:
        codes = {row["code"] for row in qualification_failures}
        if "NOT_QUALIFIED_REPEATABILITY" in codes:
            disposition = "NOT_QUALIFIED_REPEATABILITY"
        elif "NOT_QUALIFIED_BEHAVIORAL_EQUIVALENCE" in codes:
            disposition = "NOT_QUALIFIED_BEHAVIORAL_EQUIVALENCE"
        elif "NOT_QUALIFIED_RESOURCE_BOUND" in codes:
            disposition = "NOT_QUALIFIED_RESOURCE_BOUND"
        elif "NOT_QUALIFIED_AUTHORITY" in codes:
            disposition = "NOT_QUALIFIED_AUTHORITY"
        else:
            disposition = "NOT_QUALIFIED_CONSTITUENT_REGRESSION"

    report = {
        "schema_version": 2,
        "profile_id": "engine-opt-v2-candidate-aggregate" if args.candidate_mode else "engine-opt-v2-aggregate",
        "qualification_scope": "candidate_overlay" if args.candidate_mode else "canonical_profile",
        "source_commit": source,
        "evidence_valid": evidence_valid,
        "candidate_qualified": profile_qualified if args.candidate_mode else None,
        "promotion_ready": profile_qualified if args.candidate_mode else None,
        "canonical_profile_changed": False if args.candidate_mode else None,
        "profile_qualified": False if args.candidate_mode else profile_qualified,
        "passed": profile_qualified,
        "qualification_disposition": disposition,
        "invalid_evidence": invalid,
        "qualification_failures": qualification_failures,
        "details": details,
        "claim_boundary": {
            "engine_profile_qualified": False if args.candidate_mode else profile_qualified,
            "candidate_overlay_qualified": profile_qualified if args.candidate_mode else False,
            "full_game_lifecycle": bool(
                evidence_valid and (details.get("local1_g3") or {}).get("validated_games") == 28
            ),
            "generic_host_portability": bool(
                profile_qualified
                and (details.get("execution_domain") or {}).get("generic_host_portability_eligible") is True
            ),
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    # A valid negative qualification result is successful evidence collection.
    return 0 if evidence_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
