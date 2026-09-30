"""Deterministic behavior-preserving Pareto analysis for J6 Stage A."""

from __future__ import annotations

from typing import Any

from .candidate_matrix import Candidate, LabSpec
from .measure import candidate_summary


class ParetoError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ParetoError(message)


def _metric(summary: dict[str, Any], name: str) -> float:
    if name == "median_wall_ms":
        return float(summary["wall_ms"]["median"])
    if name == "p95_wall_ms":
        return float(summary["wall_ms"]["p95"])
    if name == "median_cpu_ms":
        return float(summary["cpu_ms"]["median"])
    if name == "p95_cpu_ms":
        return float(summary["cpu_ms"]["p95"])
    if name == "p95_vm_hwm_bytes":
        memory = summary.get("vm_hwm_bytes")
        require(isinstance(memory, dict), "candidate lacks VmHWM evidence")
        return float(memory["p95"])
    raise ParetoError(f"unknown Pareto metric: {name}")


def dominates(
    left: dict[str, Any],
    right: dict[str, Any],
    dimensions: tuple[str, ...],
) -> bool:
    lvals = tuple(_metric(left, name) for name in dimensions)
    rvals = tuple(_metric(right, name) for name in dimensions)
    return all(l <= r for l, r in zip(lvals, rvals)) and any(
        l < r for l, r in zip(lvals, rvals)
    )


def build_pareto_report(
    *,
    spec: LabSpec,
    candidates: tuple[Candidate, ...],
    rows: list[dict[str, Any]],
    case_ids: tuple[str, ...],
) -> dict[str, Any]:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        candidate_id = row.get("candidate_id")
        require(isinstance(candidate_id, str), "Stage-A row lacks candidate id")
        by_candidate.setdefault(candidate_id, []).append(row)

    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    require(set(by_candidate).issubset(candidate_map), "Stage-A rows contain unknown candidate id")
    summaries: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        summary = candidate_summary(
            by_candidate.get(candidate.candidate_id, []),
            case_ids=case_ids,
            repeats=spec.repeats,
        )
        summary.update(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_digest": candidate.digest,
                "family": candidate.family,
                "variant": candidate.variant,
                "nodes": candidate.nodes,
                "reference": candidate.reference,
            }
        )
        summaries[candidate.candidate_id] = summary

    lookup = {
        (candidate.family, candidate.nodes, candidate.reference): candidate
        for candidate in candidates
        if candidate.reference
    }
    dimensions = tuple(spec.raw["pareto"]["dimensions"])
    groups: list[dict[str, Any]] = []

    for family in ("stockfish", "reckless", "lc0"):
        budgets = sorted({candidate.nodes for candidate in candidates if candidate.family == family})
        for nodes in budgets:
            reference = lookup.get((family, nodes, True))
            require(reference is not None, f"{family}/n{nodes}: reference candidate missing")
            ref_summary = summaries[reference.candidate_id]
            reference_vector = (
                tuple(ref_summary["bestmove_vectors"][0])
                if ref_summary.get("bestmove_vectors")
                else None
            )
            group_candidates = sorted(
                (
                    candidate
                    for candidate in candidates
                    if candidate.family == family and candidate.nodes == nodes
                ),
                key=lambda candidate: candidate.candidate_id,
            )
            eligible: list[Candidate] = []
            rejected: list[dict[str, Any]] = []
            for candidate in group_candidates:
                summary = summaries[candidate.candidate_id]
                reasons: list[str] = []
                if not summary.get("complete"):
                    reasons.append("incomplete")
                if spec.raw["pareto"]["require_repeatable_bestmove"] and not summary.get("bestmove_repeatable"):
                    reasons.append("bestmove-not-repeatable")
                if spec.raw["pareto"]["require_repeatable_native_work"] and not summary.get("native_work_repeatable"):
                    reasons.append("native-work-not-repeatable")
                cpu_quality = summary.get("cpu_measurement_quality")
                if not isinstance(cpu_quality, dict) or cpu_quality.get("usable") is not True:
                    reasons.append("cpu-measurement-unusable")
                if summary.get("process_cpu_scope_complete") is not True:
                    reasons.append("process-cpu-scope-incomplete")
                vector = (
                    tuple(summary["bestmove_vectors"][0])
                    if summary.get("bestmove_vectors")
                    else None
                )
                if (
                    spec.raw["pareto"]["require_bestmove_reference_match"]
                    and reference_vector is not None
                    and vector != reference_vector
                ):
                    reasons.append("bestmove-reference-drift")
                try:
                    for metric in dimensions:
                        _metric(summary, metric)
                except (KeyError, TypeError, ValueError, ParetoError):
                    reasons.append("missing-pareto-metric")
                if reasons:
                    rejected.append({"candidate_id": candidate.candidate_id, "reasons": sorted(set(reasons))})
                else:
                    eligible.append(candidate)

            pareto: list[str] = []
            dominated: list[dict[str, Any]] = []
            for candidate in eligible:
                dominators = [
                    other.candidate_id
                    for other in eligible
                    if other.candidate_id != candidate.candidate_id
                    and dominates(
                        summaries[other.candidate_id],
                        summaries[candidate.candidate_id],
                        dimensions,
                    )
                ]
                if dominators:
                    dominated.append(
                        {
                            "candidate_id": candidate.candidate_id,
                            "dominated_by": sorted(dominators),
                        }
                    )
                else:
                    pareto.append(candidate.candidate_id)

            groups.append(
                {
                    "family": family,
                    "nodes": nodes,
                    "reference_candidate_id": reference.candidate_id,
                    "eligible": sorted(candidate.candidate_id for candidate in eligible),
                    "pareto": sorted(pareto),
                    "dominated": sorted(dominated, key=lambda row: row["candidate_id"]),
                    "rejected": sorted(rejected, key=lambda row: row["candidate_id"]),
                }
            )

    return {
        "schema_version": 1,
        "kind": "resource-lab-stage-a-pareto",
        "lab_id": spec.lab_id,
        "lab_spec_digest": spec.digest,
        "dimensions": list(dimensions),
        "candidate_summaries": summaries,
        "groups": groups,
        "claim_boundary": {
            "pareto_analysis": True,
            "profile_selection": False,
            "strength": False,
            "elo": False,
            "deployment": False,
        },
    }
