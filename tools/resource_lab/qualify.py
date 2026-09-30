"""Independent completeness/integrity qualifier for J6 resource-lab evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_proc import ProcessDelta
from controller.decision import canonical_digest
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.domain import (
    candidate_bundle_identity,
    validate_execution_domain,
)
from tools.engine_opt.report import sha256, source_identity

from .candidate_matrix import expand_candidates, expand_compositions, load_lab_spec
from .compose import summarize_composition_interference
from .measure import parse_search_observation
from .pareto import build_pareto_report


class ResourceLabQualificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceLabQualificationError(message)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_attempt_keys(
    rows: list[dict[str, Any]],
    expected: set[tuple[Any, ...]],
    *,
    fields: tuple[str, ...],
    label: str,
) -> set[tuple[Any, ...]]:
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        require(isinstance(row, dict), f"{label} row must be object")
        key = tuple(row.get(field) for field in fields)
        require(key in expected, f"unexpected {label} attempt: {key}")
        require(key not in seen, f"retry/duplicate {label} attempt: {key}")
        require(row.get("attempt_index") == 0, f"{label} retry index is forbidden")
        seen.add(key)
    require(seen == expected, f"{label} attempt set incomplete: missing={len(expected-seen)}")
    return seen


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
    validate_attempt_keys(
        rows,
        expected_a,
        fields=("candidate_id", "repeat_index", "case_id"),
        label="Stage-A",
    )
    completed_a = errors_a = 0
    for row in rows:
        candidate_id = row.get("candidate_id")
        candidate = candidate_map[candidate_id]
        require(row.get("candidate_digest") == candidate.digest, f"{candidate_id}: candidate digest drift")
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
            transcript = row.get("transcript")
            require(isinstance(transcript, list) and transcript, f"{candidate_id}: transcript missing")
            require(isinstance(row.get("affinity_before"), dict), f"{candidate_id}: affinity_before missing")
            require(isinstance(row.get("affinity_after"), dict), f"{candidate_id}: affinity_after missing")
            replayed = parse_search_observation(
                transcript,
                family=candidate.family,
                delta=ProcessDelta(
                    pid=int(measurement["pid"]),
                    start_time_ticks=int(measurement["process_start_time_ticks"]),
                    wall_ms=float(measurement["wall_ms"]),
                    cpu_ms=float(measurement["cpu_ms"]),
                    start_rss_bytes=measurement.get("start_rss_bytes"),
                    end_rss_bytes=measurement.get("end_rss_bytes"),
                    vm_hwm_bytes=measurement.get("vm_hwm_bytes"),
                ),
            ).as_dict()
            for key in ("bestmove", "pv", "evaluation", "native_work_value", "native_work_semantics", "nps"):
                require(
                    replayed.get(key) == measurement.get(key),
                    f"{candidate_id}: transcript/measurement {key} mismatch",
                )
        else:
            errors_a += 1
            require(isinstance(row.get("error"), str) and row["error"], f"{candidate_id}: error outcome lacks error")
            require(row.get("measurement") is None, f"{candidate_id}: failed attempt carries measurement")
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
    validate_attempt_keys(
        stage_b_rows,
        expected_b,
        fields=("composition_id", "repeat_index", "case_id"),
        label="Stage-B",
    )
    completed_b = errors_b = 0
    for row in stage_b_rows:
        composition = composition_map[row["composition_id"]]
        require(row.get("composition_digest") == composition.digest, "composition digest drift")
        members = row.get("members")
        require(isinstance(members, list), "Stage-B member evidence missing")
        expected_members = {member.instance: member for member in composition.members}
        require({member.get("instance") for member in members} == set(expected_members), "Stage-B member set drift")
        for member in members:
            expected = expected_members[member["instance"]]
            require(member.get("candidate_id") == expected.candidate_id, "Stage-B candidate identity drift")
            require(member.get("candidate_digest") == expected.candidate_digest, "Stage-B candidate digest drift")
            require(member.get("status") in ("completed", "error"), "Stage-B member status invalid")
            if member["status"] == "completed":
                measurement = member.get("measurement")
                require(isinstance(measurement, dict), "completed Stage-B member lacks measurement")
                require(isinstance(member.get("transcript"), list) and member["transcript"], "completed Stage-B member lacks transcript")
                require(isinstance(member.get("affinity_before"), dict), "completed Stage-B member lacks affinity_before")
                require(isinstance(member.get("affinity_after"), dict), "completed Stage-B member lacks affinity_after")
            else:
                require(member.get("measurement") is None, "failed Stage-B member carries measurement")
                require(isinstance(member.get("error"), str) and member["error"], "failed Stage-B member lacks error")
        require(row.get("status") in ("completed", "error"), "Stage-B batch status invalid")
        if row.get("status") == "completed":
            completed_b += 1
            require(all(member["status"] == "completed" for member in members), "completed Stage-B batch contains failed member")
        else:
            errors_b += 1
            require(any(member["status"] == "error" for member in members), "failed Stage-B batch has no failed member")
    require(len(stage_b_rows) == len(expected_b) == 72, "frozen Stage-B batch count drift")

    retained_stage_b_summary = load(root / "stage-b/summary.json")
    recomputed_stage_b_summary = summarize_composition_interference(
        rows=stage_b_rows,
        isolated_rows=rows,
    )
    require(
        canonical_digest(retained_stage_b_summary)
        == canonical_digest(recomputed_stage_b_summary),
        "retained Stage-B interference summary does not independently recompute",
    )

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
