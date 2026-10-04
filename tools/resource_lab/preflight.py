"""Real-engine measurement-substrate preflight for the J6 resource laboratory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.domain import candidate_bundle_identity, validate_execution_domain
from tools.engine_opt.report import source_identity

from .candidate_matrix import (
    expand_candidates,
    expand_compositions,
    load_lab_spec,
    validate_reference_contract,
)
from .qualify import (
    ResourceLabQualificationError,
    _recompute_aggregate,
    _reconstruct_measurement,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceLabQualificationError(message)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_preflight(root: Path, spec_path: Path) -> dict[str, Any]:
    spec = load_lab_spec(spec_path)
    validate_reference_contract(spec, ROOT)
    manifest = load(root / "manifest.json")
    source = source_identity(ROOT)
    require(manifest.get("source_commit") == source["commit"], "preflight source is not exact head")
    require(manifest.get("cpu_measurement") == spec.raw["cpu_measurement"], "preflight CPU policy drift")
    require(manifest.get("affinity_observation") == spec.raw["affinity_observation"], "preflight affinity policy drift")

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
    require(
        manifest.get("candidate_bundle")
        == candidate_bundle_identity(
            bundle_root,
            expected_source_commit=source["commit"],
        ),
        "preflight candidate bundle identity drift",
    )
    build_manifest = load(bundle_root / "build-manifest.json")
    candidates = expand_candidates(spec, build_manifest)
    compositions = expand_compositions(spec, candidates)
    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    c0 = compositions[0]

    stage_a = load(root / "stage-a/raw/rows.json")
    require(isinstance(stage_a, list), "preflight Stage-A rows must be array")
    reference_attempts = sum(candidate.reference for candidate in candidates)
    require(
        len(stage_a) == reference_attempts,
        f"preflight reference-attempt count drift: {len(stage_a)} != {reference_attempts}",
    )
    require(
        [row.get("attempt_ordinal") for row in stage_a]
        == list(range(reference_attempts)),
        "preflight Stage-A attempt order is not canonical",
    )
    families: set[str] = set()
    positive_by_family: dict[str, int] = {}
    for row in stage_a:
        require(row.get("status") == "completed", f"preflight Stage-A failed: {row.get('error')}")
        candidate = candidate_map.get(row.get("candidate_id"))
        require(candidate is not None and candidate.reference, "preflight used a non-reference candidate")
        families.add(candidate.family)
        measurement, _ = _reconstruct_measurement(
            row,
            candidate=candidate,
            clock_ticks_per_second=clock_ticks_per_second,
            cpu_policy=cpu_policy,
            affinity_policy=affinity_policy,
            label=f"preflight/{candidate.candidate_id}",
        )
        require(
            measurement.get("cpu_clock_method") == cpu_policy["required_method"],
            "preflight CPU method drift",
        )
        require(
            int(measurement.get("cpu_clock_resolution_ns"))
            <= int(cpu_policy["max_resolution_ns"]),
            "preflight CPU clock resolution exceeds frozen maximum",
        )
        require(
            isinstance(row.get("process_cpu_scope"), dict)
            and row["process_cpu_scope"].get("complete") is True,
            f"preflight process CPU scope incomplete for {candidate.candidate_id}",
        )
        if float(measurement["cpu_ms"]) > 0.0:
            positive_by_family[candidate.family] = (
                positive_by_family.get(candidate.family, 0) + 1
            )
        if candidate.family == "lc0":
            if candidate.warmup_nodes is None:
                require(
                    row.get("warmup") is None,
                    "preflight LC0 unexpectedly performed warmup",
                )
            else:
                require(
                    isinstance(row.get("warmup"), dict)
                    and row["warmup"].get("nodes") == candidate.warmup_nodes,
                    "preflight LC0 warmup evidence missing",
                )

    require(families == {"stockfish", "reckless", "lc0"}, "preflight family coverage drift")
    require(
        all(positive_by_family.get(family, 0) > 0 for family in families),
        "preflight CPU signal is not measurably positive for every family",
    )

    stage_b = load(root / "stage-b/raw/rows.json")
    require(isinstance(stage_b, list) and len(stage_b) == 1, "preflight must run exactly one C0 batch")
    batch = stage_b[0]
    require(batch.get("composition_id") == c0.composition_id, "preflight Stage-B composition is not C0")
    require(batch.get("status") == "completed", f"preflight C0 failed: {batch.get('error')}")
    expected_members = {member.instance: member for member in c0.members}
    members = batch.get("members")
    require(isinstance(members, list), "preflight C0 member evidence missing")
    require({row.get("instance") for row in members} == set(expected_members), "preflight C0 member set drift")
    rebuilt_members: list[dict[str, Any]] = []
    for member in members:
        require(member.get("status") == "completed", f"preflight C0 member failed: {member.get('error')}")
        expected = expected_members[member["instance"]]
        candidate = candidate_map[expected.candidate_id]
        measurement, _ = _reconstruct_measurement(
            member,
            candidate=candidate,
            clock_ticks_per_second=clock_ticks_per_second,
            cpu_policy=cpu_policy,
            affinity_policy=affinity_policy,
            label=f"preflight/C0/{member['instance']}",
        )
        require(float(measurement["cpu_ms"]) > 0.0, f"preflight C0 CPU signal is zero for {member['instance']}")
        require(
            isinstance(member.get("process_cpu_scope"), dict)
            and member["process_cpu_scope"].get("complete") is True,
            f"preflight C0 CPU scope incomplete for {member['instance']}",
        )
        rebuilt = dict(member)
        rebuilt["measurement"] = measurement
        rebuilt_members.append(rebuilt)

    batch_wall, aggregate = _recompute_aggregate(rebuilt_members)
    require(batch.get("batch_wall_ms") == batch_wall, "preflight C0 batch wall does not reconstruct")
    require(batch.get("aggregate_resource") == aggregate, "preflight C0 aggregate does not reconstruct")
    require(aggregate.get("process_scope_complete") is True, "preflight C0 process scope is incomplete")
    require(float(aggregate.get("sum_cpu_ms") or 0.0) > 0.0, "preflight C0 aggregate CPU signal is zero")

    return {
        "schema_version": 1,
        "profile_id": f"{spec.lab_id}-preflight",
        "source_commit": source["commit"],
        "execution_domain_id": domain["execution_domain_id"],
        "cpu_measurement_method": cpu_policy["required_method"],
        "cpu_measurement_max_resolution_ns": cpu_policy["max_resolution_ns"],
        "families": sorted(families),
        "stage_a_reference_attempts": len(stage_a),
        "stage_b_reference_batches": len(stage_b),
        "preflight_valid": True,
        "promotion_ready": False,
        "claim_boundary": {
            "resource_measurement_preflight": True,
            "profile_selection": False,
            "strength": False,
            "elo": False,
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
        report = validate_preflight(args.root.resolve(), args.spec.resolve())
    except Exception as exc:
        report = {
            "schema_version": 1,
            "profile_id": "resource-lab-v1-preflight",
            "preflight_valid": False,
            "promotion_ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(report, sort_keys=True))
        return 1

    output = args.output or args.root / "preflight-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
