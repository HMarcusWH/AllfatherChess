#!/usr/bin/env python3
"""Validate frozen resource-profile evidence/selection v2 without trusting producer claims."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.resource_lab.candidate_matrix import expand_candidates, expand_compositions, load_lab_spec

EVIDENCE = ROOT / "qualification/resource-profile-evidence-v2.json"
SELECTION = ROOT / "qualification/resource-profile-selection-v2.json"
SPEC = ROOT / "qualification/resource-lab-v2.json"
AUTH = {"runtime_authority": False, "resource_authorization": False, "outward_move": False}
FAMILIES = {"stockfish": (16, 64, 256), "reckless": (16, 64, 256), "lc0": (16, 32, 64)}
METRICS = ("median_wall_ms", "p95_wall_ms", "median_cpu_ms", "p95_cpu_ms", "p95_vm_hwm_bytes")
TIE_BREAK = METRICS + ("candidate_id",)


class SelectionV2Error(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise SelectionV2Error(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionV2Error(f"cannot read {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path}: root must be an object")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any, label: str) -> float:
    require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    value = float(value)
    require(math.isfinite(value) and value >= 0, f"{label} must be finite/non-negative")
    return value


def candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    metrics = row.get("metrics")
    require(isinstance(metrics, list) and len(metrics) == len(METRICS), f"{row.get('id')}: metric vector missing")
    return tuple(finite(value, f"{row.get('id')} metric") for value in metrics) + (row["id"],)


def validate(
    evidence_path: Path = EVIDENCE,
    selection_path: Path = SELECTION,
    spec_path: Path = SPEC,
) -> dict[str, Any]:
    evidence = load(evidence_path)
    selection = load(selection_path)
    spec = load_lab_spec(spec_path)

    require(
        evidence.get("schema_version") == 1
        and evidence.get("evidence_id") == "resource-profile-evidence-v2",
        "wrong evidence schema/id",
    )
    require(
        selection.get("schema_version") == 1
        and selection.get("selection_id") == "resource-profile-selection-v2",
        "wrong selection schema/id",
    )
    require(evidence.get("authority") == AUTH and selection.get("authority") == AUTH, "authority boundary changed")
    require(selection.get("evidence_path") == "qualification/resource-profile-evidence-v2.json", "selection evidence path drift")
    require(selection.get("evidence_sha256") == sha(evidence_path), "selection/evidence SHA mismatch")

    source = evidence.get("source")
    require(isinstance(source, dict) and source.get("lab_id") == "resource-lab-v2", "wrong source lab")
    require(selection.get("source_commit") == source.get("source_commit"), "selection/source commit mismatch")
    require(selection.get("source_tree") == source.get("source_tree"), "selection/source tree mismatch")
    require(isinstance(source.get("workflow_run"), int) and source["workflow_run"] > 0, "workflow run missing")
    require(isinstance(source.get("artifact_id"), int) and source["artifact_id"] > 0, "artifact id missing")
    require(isinstance(source.get("artifact_sha256"), str) and len(source["artifact_sha256"]) == 64, "artifact SHA missing")

    frozen_spec = source.get("lab_spec")
    require(isinstance(frozen_spec, dict), "frozen lab spec missing")
    require(frozen_spec.get("path") == "qualification/resource-lab-v2.json", "frozen lab-spec path drift")
    require(frozen_spec.get("sha256") == sha(spec_path), "current resource-lab-v2 spec differs from measured spec")
    require(frozen_spec.get("canonical_digest") == spec.digest, "lab-spec canonical digest drift")

    ref = source.get("reference_contract")
    require(isinstance(ref, dict), "reference contract missing")
    for key in ("selection", "runtime", "build_policy"):
        path = ref.get(f"{key}_path")
        digest = ref.get(f"{key}_sha256")
        require(isinstance(path, str) and isinstance(digest, str), f"reference contract {key} missing")
        require(digest == sha(ROOT / path), f"reference contract {key} changed after measurement")

    domain = evidence.get("execution_domain")
    sdomain = selection.get("execution_domain")
    require(isinstance(domain, dict) and isinstance(sdomain, dict), "execution domain missing")
    require(
        {k: domain.get(k) for k in ("id", "digest", "binding_scope")}
        == {k: sdomain.get(k) for k in ("id", "digest", "binding_scope")},
        "selection execution-domain mismatch",
    )
    require(domain.get("binding_scope") == "exact_host_observation", "v2 evidence is not exact-host bound")

    bundle = source.get("candidate_bundle")
    require(isinstance(bundle, dict), "candidate bundle missing")
    require(bundle.get("source_commit") == source.get("source_commit"), "candidate bundle source mismatch")
    candidates = expand_candidates(spec, bundle)
    generated = {candidate.candidate_id: candidate for candidate in candidates}
    require(len(generated) == len(candidates), "generated candidate ids are not unique")
    compositions = expand_compositions(spec, candidates)

    expected = spec.raw.get("expected_counts") or {}
    stage_a = evidence.get("stage_a")
    stage_b = evidence.get("stage_b")
    require(isinstance(stage_a, dict) and isinstance(stage_b, dict), "stage summaries missing")
    require(stage_a.get("candidates") == expected.get("stage_a_candidates") == len(candidates), "Stage-A candidate count drift")
    require(stage_a.get("expected_measurements") == expected.get("stage_a_measurements"), "Stage-A measurement plan drift")
    require(stage_a.get("completed_measurements") == stage_a.get("expected_measurements"), "Stage-A incomplete")
    require(stage_a.get("errors") == 0, "Stage-A retained errors")
    require(stage_a.get("pareto_groups") == 9, "resource group count drift")
    require(stage_b.get("compositions") == expected.get("stage_b_compositions") == len(compositions), "Stage-B composition count drift")
    require(stage_b.get("expected_batches") == expected.get("stage_b_batches"), "Stage-B batch plan drift")
    require(stage_b.get("completed_batches") == stage_b.get("expected_batches"), "Stage-B incomplete")
    require(stage_b.get("errors") == 0, "Stage-B retained errors")
    require(
        stage_b.get("composition_ids") == [row.composition_id for row in compositions],
        "Stage-B composition ids differ from frozen spec",
    )
    require(
        stage_b.get("members")
        == {row.composition_id: [member.as_dict() for member in row.members] for row in compositions},
        "Stage-B composition members differ from frozen spec",
    )

    contract = evidence.get("selection_contract")
    require(isinstance(contract, dict) and selection.get("selection_rule") == contract, "selection rule/evidence mismatch")
    require(tuple(contract.get("dimensions") or ()) == METRICS, "selection dimensions drift")
    require(tuple(contract.get("tie_break_order") or ()) == TIE_BREAK and contract.get("tie_break") == "lexicographic-v1", "tie-break drift")
    for name in (
        "require_bestmove_reference_match",
        "require_repeatable_bestmove",
        "require_repeatable_native_work",
        "require_usable_cpu",
        "require_complete_process_cpu_scope",
    ):
        require(contract.get(name) is True, f"eligibility gate disabled: {name}")

    expected_keys = {(family, nodes) for family, nodeset in FAMILIES.items() for nodes in nodeset}
    refs = evidence.get("reference_candidates")
    require(isinstance(refs, list) and len(refs) == 9, "nine reference candidates required")
    refmap: dict[tuple[str, int], dict[str, Any]] = {}
    for row in refs:
        key = (row.get("family"), row.get("nodes"))
        require(key in expected_keys and key not in refmap, f"unexpected/duplicate reference {key}")
        cid = row.get("id")
        require(cid in generated and generated[cid].reference, f"{key}: reference candidate identity drift")
        require(row.get("digest") == generated[cid].digest, f"{key}: reference digest drift")
        require(isinstance(row.get("bestmove_digest"), str) and len(row["bestmove_digest"]) == 64, f"{key}: reference bestmove digest missing")
        refmap[key] = row

    rows = evidence.get("pareto_candidates")
    require(isinstance(rows, list) and len(rows) == stage_a.get("pareto_candidates"), "Pareto candidate count drift")
    byid: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row.get("id")
        require(cid in generated and cid not in byid, f"unknown/duplicate Pareto candidate {cid!r}")
        candidate = generated[cid]
        key = (row.get("family"), row.get("nodes"))
        require(key in expected_keys and (candidate.family, candidate.nodes, candidate.variant) == (row.get("family"), row.get("nodes"), row.get("variant")), f"{cid}: candidate material identity mismatch")
        require(row.get("digest") == candidate.digest, f"{cid}: candidate digest mismatch")
        require(row.get("flags") == [True, True, True, True, True], f"{cid}: promotion flags are not all established")
        require(isinstance(row.get("bestmove_digest"), str) and len(row["bestmove_digest"]) == 64, f"{cid}: bestmove digest missing")
        candidate_key(row)
        byid[cid] = row

    groups = evidence.get("groups")
    require(isinstance(groups, list) and len(groups) == 9, "nine groups required")
    groupmap: dict[tuple[str, int], list[str]] = {}
    for group in groups:
        key = (group.get("family"), group.get("nodes"))
        require(key in expected_keys and key not in groupmap, f"unexpected/duplicate group {key}")
        require(group.get("reference_id") == refmap[key]["id"], f"{key}: reference binding drift")
        ids = group.get("pareto")
        require(isinstance(ids, list) and ids == sorted(set(ids)) and ids, f"{key}: malformed Pareto set")
        for cid in ids:
            require(cid in byid and (byid[cid]["family"], byid[cid]["nodes"]) == key, f"{key}: bad Pareto member {cid}")
            require(byid[cid]["bestmove_digest"] == refmap[key]["bestmove_digest"], f"{cid}: bestmove differs from reference")
        groupmap[key] = ids
    require(set(groupmap) == expected_keys, "group coverage incomplete")
    require(set().union(*(set(ids) for ids in groupmap.values())) == set(byid), "group/Pareto coverage mismatch")

    selections = selection.get("selections")
    require(isinstance(selections, list) and len(selections) == 9, "exactly nine selections required")
    seen: set[tuple[str, int]] = set()
    winners: dict[str, str] = {}
    for chosen in selections:
        key = (chosen.get("family"), chosen.get("nodes"))
        require(key in expected_keys and key not in seen, f"unexpected/duplicate selection {key}")
        seen.add(key)
        cid = chosen.get("candidate_id")
        require(cid in groupmap[key], f"{key}: selected candidate is not Pareto eligible")
        expected_id = min(groupmap[key], key=lambda candidate_id: candidate_key(byid[candidate_id]))
        require(cid == expected_id, f"{key}: deterministic tie-break violation")
        row = byid[cid]
        require(chosen.get("candidate_digest") == row["digest"] and chosen.get("variant") == row["variant"], f"{key}: selected candidate identity mismatch")
        require(chosen.get("reference_candidate_id") == refmap[key]["id"], f"{key}: selected reference mismatch")
        require(chosen.get("option_overrides") == row.get("delta"), f"{key}: selected option delta mismatch")
        require(chosen.get("selection_status") == "isolated_resource_profile", f"{key}: invalid selection status")
        require(chosen.get("composition_qualification") == "not_established", f"{key}: composition qualification overclaim")
        skey = chosen.get("selection_key")
        require(
            isinstance(skey, dict) and set(skey) == set(METRICS),
            f"{key}: selection key drift",
        )
        for index, name in enumerate(METRICS):
            require(
                float(skey[name]) == float(row["metrics"][index]),
                f"{key}: selection metric mismatch {name}",
            )
        winners[f"{key[0]}/n{key[1]}"] = row["variant"]
    require(seen == expected_keys, "selection coverage incomplete")

    return {
        "qualified": True,
        "selection_id": selection["selection_id"],
        "evidence_sha256": selection["evidence_sha256"],
        "groups": 9,
        "pareto_candidates": len(byid),
        "selections": winners,
        "authority": AUTH,
        "composition_qualification": "not_established",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--selection", type=Path, default=SELECTION)
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.evidence, args.selection, args.spec)
    except SelectionV2Error as exc:
        print(f"resource-profile selection v2 qualification FAILED: {exc}")
        return 2
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
