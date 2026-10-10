#!/usr/bin/env python3
"""Aggregate exact-head ENGINE-OPT-V2 evidence without confusing validity with promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import statistics
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
from tools.engine_opt.constituent_hash import load_policy as load_constituent_policy, qualify_hash_matrix
from tools.engine_opt.g3_b4 import (
    B4_POLICY, WITNESS_CORPUS, file_hash as b4_file_hash, validate_b4_policy,
    require_positive_g3,
)


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


def local1_campaign_pattern(*, candidate_mode: bool) -> str:
    """Select the candidate-era or promoted-canonical campaign explicitly."""
    directory = "local1" if candidate_mode else "canonical-local1"
    return f"engine-opt-v2-profile-domain/**/{directory}/campaign/report.json"


def j12_campaign_pattern() -> str:
    return "engine-opt-v2-profile-domain/**/j12-local1/campaign/report.json"


def validate_j12_campaign_metadata(
    local: dict[str, Any],
    manifest: dict[str, Any],
    prerequisite: dict[str, Any],
    *,
    source: str,
    candidate_bundle: dict[str, Any],
    policy_sha256: str,
) -> None:
    """Reject inconsistent J12 lifecycle evidence without granting hybrid authority."""
    require(local.get("passed") is True, f"J12 LOCAL-1 failed: {local.get('errors')}")
    require(local.get("errors") == [], "J12 lifecycle has retained errors")
    require(local.get("execution_scope") == "required_local1", "J12 lifecycle scope drift")
    require(local.get("validated_games") == 28, "J12 lifecycle did not validate 28 games")
    require(local.get("observed_games") == 28, "J12 lifecycle did not observe 28 games")
    require(local.get("claim_boundary", {}).get("full_game_lifecycle") is True,
            "J12 lifecycle lacks full-game evidence")
    require((manifest.get("source") or {}).get("commit") == source,
            "J12 lifecycle source is not exact head")
    require(manifest.get("status") == "completed" and not manifest.get("failures"),
            "J12 lifecycle manifest is incomplete")
    require(manifest.get("mode") == "required", "J12 lifecycle mode is not required")
    require((manifest.get("policy") or {}).get("path")
            == "qualification/local-full-game-orchestrated-v1.json",
            "J12 lifecycle policy path drift")
    require((manifest.get("policy") or {}).get("sha256") == policy_sha256,
            "J12 lifecycle policy bytes drift")
    require(local.get("candidate_bundle") == candidate_bundle
            and manifest.get("candidate_bundle") == candidate_bundle,
            "J12 lifecycle bundle differs from exact-head candidate")
    require((manifest.get("prerequisites") or []) and
            [row.get("id") for row in manifest["prerequisites"]] == ["engine-opt-v2", "j12"]
            and all(row.get("returncode") == 0 and row.get("timed_out") is False
                    for row in manifest["prerequisites"]),
            "J12 lifecycle prerequisites missing, reordered or failed")
    require(prerequisite.get("schema_version") == 1
            and prerequisite.get("profile_id") == "allfather.orchestrated-v1"
            and prerequisite.get("mechanism_valid") is True
            and prerequisite.get("source_commit") == source
            and prerequisite.get("candidate_bundle") == candidate_bundle,
            "J12 prerequisite mechanism identity is invalid")
    require(prerequisite.get("work_grants_authorized") == 9
            and prerequisite.get("work_grants_settled") == 9
            and prerequisite.get("open_reservations") == 0
            and prerequisite.get("allocation_action") == "BUY_BUNDLE",
            "J12 prerequisite did not settle its frozen WorkGrant mechanism")
    # J12 may validly remain ANCHOR_FALLBACK on a host with synthetic capacity.
    # Its prerequisite's authority_qualified=false is not an invalid lifecycle.


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
        protocol = load_constituent_policy()
        from tools.engine_opt.constituent_hash import load_case_ids
        expected_case_ids = load_case_ids(protocol)
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

        engine_artifacts = (
            ((details.get("candidate") or {}).get("bundle") or {})
            .get("artifacts", {})
            .get("engines", {})
        )
        for family, doc, derived_label in (
            ("lc0", lc0, "allfather-derived"),
            ("reckless", reckless, "allfather-derived"),
            ("stockfish", stockfish, "pgo"),
        ):
            expected_sha = (engine_artifacts.get(family) or {}).get("sha256")
            require(
                isinstance(expected_sha, str)
                and (doc.get("binaries") or {}).get(derived_label, {}).get("sha256") == expected_sha,
                f"{family} A/B derived binary differs from candidate bundle",
            )
        require(
            (sf_hash.get("binary") or {}).get("sha256")
            == (engine_artifacts.get("stockfish") or {}).get("sha256"),
            "Stockfish hash matrix binary differs from candidate bundle",
        )
        require(
            (rr_hash.get("binary") or {}).get("sha256")
            == (engine_artifacts.get("reckless") or {}).get("sha256"),
            "Reckless hash matrix binary differs from candidate bundle",
        )

        def recompute_ab(doc: dict[str, Any]) -> dict[str, Any]:
            rows = doc.get("rows")
            binaries = doc.get("binaries") or {}
            family = doc.get("family")
            expected_labels = {
                "lc0": ("allfather-derived", "pristine-upstream"),
                "reckless": ("allfather-derived", "pristine-upstream"),
                "stockfish": ("pgo", "no-pgo"),
            }.get(family)
            require(
                expected_labels is not None
                and isinstance(rows, list)
                and set(binaries) == set(expected_labels),
                "A/B rows or labels malformed",
            )
            labels = expected_labels
            by_side: dict[str, dict[str, dict[str, Any]]] = {label: {} for label in labels}
            require(len(rows) == len(expected_case_ids) * 2, "A/B row count drift")
            for case_index, case_id in enumerate(expected_case_ids):
                expected_order = labels if case_index % 2 == 0 else tuple(reversed(labels))
                pair = rows[case_index * 2 : case_index * 2 + 2]
                require(
                    [row.get("case_id") for row in pair] == [case_id, case_id]
                    and [row.get("side") for row in pair] == list(expected_order)
                    and [row.get("case_order_index") for row in pair] == [case_index, case_index]
                    and [row.get("side_order_index") for row in pair] == [0, 1],
                    "A/B paired execution order drift",
                )
            expected_options = doc.get("options")
            expected_nodes = doc.get("nodes")
            require(isinstance(expected_options, dict), "A/B options missing")
            require(type(expected_nodes) is int and expected_nodes > 0, "A/B nodes missing")
            for row in rows:
                require(isinstance(row, dict), "A/B row must be an object")
                side = row.get("side")
                case_id = row.get("case_id")
                require(side in by_side and isinstance(case_id, str) and case_id, "A/B row identity malformed")
                require(case_id not in by_side[side], "duplicate A/B case")
                require(row.get("family") == family, "A/B row family drift")
                require(row.get("options") == expected_options, "A/B row options drift")
                require(row.get("nodes_requested") == expected_nodes, "A/B row work target drift")
                require(
                    row.get("binary_sha256") == (binaries.get(side) or {}).get("sha256"),
                    "A/B row binary identity mismatch",
                )
                metrics = row.get("metrics")
                require(isinstance(metrics, dict), "A/B metrics missing")
                wall = metrics.get("wall_ms")
                cpu = metrics.get("cpu_ms")
                move = metrics.get("bestmove")
                require(
                    not isinstance(wall, bool) and isinstance(wall, (int, float))
                    and math.isfinite(float(wall)) and float(wall) > 0,
                    "A/B wall measurement invalid",
                )
                require(
                    not isinstance(cpu, bool) and isinstance(cpu, (int, float))
                    and math.isfinite(float(cpu)) and float(cpu) >= 0,
                    "A/B CPU measurement invalid",
                )
                require(isinstance(move, str) and move, "A/B bestmove missing")
                by_side[side][case_id] = {
                    "wall_ms": float(wall),
                    "cpu_ms": float(cpu),
                    "bestmove": move,
                }
            left, right = labels
            require(set(by_side[left]) == set(by_side[right]) and len(by_side[left]) == 8, "A/B case coverage drift")
            case_ids = sorted(by_side[left])
            left_wall = statistics.median(by_side[left][case]["wall_ms"] for case in case_ids)
            right_wall = statistics.median(by_side[right][case]["wall_ms"] for case in case_ids)
            agreement = sum(
                by_side[left][case]["bestmove"] == by_side[right][case]["bestmove"]
                for case in case_ids
            ) / len(case_ids)
            return {
                "left_label": left,
                "right_label": right,
                "bestmove_agreement": agreement,
                "left_median_wall_ms": float(left_wall),
                "right_median_wall_ms": float(right_wall),
                "right_over_left_wall_ratio": float(right_wall) / float(left_wall),
            }

        lc0_ab = recompute_ab(lc0)
        reckless_ab = recompute_ab(reckless)
        stockfish_ab = recompute_ab(stockfish)

        if lc0_ab["bestmove_agreement"] != 1.0:
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "derived LC0 disagrees with pristine control")
        if not (0.85 <= lc0_ab["right_over_left_wall_ratio"] <= 1.15):
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "derived LC0 left the disabled-feature runtime band")
        if (
            reckless_ab["bestmove_agreement"] != 1.0
            or reckless_ab["left_median_wall_ms"] > 1.10 * reckless_ab["right_median_wall_ms"]
        ):
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "derived Reckless failed its frozen A/B gate")
        if (
            stockfish_ab["bestmove_agreement"] != 1.0
            or stockfish_ab["left_median_wall_ms"] > 1.05 * stockfish_ab["right_median_wall_ms"]
        ):
            fail("NOT_QUALIFIED_CONSTITUENT_REGRESSION", "PGO Stockfish failed its frozen A/B gate")

        sf_hash_result = qualify_hash_matrix(
            sf_hash,
            expected_source_commit=source,
            selected_hash_mb=int((selection.get("selected") or {}).get("stockfish", {}).get("hash_mb")),
            policy=protocol,
        )
        rr_hash_result = qualify_hash_matrix(
            rr_hash,
            expected_source_commit=source,
            selected_hash_mb=int((selection.get("selected") or {}).get("reckless", {}).get("hash_mb")),
            policy=protocol,
        )
        for family, result in (("Stockfish", sf_hash_result), ("Reckless", rr_hash_result)):
            if result["disposition"] == "NOT_QUALIFIED_BEHAVIOR":
                fail("NOT_QUALIFIED_CONSTITUENT_BEHAVIOR", f"{family} hash benchmark changed behavioral output")
            elif result["disposition"] == "NOT_QUALIFIED":
                fail("NOT_QUALIFIED_HASH_EFFICIENCY", f"{family} Hash=16 is outside the frozen 3% efficiency band")
            elif result["disposition"] == "INCONCLUSIVE":
                fail("INCONCLUSIVE_CONSTITUENT_PERFORMANCE", f"{family} Hash=16 efficiency evidence is inconclusive")

        return {
            "lc0": lc0_ab,
            "reckless": reckless_ab,
            "stockfish": stockfish_ab,
            "stockfish_hash": sf_hash_result,
            "reckless_hash": rr_hash_result,
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
            local1_campaign_pattern(candidate_mode=args.candidate_mode),
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
        if not args.candidate_mode:
            validate_b4_policy(repo)
            contracts = g3.get("contracts") or {}
            require(contracts.get("policy_sha256") == b4_file_hash(repo, B4_POLICY)
                    and contracts.get("selection_sha256") == sha256(selection_path)
                    and contracts.get("reference_runtime_sha256") == sha256(repo / "config/allfather.online-engine-opt-v2.json")
                    and contracts.get("hybrid_runtime_sha256") == sha256(repo / "config/allfather.online-hybrid-v2.validation.json")
                    and contracts.get("witnesses_sha256") == b4_file_hash(repo, WITNESS_CORPUS),
                    "canonical G3 prerequisite differs from frozen b4 source contracts")
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
            if args.candidate_mode:
                positive = g3.get("positive_case") or {}
                require(
                    positive.get("authority") == "HYBRID"
                    and positive.get("emitted_move")
                    and positive.get("emitted_move") != positive.get("anchor_move"),
                    "G3-v2 qualified flag lacks a genuine non-anchor witness",
                )
            else:
                require_positive_g3(g3, policy_sha256=b4_file_hash(repo, B4_POLICY))

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
            "g3_policy_sha256": (None if args.candidate_mode else b4_file_hash(repo, B4_POLICY)),
            "lc0_explore_cpu_ms_max": max(explore) if explore else None,
            "lc0_verify_cpu_ms_max": max(verify) if verify else None,
            "lc0_explore_reserved_ms": explore_reserved,
            "lc0_verify_reserved_ms": verify_reserved,
        }

    def check_j12_lifecycle() -> dict[str, Any]:
        report_path = find_one(artifact_root, j12_campaign_pattern())
        local = load(report_path)
        manifest = load(report_path.parent / "manifest.json")
        prerequisite = load(report_path.parent / "prerequisites" / "j12.json")
        candidate = details["candidate"]["bundle"]
        validate_j12_campaign_metadata(
            local, manifest, prerequisite,
            source=source,
            candidate_bundle=candidate,
            policy_sha256=sha256(repo / "qualification/local-full-game-orchestrated-v1.json"),
        )
        local_domain = validate_execution_domain(
            local.get("execution_domain"), expected_source_commit=source,
        )
        manifest_domain = validate_execution_domain(
            manifest.get("execution_domain"), expected_source_commit=source,
        )
        prerequisite_domain = validate_execution_domain(
            prerequisite.get("execution_domain"), expected_source_commit=source,
        )
        require_same_execution_domain(
            {
                "profile_domain": details["execution_domain"],
                "j12_report": local_domain,
                "j12_manifest": manifest_domain,
                "j12_prerequisite": prerequisite_domain,
            },
            expected_source_commit=source,
        )
        return {
            "execution_domain": local_domain,
            "campaign_id": local.get("campaign_id"),
            "validated_games": local.get("validated_games"),
            "mechanism_valid": prerequisite.get("mechanism_valid"),
            "authority_qualified": prerequisite.get("authority_qualified"),
            "qualification_disposition": prerequisite.get("qualification_disposition"),
        }

    invalid_gate("execution_domain", check_domain)
    invalid_gate("candidate", check_candidate)
    invalid_gate("constituent_ab", check_ab)
    if not invalid:
        invalid_gate("lc0", check_lc0)
    if not invalid:
        invalid_gate("local1_g3", check_local1_and_g3)
    if not invalid and not args.candidate_mode:
        invalid_gate("j12_lifecycle", check_j12_lifecycle)

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
        elif "INCONCLUSIVE_CONSTITUENT_PERFORMANCE" in codes:
            disposition = "INCONCLUSIVE_CONSTITUENT_PERFORMANCE"
        elif "NOT_QUALIFIED_HASH_EFFICIENCY" in codes:
            disposition = "NOT_QUALIFIED_HASH_EFFICIENCY"
        elif "NOT_QUALIFIED_CONSTITUENT_BEHAVIOR" in codes:
            disposition = "NOT_QUALIFIED_CONSTITUENT_BEHAVIOR"
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
                evidence_valid
                and (details.get("local1_g3") or {}).get("validated_games") == 28
                and (
                    args.candidate_mode
                    or (details.get("j12_lifecycle") or {}).get("validated_games") == 28
                )
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
