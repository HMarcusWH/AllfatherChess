#!/usr/bin/env python3
"""Validate the M14-J J7 frozen resource-profile selection.

J7 is a deterministic transformation of retained J6 evidence. It does not
modify the runtime catalog and grants no runtime/resource/move authority.
"""
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

EVIDENCE = Path("qualification/resource-profile-evidence-v1.json")
SELECTION = Path("qualification/resource-profile-selection-v1.json")
CATALOG = Path("qualification/resource-profile-catalog-v1.json")
AUTH = {"runtime_authority": False, "resource_authorization": False, "outward_move": False}
FAMILIES = {"stockfish": (16, 64, 256), "reckless": (16, 64, 256), "lc0": (16, 32, 64)}
STAGE_B_IDS = {"c0-four-way-v2-current", "c1-anchor2-reckless-lc0", "c2-anchor2-lc0-threads2"}
METRICS = ("median_wall_ms", "p95_wall_ms", "median_cpu_ms", "p95_cpu_ms", "p95_vm_hwm_bytes")
TIE_BREAK = METRICS + ("candidate_id",)
WINNERS = {
    "stockfish/n16": "v2-current", "stockfish/n64": "t1-h32", "stockfish/n256": "v2-current",
    "reckless/n16": "v2-current", "reckless/n64": "v2-current", "reckless/n256": "v2-current",
    "lc0/n16": "v2-current", "lc0/n32": "prefetch0", "lc0/n64": "v2-current",
}


class SelectionQualificationError(ValueError):
    pass


def require(ok: bool, msg: str) -> None:
    if not ok:
        raise SelectionQualificationError(msg)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionQualificationError(f"cannot read {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain an object")
    return value


def sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SelectionQualificationError(f"cannot hash {path}: {exc}") from exc


def finite(value: Any, label: str) -> float:
    require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    value = float(value)
    require(math.isfinite(value) and value >= 0, f"{label} must be finite/non-negative")
    return value


def metric(row: dict[str, Any], name: str) -> float:
    values = row.get("metrics")
    require(isinstance(values, list) and len(values) == 5, f"{row.get('id')}: metric vector missing")
    return finite(values[METRICS.index(name)], f"{row.get('id')} {name}")


def candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(metric(row, name) for name in METRICS) + (row["id"],)


def validate(evidence_path: Path = EVIDENCE, selection_path: Path = SELECTION, catalog_path: Path = CATALOG) -> dict[str, Any]:
    ev, sel, catalog = load(evidence_path), load(selection_path), load(catalog_path)

    require(ev.get("schema_version") == 1 and ev.get("evidence_id") == "resource-profile-evidence-v1", "wrong evidence schema/id")
    require(sel.get("schema_version") == 1 and sel.get("selection_id") == "resource-profile-selection-v1", "wrong selection schema/id")
    require(ev.get("authority") == AUTH and sel.get("authority") == AUTH, "J7 authority boundary changed")
    ev_sha = sha(evidence_path)
    require(sel.get("evidence_sha256") == ev_sha, "selection/evidence SHA-256 mismatch")

    source = ev.get("source")
    require(isinstance(source, dict), "source record missing")
    require(source.get("lab_id") == "resource-lab-v1", "wrong J6 lab id")
    require(source.get("source_commit") == sel.get("source_commit") == "6aecd0bae7848ca8a9893377fadffb049d336c3a", "wrong J6 source commit")
    require(source.get("source_tree") == sel.get("source_tree") == "f557f5c2dab42bcbadff01d395490cd66dba93a0", "wrong J6 source tree")
    require(source.get("workflow_run") == 36754169712, "wrong J6 workflow")
    require(source.get("artifact_id") == 11117218402, "wrong J6 artifact")
    require(source.get("artifact_sha256") == "e8b153b1cb0eeafeb18796931e3cad116c09f8913a3eb61ac7d60806fc7ec0e7", "wrong J6 artifact hash")

    domain, sdomain = ev.get("execution_domain"), sel.get("execution_domain")
    require(isinstance(domain, dict) and isinstance(sdomain, dict), "execution-domain record missing")
    require({k: domain.get(k) for k in ("id", "digest", "binding_scope")} == {k: sdomain.get(k) for k in ("id", "digest", "binding_scope")}, "selection execution-domain mismatch")
    require(domain.get("binding_scope") == "exact_host_observation", "selection is not exact-host bound")

    clock = sel.get("catalog")
    ref = source.get("reference_contract")
    require(isinstance(clock, dict) and isinstance(ref, dict), "catalog/reference lock missing")
    require(clock.get("path") == "qualification/resource-profile-catalog-v1.json", "wrong catalog path")
    require(clock.get("sha256") == ref.get("catalog_sha256") == sha(catalog_path), "J3 catalog hash changed")
    require(catalog.get("schema_version") == 1 and catalog.get("catalog_version") == "resource-profile-catalog-v1", "J3 catalog schema changed")
    require(catalog.get("catalog_id") == clock.get("catalog_id") == "resource-profile-catalog-v1", "J3 catalog identity changed")
    require(catalog.get("selection_enabled") is False and clock.get("selection_enabled") is False, "runtime profile selection became enabled")
    require(catalog.get("fallback_profile") == clock.get("fallback_profile") == "engine-opt-v2", "fallback profile changed")
    require(catalog.get("authority") == {"resource_profile_catalog": True, "resource_authorization": False, "outward_move": False}, "J3 catalog authority changed")

    contract = ev.get("selection_contract")
    require(isinstance(contract, dict) and sel.get("selection_rule") == contract, "selection rule differs from evidence")
    require(tuple(contract.get("dimensions", ())) == METRICS, "selection metrics changed")
    require(tuple(contract.get("tie_break_order", ())) == TIE_BREAK and contract.get("tie_break") == "lexicographic-v1", "selection tie-break changed")
    for name in ("require_bestmove_reference_match", "require_repeatable_bestmove", "require_repeatable_native_work", "require_usable_cpu", "require_complete_process_cpu_scope"):
        require(contract.get(name) is True, f"eligibility gate {name} disabled")

    require(ev.get("stage_a") == {"candidates": 57, "expected_measurements": 1368, "completed_measurements": 1368, "errors": 0, "pareto_groups": 9, "pareto_candidates": 18}, "Stage-A summary changed")
    sb = ev.get("stage_b")
    require(isinstance(sb, dict) and sb.get("compositions") == 3 and sb.get("expected_batches") == sb.get("completed_batches") == 72 and sb.get("errors") == 0, "Stage-B summary incomplete")
    require(set(sb.get("composition_ids") or ()) == STAGE_B_IDS and isinstance(sb.get("members"), dict) and set(sb["members"]) == STAGE_B_IDS, "Stage-B identities changed")

    lab_spec = source.get("lab_spec")
    bundle = source.get("candidate_bundle")
    require(isinstance(lab_spec, dict) and isinstance(bundle, dict), "J6 lab-spec/candidate bundle lock missing")
    lab_path = Path(str(lab_spec.get("path")))
    require(sha(lab_path) == lab_spec.get("sha256"), "J6 lab-spec file hash changed")
    spec = load_lab_spec(lab_path)
    require(spec.digest == lab_spec.get("canonical_digest"), "J6 lab-spec canonical digest changed")
    candidates = expand_candidates(spec, bundle)
    require(len(candidates) == 57, "J6 candidate matrix no longer expands to 57 candidates")
    generated = {candidate.candidate_id: candidate for candidate in candidates}
    require(len(generated) == 57, "J6 generated candidate ids are not unique")
    compositions = expand_compositions(spec, candidates)
    require({row.composition_id for row in compositions} == STAGE_B_IDS, "J6 Stage-B composition matrix changed")
    composition_map = {row.composition_id: row for row in compositions}
    for composition_id in STAGE_B_IDS:
        require(composition_map[composition_id].as_dict()["members"] == sb["members"][composition_id], f"{composition_id}: Stage-B member binding changed")

    refs = ev.get("reference_candidates")
    require(isinstance(refs, list) and len(refs) == 9, "nine reference candidates required")
    expected_keys = {(f, n) for f, ns in FAMILIES.items() for n in ns}
    refmap: dict[tuple[str, int], dict[str, Any]] = {}
    for row in refs:
        require(isinstance(row, dict), "reference row malformed")
        key = (row.get("family"), row.get("nodes"))
        require(key in expected_keys and key not in refmap, f"unexpected/duplicate reference {key}")
        cid = row.get("id")
        require(cid in generated and row.get("digest") == generated[cid].digest, f"{key}: reference digest mismatch")
        require(isinstance(row.get("bestmove_digest"), str) and len(row["bestmove_digest"]) == 64, f"{key}: reference bestmove digest missing")
        refmap[key] = row

    rows = ev.get("pareto_candidates")
    require(isinstance(rows, list) and len(rows) == 18, "18 Pareto summaries required")
    byid: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), "Pareto row malformed")
        cid = row.get("id")
        require(cid in generated and cid not in byid, f"unknown/duplicate Pareto candidate {cid!r}")
        require(row.get("digest") == generated[cid].digest, f"{cid}: candidate digest mismatch")
        key = (row.get("family"), row.get("nodes"))
        require(key in expected_keys, f"{cid}: invalid family/work budget")
        candidate = generated[cid]
        require((candidate.family, candidate.nodes, candidate.variant) == (row.get("family"), row.get("nodes"), row.get("variant")), f"{cid}: candidate material identity mismatch")
        require(row.get("flags") == [True, True, True, True, True], f"{cid}: J7 eligibility flags are not all established")
        require(row.get("bestmove_digest") == refmap[key]["bestmove_digest"], f"{cid}: bestmove differs from frozen reference")
        require(isinstance(row.get("delta"), dict), f"{cid}: option delta missing")
        for name in METRICS:
            metric(row, name)
        byid[cid] = row

    groups = ev.get("groups")
    require(isinstance(groups, list) and len(groups) == 9, "nine selection groups required")
    groupmap: dict[tuple[str, int], list[str]] = {}
    total = 0
    for group in groups:
        require(isinstance(group, dict), "group row malformed")
        key = (group.get("family"), group.get("nodes"))
        require(key in expected_keys and key not in groupmap, f"unexpected/duplicate group {key}")
        require(group.get("reference_id") == refmap[key]["id"], f"{key}: reference changed")
        ids = group.get("pareto")
        require(isinstance(ids, list) and ids == sorted(set(ids)) and ids, f"{key}: malformed Pareto list")
        for cid in ids:
            require(cid in byid and (byid[cid]["family"], byid[cid]["nodes"]) == key, f"{key}: bad Pareto member {cid}")
        groupmap[key] = ids
        total += len(ids)
    require(set(groupmap) == expected_keys and total == 18, "group/Pareto coverage incomplete")
    require(set().union(*(set(v) for v in groupmap.values())) == set(byid), "frozen Pareto summaries/group membership disagree")

    selections = sel.get("selections")
    require(isinstance(selections, list) and len(selections) == 9, "exactly nine selections required")
    seen: set[tuple[str, int]] = set()
    winners: dict[str, str] = {}
    for chosen in selections:
        require(isinstance(chosen, dict), "selection row malformed")
        key = (chosen.get("family"), chosen.get("nodes"))
        require(key in expected_keys and key not in seen, f"unexpected/duplicate selection {key}")
        seen.add(key)
        cid = chosen.get("candidate_id")
        require(cid in groupmap[key], f"{key}: selected candidate is not Pareto eligible")
        expected = min(groupmap[key], key=lambda x: candidate_key(byid[x]))
        require(cid == expected, f"{key}: deterministic tie-break violation")
        row = byid[cid]
        require(chosen.get("candidate_digest") == row["digest"] and chosen.get("variant") == row["variant"], f"{key}: candidate identity mismatch")
        require(chosen.get("reference_candidate_id") == refmap[key]["id"], f"{key}: reference mismatch")
        require(chosen.get("option_overrides") == row["delta"], f"{key}: option delta mismatch")
        require(chosen.get("selection_status") == "isolated_resource_profile", f"{key}: invalid selection status")
        require(chosen.get("composition_qualification") == "not_established", f"{key}: composition qualification overclaimed")
        skey = chosen.get("selection_key")
        require(isinstance(skey, dict) and tuple(skey) == METRICS, f"{key}: selection key changed")
        for name in METRICS:
            require(float(skey[name]) == metric(row, name), f"{key}: selection metric mismatch: {name}")
        winners[f"{key[0]}/n{key[1]}"] = row["variant"]
    require(seen == expected_keys and winners == WINNERS, "frozen J7 selection set changed")

    return {"qualified": True, "selection_id": sel["selection_id"], "evidence_sha256": ev_sha, "catalog_sha256": clock["sha256"], "groups": 9, "pareto_candidates": 18, "selections": winners, "authority": AUTH, "composition_qualification": "not_established"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--selection", type=Path, default=SELECTION)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.evidence, args.selection, args.catalog)
    except SelectionQualificationError as exc:
        print(f"J7 resource-profile selection qualification FAILED: {exc}")
        return 2
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
