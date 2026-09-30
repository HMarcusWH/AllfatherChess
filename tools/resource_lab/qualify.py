"""Independent completeness/integrity qualifier for J6 resource-lab evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import canonical_digest
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.domain import (
    candidate_bundle_identity,
    validate_execution_domain,
)
from tools.engine_opt.report import sha256, source_identity

from .candidate_matrix import expand_candidates, expand_compositions, load_lab_spec
from .pareto import build_pareto_report


class ResourceLabQualificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceLabQualificationError(message)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def qualify(root: Path, spec_path: Path) -> dict[str, Any]:
    spec = load_lab_spec(spec_path)
    manifest = load(root / "manifest.json")
    require(isinstance(manifest, dict), "manifest must be object")
    source = source_identity(ROOT)
    require(manifest.get("source_commit") == source["commit"], "lab source is not exact head")
    require(manifest.get("attempt_policy") == "single-pass-no-retry-v1", "retry policy drift")
    require(manifest.get("placement_mode") == "observed", "placement mode drift")

    retained_spec = manifest.get("lab_spec") or {}
    require(retained_spec.get("sha256") == sha256(spec_path), "lab spec SHA mismatch")
    require(retained_spec.get("canonical_digest") == spec.digest, "lab spec digest mismatch")
    corpus_path = ROOT / spec.corpus
    cases = load_epd(corpus_path)
    case_ids = tuple(case.case_id for case in cases)
    require(len(cases) == spec.corpus_cases, "corpus case count drift")
    retained_corpus = manifest.get("corpus") or {}
    require(retained_corpus.get("sha256") == sha256(corpus_path), "corpus SHA mismatch")
    require(tuple(retained_corpus.get("cases") or []) == case_ids, "corpus case identity drift")

    domain = validate_execution_domain(
        manifest.get("execution_domain"),
        expected_source_commit=source["commit"],
    )
    bundle_root = ROOT / spec.bundle_root
    candidate_bundle = candidate_bundle_identity(
        bundle_root,
        expected_source_commit=source["commit"],
    )
    require(manifest.get("candidate_bundle") == candidate_bundle, "candidate bundle binding drift")
    build_manifest = load(bundle_root / "build-manifest.json")
    candidates = expand_candidates(spec, build_manifest)
    compositions = expand_compositions(spec, candidates)
    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    composition_map = {row.composition_id: row for row in compositions}

    retained_candidates = load(root / "stage-a/candidates.json")
    require(
        retained_candidates
        == {
            "schema_version": 1,
            "candidates": [candidate.as_dict() for candidate in candidates],
        },
        "retained candidate matrix differs from frozen expansion",
    )
    retained_compositions = load(root / "stage-b/compositions.json")
    require(
        retained_compositions
        == {
            "schema_version": 1,
            "compositions": [row.as_dict() for row in compositions],
        },
        "retained composition matrix differs from frozen expansion",
    )

    rows = load(root / "stage-a/raw/rows.json")
    require(isinstance(rows, list), "Stage-A rows must be array")
    expected_a = {
        (candidate.candidate_id, repeat, case_id)
        for candidate in candidates
        for repeat in range(spec.repeats)
        for case_id in case_ids
    }
    seen_a: set[tuple[str, int, str]] = set()
    completed_a = errors_a = 0
    for row in rows:
        require(isinstance(row, dict), "Stage-A row must be object")
        candidate_id = row.get("candidate_id")
        repeat = row.get("repeat_index")
        case_id = row.get("case_id")
        key = (candidate_id, repeat, case_id)
        require(key in expected_a, f"unexpected Stage-A attempt: {key}")
        require(key not in seen_a, f"retry/duplicate Stage-A attempt: {key}")
        seen_a.add(key)
        candidate = candidate_map[candidate_id]
        require(row.get("candidate_digest") == candidate.digest, f"{candidate_id}: candidate digest drift")
        require(row.get("attempt_index") == 0, f"{candidate_id}: retry index is forbidden")
        require(row.get("nodes") == candidate.nodes, f"{candidate_id}: work budget drift")
        status = row.get("status")
        require(status in ("completed", "error"), f"{candidate_id}: invalid attempt status")
        if status == "completed":
            completed_a += 1
            measurement = row.get("measurement")
            require(isinstance(measurement, dict), f"{candidate_id}: measurement missing")
            require(
                measurement.get("native_work_semantics") == f"{candidate.family}.uci_nodes",
                f"{candidate_id}: native-work semantics drift",
            )
            require(isinstance(measurement.get("bestmove"), str), f"{candidate_id}: bestmove missing")
        else:
            errors_a += 1
            require(isinstance(row.get("error"), str) and row["error"], f"{candidate_id}: error outcome lacks error")
            require(row.get("measurement") is None, f"{candidate_id}: failed attempt carries measurement")
    require(seen_a == expected_a, f"Stage-A attempt set incomplete: missing={len(expected_a-seen_a)}")
    require(len(rows) == len(expected_a) == 1368, "frozen Stage-A attempt count drift")

    recomputed_pareto = build_pareto_report(
        spec=spec,
        candidates=candidates,
        rows=rows,
        case_ids=case_ids,
    )
    retained_pareto = load(root / "stage-a/pareto.json")
    require(
        canonical_digest(retained_pareto) == canonical_digest(recomputed_pareto),
        "retained Pareto analysis does not independently recompute",
    )

    stage_b_rows = load(root / "stage-b/raw/rows.json")
    require(isinstance(stage_b_rows, list), "Stage-B rows must be array")
    expected_b = {
        (composition.composition_id, repeat, case_id)
        for composition in compositions
        for repeat in range(spec.repeats)
        for case_id in case_ids
    }
    seen_b: set[tuple[str, int, str]] = set()
    completed_b = errors_b = 0
    for row in stage_b_rows:
        require(isinstance(row, dict), "Stage-B row must be object")
        key = (row.get("composition_id"), row.get("repeat_index"), row.get("case_id"))
        require(key in expected_b, f"unexpected Stage-B attempt: {key}")
        require(key not in seen_b, f"retry/duplicate Stage-B attempt: {key}")
        seen_b.add(key)
        composition = composition_map[row["composition_id"]]
        require(row.get("composition_digest") == composition.digest, "composition digest drift")
        require(row.get("attempt_index") == 0, "Stage-B retry index is forbidden")
        members = row.get("members")
        require(isinstance(members, list), "Stage-B member evidence missing")
        expected_members = {member.instance: member for member in composition.members}
        require({member.get("instance") for member in members} == set(expected_members), "Stage-B member set drift")
        for member in members:
            expected = expected_members[member["instance"]]
            require(member.get("candidate_id") == expected.candidate_id, "Stage-B candidate identity drift")
            require(member.get("candidate_digest") == expected.candidate_digest, "Stage-B candidate digest drift")
            require(member.get("status") in ("completed", "error"), "Stage-B member status invalid")
        if row.get("status") == "completed":
            completed_b += 1
        else:
            errors_b += 1
    require(seen_b == expected_b, f"Stage-B attempt set incomplete: missing={len(expected_b-seen_b)}")
    require(len(stage_b_rows) == len(expected_b) == 72, "frozen Stage-B batch count drift")

    pareto_ids = sorted(
        candidate_id
        for group in retained_pareto["groups"]
        for candidate_id in group["pareto"]
    )
    return {
        "schema_version": 1,
        "profile_id": "resource-lab-v1-report",
        "source_commit": source["commit"],
        "execution_domain_id": domain["execution_domain_id"],
        "execution_domain_digest": domain["execution_domain_digest"],
        "binding_scope": domain["binding_scope"],
        "evidence_valid": True,
        "lab_complete": True,
        "stage_a_candidates": len(candidates),
        "stage_a_expected_measurements": len(expected_a),
        "stage_a_completed_measurements": completed_a,
        "stage_a_error_measurements": errors_a,
        "stage_a_pareto_candidate_ids": pareto_ids,
        "stage_b_compositions": len(compositions),
        "stage_b_expected_batches": len(expected_b),
        "stage_b_completed_batches": completed_b,
        "stage_b_error_batches": errors_b,
        "promotion_ready": False,
        "promotion_requires": "J7 frozen profile selection",
        "claim_boundary": {
            "resource_measurement": True,
            "profile_selection": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--spec",
        type=Path,
        default=ROOT / "qualification/resource-lab-v1.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = qualify(args.root.resolve(), args.spec.resolve())
    except Exception as exc:
        report = {
            "schema_version": 1,
            "profile_id": "resource-lab-v1-report",
            "evidence_valid": False,
            "lab_complete": False,
            "promotion_ready": False,
            "error": f"{type(exc).__name__}: {exc}",
            "claim_boundary": {
                "resource_measurement": False,
                "profile_selection": False,
                "strength": False,
                "elo": False,
                "equal_compute": False,
                "deployment": False,
            },
        }
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(report, sort_keys=True))
        return 1

    output = args.output or args.root / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
