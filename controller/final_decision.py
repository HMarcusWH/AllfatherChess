"""Persistence for the actual outward M14-C decision.

The live decision is selected entirely in memory on the anchor completion path.
This module runs only after stdout emission and seals a hash-bound description
of which authority selected the move. It never participates in authorization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from controller.decision import FinalDecision, canonical_digest
from controller.replay import atomic_write_text, sha256_file


FINAL_DECISION_SCHEMA_VERSION = 1


class FinalDecisionError(RuntimeError):
    """Raised when final-decision evidence cannot be sealed or verified."""


_SOURCE_PATHS = (
    "manifest.json",
    "verification/manifest.json",
    "crossfeed/manifest.json",
    "decision/counterfactual.json",
    "route.json",
    "resource.json",
)
_OPTIONAL_SOURCE_PATHS = ("staged_verification/manifest.json",)


def _source_hashes(run_dir: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    sources: dict[str, str] = {}
    missing: list[str] = []
    for relative in _SOURCE_PATHS:
        path = run_dir / relative
        if path.is_file():
            sources[relative] = sha256_file(path)
        else:
            missing.append(relative)
    for relative in _OPTIONAL_SOURCE_PATHS:
        path = run_dir / relative
        if path.is_file():
            sources[relative] = sha256_file(path)
    return sources, tuple(missing)


def seal_final_decision_artifact(
    decision: FinalDecision,
    run_dir: Path | str,
) -> dict[str, Any]:
    """Seal the actual selected authority after the source artifacts finalize."""

    run_dir = Path(run_dir)
    sources, missing_sources = _source_hashes(run_dir)
    core = {
        "schema_version": FINAL_DECISION_SCHEMA_VERSION,
        "decision": decision.as_dict(),
        "sources": sources,
        "missing_sources": list(missing_sources),
        "audit_complete": not missing_sources,
        "semantics": {
            "authorization_timing": "bounded in-memory at anchor completion",
            "terminal_resource_timing": "post-output; may qualify claims but cannot rewrite the played move",
        },
    }
    digest = canonical_digest(core)
    artifact = {
        **core,
        "decision_id": f"final-v1:{digest[:16]}",
        "content_sha256": digest,
    }
    target = run_dir / "decision" / "final.json"
    atomic_write_text(
        target,
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
    )
    return artifact


def load_final_decision_artifact(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "decision" / "final.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalDecisionError(f"cannot load final decision artifact {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FinalDecisionError("final decision artifact root must be an object")
    if data.get("schema_version") != FINAL_DECISION_SCHEMA_VERSION:
        raise FinalDecisionError(
            f"unsupported final decision schema_version: {data.get('schema_version')!r}"
        )
    return data


def verify_final_decision_integrity(run_dir: Path | str) -> list[str]:
    """Check self-digest and the source hashes captured after output."""

    run_dir = Path(run_dir)
    try:
        artifact = load_final_decision_artifact(run_dir)
    except FinalDecisionError as exc:
        return [str(exc)]

    problems: list[str] = []
    core = {
        "schema_version": artifact.get("schema_version"),
        "decision": artifact.get("decision"),
        "sources": artifact.get("sources"),
        "missing_sources": artifact.get("missing_sources"),
        "audit_complete": artifact.get("audit_complete"),
        "semantics": artifact.get("semantics"),
    }
    expected = canonical_digest(core)
    if artifact.get("content_sha256") != expected:
        problems.append("final decision content digest mismatch")

    decision = artifact.get("decision")
    if isinstance(decision, dict):
        authorization = decision.get("authorization")
        snapshot = decision.get("authorization_snapshot")
        if isinstance(authorization, dict) and isinstance(snapshot, dict):
            expected_snapshot = canonical_digest(snapshot)
            if authorization.get("snapshot_digest") != expected_snapshot:
                problems.append("final decision authorization snapshot digest mismatch")
            if (
                snapshot.get("terminal_source") == "staged_verification"
                and "staged_verification/manifest.json"
                not in (artifact.get("sources") or {})
            ):
                problems.append(
                    "staged authority is missing direct staged VERIFY source binding"
                )
        else:
            problems.append("final decision authorization evidence is incomplete")
    else:
        problems.append("final decision decision payload must be an object")

    missing_sources = artifact.get("missing_sources")
    if not isinstance(missing_sources, list):
        problems.append("final decision missing_sources must be an array")
    elif missing_sources:
        problems.append(
            "final decision audit sources missing: " + ", ".join(map(str, missing_sources))
        )
    if artifact.get("audit_complete") is not (not bool(missing_sources)):
        problems.append("final decision audit_complete is inconsistent")

    # M14-G3 authorization is reconstructed from the exact sealed control
    # artifacts rather than trusting the snapshot's descriptive fields.
    if isinstance(decision, dict):
        authorization = decision.get("authorization")
        snapshot = decision.get("authorization_snapshot")
        if (
            isinstance(authorization, dict)
            and authorization.get("policy") == "clocked_staged_preanchor_v1"
            and isinstance(snapshot, dict)
        ):
            def _load(relative: str) -> dict[str, Any] | None:
                path = run_dir / relative
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    problems.append(f"G3 authority source cannot be loaded: {relative}")
                    return None
                if not isinstance(value, dict):
                    problems.append(f"G3 authority source is not an object: {relative}")
                    return None
                return value

            parent = _load("manifest.json")
            route = _load("route.json")
            staged = _load("staged_verification/manifest.json")
            counterfactual = _load("decision/counterfactual.json")

            if parent is not None:
                if parent.get("outward_decision") != decision:
                    problems.append("G3 replay outward_decision differs from final decision")
                plan = parent.get("time_plan")
                if not isinstance(plan, dict) or plan.get("plan_id") != snapshot.get("time_plan_id"):
                    problems.append("G3 authorization TimePlan identity mismatch")

            if route is not None:
                route_digest = snapshot.get("route_decision_digest")
                matches = [
                    item
                    for item in (route.get("value_decisions") or [])
                    if isinstance(item, dict)
                    and canonical_digest(item) == route_digest
                ]
                if len(matches) != 1:
                    problems.append("G3 route decision digest does not identify exactly one sealed route")
                else:
                    chosen = matches[0]
                    if chosen.get("action") != snapshot.get("route_action"):
                        problems.append("G3 route action differs from authorization snapshot")
                    if chosen.get("buy_extension") is not snapshot.get("route_buy_extension"):
                        problems.append("G3 route buy flag differs from authorization snapshot")

            if staged is not None:
                nomination = staged.get("nomination") or {}
                if staged.get("intervention") != snapshot.get("staged_intervention"):
                    problems.append("G3 staged intervention identity mismatch")
                if staged.get("generation") != snapshot.get("staged_generation"):
                    problems.append("G3 staged generation mismatch")
                if list(snapshot.get("staged_candidate_roots") or []) != list(
                    nomination.get("candidate_roots") or []
                ):
                    problems.append("G3 staged candidate order mismatch")
                if (staged.get("disposition") or {}).get("run") != "completed":
                    problems.append("G3 authority source staged VERIFY is not completed")

            if counterfactual is not None:
                source = counterfactual.get("source") or {}
                if source.get("decision_terminal_source") != snapshot.get("terminal_source"):
                    problems.append("G3 counterfactual terminal source mismatch")

    stored_sources = artifact.get("sources")
    if not isinstance(stored_sources, dict):
        problems.append("final decision sources must be an object")
        return problems
    for relative, stored_hash in stored_sources.items():
        path = run_dir / str(relative)
        if not path.is_file():
            problems.append(f"final decision source missing: {relative}")
            continue
        if sha256_file(path) != stored_hash:
            problems.append(f"final decision source hash mismatch: {relative}")
    return problems
