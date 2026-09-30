"""Independent completeness/integrity qualifier for J6 resource-lab evidence."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import canonical_digest
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.domain import (
    candidate_bundle_identity,
    validate_execution_domain,
)
from tools.engine_opt.report import sha256, source_identity

from .candidate_matrix import (
    Candidate,
    expand_candidates,
    expand_compositions,
    load_lab_spec,
    stage_a_attempt_plan,
    stage_b_attempt_plan,
    validate_reference_contract,
)
from .compose import summarize_composition_interference
from .measure import (
    parse_search_observation,
    reconstruct_physical_measurement,
)
from .pareto import build_pareto_report


class ResourceLabQualificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceLabQualificationError(message)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_attempt_order(
    rows: list[dict[str, Any]],
    expected_plan: tuple[dict[str, Any], ...],
    *,
    identity_field: str,
    label: str,
) -> None:
    require(len(rows) == len(expected_plan), f"{label} attempt count differs from plan")
    for index, (row, expected) in enumerate(zip(rows, expected_plan)):
        require(isinstance(row, dict), f"{label} row {index} must be object")
        for field in (
            identity_field,
            "repeat_index",
            "case_id",
            "block_index",
            "order_index",
            "attempt_ordinal",
        ):
            require(
                row.get(field) == expected.get(field),
                f"{label} row {index} {field} differs from blocked-cyclic plan",
            )
        require(row.get("attempt_index") == 0, f"{label} retry index is forbidden")


def _validate_affinity_observation(
    raw: Any,
    *,
    measurement: Mapping[str, Any],
    policy: str,
    max_attempts: int,
    label: str,
) -> bool:
    require(isinstance(raw, dict), f"{label}: affinity observation missing")
    require(raw.get("policy") == policy, f"{label}: affinity policy drift")
    require(raw.get("status") in ("completed", "incomplete"), f"{label}: affinity status invalid")
    attempts = raw.get("attempts")
    require(
        not isinstance(attempts, bool)
        and isinstance(attempts, int)
        and 1 <= attempts <= max_attempts,
        f"{label}: affinity attempt count invalid",
    )
    require(raw.get("root_pid") == measurement.get("pid"), f"{label}: affinity PID mismatch")
    require(
        raw.get("root_start_time_ticks")
        == measurement.get("process_start_time_ticks"),
        f"{label}: affinity process identity mismatch",
    )
    require(
        raw.get("authority")
        == {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        },
        f"{label}: affinity authority marker invalid",
    )
    faults = raw.get("faults")
    require(isinstance(faults, list), f"{label}: affinity faults must be array")
    observation = raw.get("observation")
    if raw["status"] == "completed":
        require(isinstance(observation, dict), f"{label}: completed affinity lacks observation")
        require(observation.get("enforced") is False, f"{label}: observed affinity claims enforcement")
        require(observation.get("root_pid") == measurement.get("pid"), f"{label}: observed affinity PID mismatch")
        require(
            observation.get("root_start_time_ticks")
            == measurement.get("process_start_time_ticks"),
            f"{label}: observed affinity start-time mismatch",
        )
        return bool(faults)
    require(observation is None, f"{label}: incomplete affinity must not fabricate observation")
    require(bool(faults), f"{label}: incomplete affinity lacks retained sensor fault")
    return True


def _reconstruct_measurement(
    row: Mapping[str, Any],
    *,
    candidate: Candidate,
    clock_ticks_per_second: int,
    cpu_policy: Mapping[str, Any],
    affinity_policy: Mapping[str, Any],
    label: str,
) -> tuple[dict[str, Any], int]:
    measurement = row.get("measurement")
    require(isinstance(measurement, dict), f"{label}: measurement missing")
    primitives = row.get("physical_primitives")
    require(isinstance(primitives, dict), f"{label}: physical primitives missing")
    physical = reconstruct_physical_measurement(
        primitives,
        clock_ticks_per_second=clock_ticks_per_second,
        required_cpu_method=str(cpu_policy["required_method"]),
        max_cpu_resolution_ns=int(cpu_policy["max_resolution_ns"]),
    )
    transcript = row.get("transcript")
    require(isinstance(transcript, list) and transcript, f"{label}: transcript missing")
    reconstructed = parse_search_observation(
        transcript,
        family=candidate.family,
        physical=physical,
    ).as_dict()
    require(
        reconstructed == measurement,
        f"{label}: producer measurement does not reconstruct from primitives/transcript",
    )
    faults = 0
    for field in ("affinity_observation_before", "affinity_observation_after"):
        faults += int(
            _validate_affinity_observation(
                row.get(field),
                measurement=reconstructed,
                policy=str(affinity_policy["policy"]),
                max_attempts=int(affinity_policy["max_attempts"]),
                label=f"{label}/{field}",
            )
        )
    return reconstructed, faults


def _recompute_aggregate(members: list[dict[str, Any]]) -> tuple[float | None, dict[str, Any]]:
    completed = [
        member["measurement"]
        for member in members
        if member.get("status") == "completed"
        and isinstance(member.get("measurement"), dict)
    ]
    batch_wall = (
        None
        if not completed
        else round(max(float(item["wall_ms"]) for item in completed), 6)
    )
    aggregate = {
        "completed_members": len(completed),
        "sum_cpu_ms": round(
            sum(float(item["cpu_ms"]) for item in completed),
            6,
        ),
        "sum_end_rss_bytes": (
            sum(int(item["end_rss_bytes"]) for item in completed)
            if completed
            and all(item.get("end_rss_bytes") is not None for item in completed)
            else None
        ),
        "sum_member_vm_hwm_bytes": (
            sum(int(item["vm_hwm_bytes"]) for item in completed)
            if completed
            and all(item.get("vm_hwm_bytes") is not None for item in completed)
            else None
        ),
    }
    return batch_wall, aggregate


def qualify(root: Path, spec_path: Path) -> dict[str, Any]:
    spec = load_lab_spec(spec_path)
    validate_reference_contract(spec, ROOT)
    manifest = load(root / "manifest.json")
    require(isinstance(manifest, dict), "manifest must be object")
    source = source_identity(ROOT)
    require(manifest.get("source_commit") == source["commit"], "lab source is not exact head")
    require(manifest.get("attempt_policy") == "single-pass-no-retry-v1", "retry policy drift")
    require(manifest.get("attempt_order_policy") == "blocked-cyclic-v1", "attempt order policy drift")
    require(manifest.get("placement_mode") == "observed", "placement mode drift")
    require(manifest.get("cpu_measurement") == spec.raw["cpu_measurement"], "CPU measurement policy drift")
    require(
        manifest.get("affinity_observation") == spec.raw["affinity_observation"],
        "affinity observation policy drift",
    )

    retained_spec = manifest.get("lab_spec") or {}
    require(retained_spec.get("sha256") == sha256(spec_path), "lab spec SHA mismatch")
    require(retained_spec.get("canonical_digest") == spec.digest, "lab spec digest mismatch")

    reference_manifest = manifest.get("reference_contract")
    require(isinstance(reference_manifest, dict), "reference contract manifest missing")
    for key, path in spec.raw["reference_contract"].items():
        retained = reference_manifest.get(key)
        require(isinstance(retained, dict), f"reference contract {key} missing")
        require(retained.get("path") == path, f"reference contract {key} path drift")
        require(
            retained.get("sha256") == sha256(ROOT / path),
            f"reference contract {key} SHA drift",
        )

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
    clock_ticks_per_second = int(
        domain["runtime_substrate"]["clock_ticks_per_second"]
    )
    cpu_policy = dict(spec.raw["cpu_measurement"])
    affinity_policy = dict(spec.raw["affinity_observation"])

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
    expected_a_plan = stage_a_attempt_plan(candidates, case_ids, spec.repeats)
    validate_attempt_order(
        rows,
        expected_a_plan,
        identity_field="candidate_id",
        label="Stage-A",
    )
    require(len(rows) == 1368, "frozen Stage-A attempt count drift")

    completed_a = errors_a = observation_faults = 0
    reconstructed_a: list[dict[str, Any]] = []
    for row in rows:
        candidate_id = row.get("candidate_id")
        candidate = candidate_map[candidate_id]
        require(row.get("candidate_digest") == candidate.digest, f"{candidate_id}: candidate digest drift")
        require(row.get("nodes") == candidate.nodes, f"{candidate_id}: work budget drift")
        status = row.get("status")
        require(status in ("completed", "error"), f"{candidate_id}: invalid attempt status")
        rebuilt = copy.deepcopy(row)
        if status == "completed":
            completed_a += 1
            measurement, faults = _reconstruct_measurement(
                row,
                candidate=candidate,
                clock_ticks_per_second=clock_ticks_per_second,
                cpu_policy=cpu_policy,
                affinity_policy=affinity_policy,
                label=f"Stage-A/{candidate_id}/{row['repeat_index']}/{row['case_id']}",
            )
            observation_faults += faults
            rebuilt["measurement"] = measurement
        else:
            errors_a += 1
            require(isinstance(row.get("error"), str) and row["error"], f"{candidate_id}: error outcome lacks error")
            require(row.get("measurement") is None, f"{candidate_id}: failed attempt carries measurement")
            require(row.get("physical_primitives") is None, f"{candidate_id}: failed attempt carries physical primitives")
        reconstructed_a.append(rebuilt)

    recomputed_pareto = build_pareto_report(
        spec=spec,
        candidates=candidates,
        rows=reconstructed_a,
        case_ids=case_ids,
    )
    retained_pareto = load(root / "stage-a/pareto.json")
    require(
        canonical_digest(retained_pareto) == canonical_digest(recomputed_pareto),
        "retained Pareto analysis does not independently recompute",
    )
    for group in retained_pareto["groups"]:
        reference_id = group["reference_candidate_id"]
        reference_summary = retained_pareto["candidate_summaries"][reference_id]
        require(reference_summary.get("complete") is True, f"reference candidate did not complete cleanly: {reference_id}")
        require(reference_summary.get("bestmove_repeatable") is True, f"reference bestmove vector unstable: {reference_id}")
        require(reference_summary.get("native_work_repeatable") is True, f"reference native-work vector unstable: {reference_id}")

    stage_b_rows = load(root / "stage-b/raw/rows.json")
    require(isinstance(stage_b_rows, list), "Stage-B rows must be array")
    expected_b_plan = stage_b_attempt_plan(compositions, case_ids, spec.repeats)
    validate_attempt_order(
        stage_b_rows,
        expected_b_plan,
        identity_field="composition_id",
        label="Stage-B",
    )
    require(len(stage_b_rows) == 72, "frozen Stage-B batch count drift")

    completed_b = errors_b = 0
    reconstructed_b: list[dict[str, Any]] = []
    for row in stage_b_rows:
        composition = composition_map[row["composition_id"]]
        require(row.get("composition_digest") == composition.digest, "composition digest drift")
        members = row.get("members")
        require(isinstance(members, list), "Stage-B member evidence missing")
        expected_members = {member.instance: member for member in composition.members}
        require({member.get("instance") for member in members} == set(expected_members), "Stage-B member set drift")
        rebuilt = copy.deepcopy(row)
        rebuilt_members: list[dict[str, Any]] = []
        for member in members:
            expected = expected_members[member["instance"]]
            require(member.get("candidate_id") == expected.candidate_id, "Stage-B candidate identity drift")
            require(member.get("candidate_digest") == expected.candidate_digest, "Stage-B candidate digest drift")
            require(member.get("status") in ("completed", "error"), "Stage-B member status invalid")
            rebuilt_member = copy.deepcopy(member)
            if member["status"] == "completed":
                candidate = candidate_map[expected.candidate_id]
                measurement, faults = _reconstruct_measurement(
                    member,
                    candidate=candidate,
                    clock_ticks_per_second=clock_ticks_per_second,
                    cpu_policy=cpu_policy,
                    affinity_policy=affinity_policy,
                    label=f"Stage-B/{row['composition_id']}/{row['repeat_index']}/{row['case_id']}/{member['instance']}",
                )
                observation_faults += faults
                rebuilt_member["measurement"] = measurement
            else:
                require(member.get("measurement") is None, "failed Stage-B member carries measurement")
                require(member.get("physical_primitives") is None, "failed Stage-B member carries physical primitives")
                require(isinstance(member.get("error"), str) and member["error"], "failed Stage-B member lacks error")
            rebuilt_members.append(rebuilt_member)

        require(row.get("status") in ("completed", "error"), "Stage-B batch status invalid")
        if row["status"] == "completed":
            completed_b += 1
            require(all(member["status"] == "completed" for member in members), "completed Stage-B batch contains failed member")
        else:
            errors_b += 1
            require(any(member["status"] == "error" for member in members), "failed Stage-B batch has no failed member")

        rebuilt["members"] = sorted(rebuilt_members, key=lambda item: item["instance"])
        batch_wall, aggregate = _recompute_aggregate(rebuilt_members)
        require(row.get("batch_wall_ms") == batch_wall, "Stage-B batch wall does not reconstruct")
        require(row.get("aggregate_resource") == aggregate, "Stage-B aggregate resource does not reconstruct")
        rebuilt["batch_wall_ms"] = batch_wall
        rebuilt["aggregate_resource"] = aggregate
        reconstructed_b.append(rebuilt)

    baseline_batches = [
        row
        for row in reconstructed_b
        if row.get("composition_id") == "c0-four-way-v2-current"
    ]
    require(
        len(baseline_batches) == spec.repeats * len(case_ids)
        and all(row.get("status") == "completed" for row in baseline_batches),
        "current-v2 Stage-B reference composition did not complete cleanly",
    )

    retained_stage_b_summary = load(root / "stage-b/summary.json")
    recomputed_stage_b_summary = summarize_composition_interference(
        rows=reconstructed_b,
        isolated_rows=reconstructed_a,
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
        "stage_a_expected_measurements": len(expected_a_plan),
        "stage_a_completed_measurements": completed_a,
        "stage_a_error_measurements": errors_a,
        "stage_a_pareto_candidate_ids": pareto_ids,
        "stage_b_compositions": len(compositions),
        "stage_b_expected_batches": len(expected_b_plan),
        "stage_b_completed_batches": completed_b,
        "stage_b_error_batches": errors_b,
        "affinity_observation_faults_retained": observation_faults,
        "cpu_measurement_method": cpu_policy["required_method"],
        "cpu_measurement_max_resolution_ns": cpu_policy["max_resolution_ns"],
        "attempt_order_policy": spec.raw["attempt_order_policy"],
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
                json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
                + "\n",
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
