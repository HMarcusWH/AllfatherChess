"""Persistence and deterministic audit for the actual outward decision.

The live decision is selected entirely in memory on the anchor-completion path.
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
_CLOCKED_POLICY = "clocked_staged_preanchor_v1"

_BASE_SOURCE_PATHS = (
    "manifest.json",
    "route.json",
    "resource.json",
)
_M14C_EVIDENCE_PATHS = (
    "verification/manifest.json",
    "crossfeed/manifest.json",
    "decision/counterfactual.json",
)
_STAGED_PATH = "staged_verification/manifest.json"
_ALL_KNOWN_SOURCE_PATHS = (
    *_BASE_SOURCE_PATHS,
    *_M14C_EVIDENCE_PATHS,
    _STAGED_PATH,
)


class FinalDecisionError(RuntimeError):
    """Raised when final-decision evidence cannot be sealed or verified."""


def _required_source_paths_from_payload(decision: dict[str, Any]) -> tuple[str, ...]:
    """Infer which sealed artifacts the selected authority actually consumed."""

    authorization = decision.get("authorization")
    snapshot = decision.get("authorization_snapshot")
    if not isinstance(authorization, dict) or not isinstance(snapshot, dict):
        return (*_BASE_SOURCE_PATHS, *_M14C_EVIDENCE_PATHS)

    if authorization.get("policy") != _CLOCKED_POLICY:
        # Frozen M14-C v0 compatibility: these sources were historically
        # mandatory for every live hybrid-authority artifact.
        return (*_BASE_SOURCE_PATHS, *_M14C_EVIDENCE_PATHS)

    required = list(_BASE_SOURCE_PATHS)
    # A granted G3 decision must be reconstructible from the complete staged
    # terminal plane. A denied early fallback may legitimately have reached the
    # anchor boundary before VERIFY/cross-feed/counterfactual artifacts existed.
    if (
        authorization.get("authorized") is True
        or snapshot.get("terminal_source") == "staged_verification"
        or snapshot.get("staged_complete") is True
    ):
        required.extend(
            (
                "verification/manifest.json",
                _STAGED_PATH,
                "crossfeed/manifest.json",
                "decision/counterfactual.json",
            )
        )
    return tuple(required)


def _source_hashes(
    run_dir: Path,
    decision: FinalDecision,
) -> tuple[dict[str, str], tuple[str, ...]]:
    payload = decision.as_dict()
    required = set(_required_source_paths_from_payload(payload))
    sources: dict[str, str] = {}
    missing: list[str] = []

    for relative in _ALL_KNOWN_SOURCE_PATHS:
        path = run_dir / relative
        if path.is_file():
            sources[relative] = sha256_file(path)
        elif relative in required:
            missing.append(relative)
    return sources, tuple(sorted(missing))


def seal_final_decision_artifact(
    decision: FinalDecision,
    run_dir: Path | str,
) -> dict[str, Any]:
    """Seal the actual selected authority after the source artifacts finalize."""

    run_dir = Path(run_dir)
    sources, missing_sources = _source_hashes(run_dir, decision)
    core = {
        "schema_version": FINAL_DECISION_SCHEMA_VERSION,
        "decision": decision.as_dict(),
        "sources": sources,
        "missing_sources": list(missing_sources),
        "audit_complete": not missing_sources,
        "semantics": {
            "authorization_timing": "bounded in-memory at anchor completion",
            "terminal_resource_timing": (
                "post-output; may qualify claims but cannot rewrite the played move"
            ),
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
        raise FinalDecisionError(
            f"cannot load final decision artifact {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise FinalDecisionError("final decision artifact root must be an object")
    if data.get("schema_version") != FINAL_DECISION_SCHEMA_VERSION:
        raise FinalDecisionError(
            "unsupported final decision schema_version: "
            f"{data.get('schema_version')!r}"
        )
    return data


def _load_json_source(
    run_dir: Path,
    relative: str,
    problems: list[str],
) -> dict[str, Any] | None:
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


def _verify_clocked_authority(
    run_dir: Path,
    decision: dict[str, Any],
    authorization: dict[str, Any],
    snapshot: dict[str, Any],
    problems: list[str],
) -> None:
    """Reconstruct G3 identity gates from the independently sealed artifacts."""

    parent = _load_json_source(run_dir, "manifest.json", problems)
    route = _load_json_source(run_dir, "route.json", problems)
    if parent is None or route is None:
        return

    if parent.get("outward_decision") != decision:
        problems.append("G3 replay outward_decision differs from final decision")

    plan = parent.get("time_plan")
    if not isinstance(plan, dict):
        problems.append("G3 parent replay is missing TimePlan")
    else:
        checks = (
            ("time_plan_id", "plan_id", "TimePlan id"),
            ("time_plan_request_class", "request_class", "TimePlan request class"),
            ("time_plan_generation", "generation", "TimePlan generation"),
            ("time_plan_position_id", "position_id", "TimePlan position"),
            (
                "time_plan_anchor_go_command",
                "anchor_go_command",
                "TimePlan anchor command",
            ),
        )
        for snapshot_key, plan_key, label in checks:
            if snapshot.get(snapshot_key) != plan.get(plan_key):
                problems.append(f"G3 {label} mismatch")

    if snapshot.get("time_plan_generation") != parent.get("generation"):
        problems.append("G3 TimePlan generation does not match parent generation")
    position = parent.get("position")
    if (
        not isinstance(position, dict)
        or snapshot.get("time_plan_position_id") != position.get("position_id")
    ):
        problems.append("G3 TimePlan position does not match parent replay")

    route_digest = snapshot.get("route_decision_digest")
    value_decisions = route.get("value_decisions")
    route_matches: list[dict[str, Any]] = []
    if isinstance(value_decisions, list) and isinstance(route_digest, str):
        route_matches = [
            item
            for item in value_decisions
            if isinstance(item, dict) and canonical_digest(item) == route_digest
        ]

    authorized = authorization.get("authorized") is True
    if authorized and (
        not isinstance(route_digest, str)
        or len(route_digest) != 64
    ):
        problems.append(
            "authorized G3 decision is missing a valid route decision digest"
        )
    elif route_digest is not None:
        if len(route_matches) != 1:
            problems.append(
                "G3 route decision digest does not identify exactly one sealed route"
            )
        else:
            chosen = route_matches[0]
            if chosen.get("action") != snapshot.get("route_action"):
                problems.append("G3 route action differs from authorization snapshot")
            if chosen.get("buy_extension") is not snapshot.get(
                "route_buy_extension"
            ):
                problems.append(
                    "G3 route buy flag differs from authorization snapshot"
                )

    if authorized:
        if decision.get("authority") != "HYBRID":
            problems.append("authorized G3 decision is not marked HYBRID")

        granted_move = authorization.get("move")
        proposal_move = decision.get("proposal_move")
        emitted_move = decision.get("emitted_move")
        if not isinstance(granted_move, str) or not granted_move:
            problems.append("authorized G3 decision is missing the granted move")
        else:
            if proposal_move != granted_move:
                problems.append(
                    "authorized G3 proposal move differs from the granted move"
                )
            if emitted_move != granted_move:
                problems.append(
                    "authorized G3 emitted move differs from the granted move"
                )
        if snapshot.get("route_action") != "BUY_STAGED_VERIFY":
            problems.append("authorized G3 decision did not bind BUY_STAGED_VERIFY")
        if snapshot.get("route_buy_extension") is not True:
            problems.append("authorized G3 decision did not bind extension purchase")
        if snapshot.get("terminal_source") != "staged_verification":
            problems.append(
                "authorized G3 decision did not bind staged terminal source"
            )
        if snapshot.get("staged_complete") is not True:
            problems.append("authorized G3 decision did not bind completed staged VERIFY")

        staged = _load_json_source(run_dir, _STAGED_PATH, problems)
        counterfactual = _load_json_source(
            run_dir, "decision/counterfactual.json", problems
        )
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
            stages = staged.get("stages")
            staged_complete = bool(
                (staged.get("disposition") or {}).get("run") == "completed"
                and isinstance(stages, list)
                and len(stages) == 3
                and all(
                    isinstance(stage, dict)
                    and stage.get("disposition") == "completed"
                    for stage in stages
                )
            )
            if not staged_complete:
                problems.append("G3 authority source staged VERIFY is not completed")

        if counterfactual is not None:
            source = counterfactual.get("source") or {}
            if (
                source.get("decision_terminal_source")
                != snapshot.get("terminal_source")
            ):
                problems.append("G3 counterfactual terminal source mismatch")
    else:
        # Denied G3 authority is itself a valid outcome. It must preserve the
        # exact anchor and may occur before specialist-derived artifacts exist.
        if decision.get("authority") != "ANCHOR_FALLBACK":
            problems.append("denied G3 authorization is not ANCHOR_FALLBACK")
        if decision.get("emitted_move") != decision.get("anchor_move"):
            problems.append("denied G3 authorization changed the anchor move")


def verify_final_decision_integrity(run_dir: Path | str) -> list[str]:
    """Check self-digest, source hashes, and versioned authority provenance."""

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
    if artifact.get("content_sha256") != canonical_digest(core):
        problems.append("final decision content digest mismatch")

    decision = artifact.get("decision")
    authorization: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    if isinstance(decision, dict):
        raw_authorization = decision.get("authorization")
        raw_snapshot = decision.get("authorization_snapshot")
        if isinstance(raw_authorization, dict) and isinstance(raw_snapshot, dict):
            authorization = raw_authorization
            snapshot = raw_snapshot
            if authorization.get("snapshot_digest") != canonical_digest(snapshot):
                problems.append(
                    "final decision authorization snapshot digest mismatch"
                )
        else:
            problems.append("final decision authorization evidence is incomplete")
    else:
        problems.append("final decision decision payload must be an object")

    stored_sources = artifact.get("sources")
    if not isinstance(stored_sources, dict):
        problems.append("final decision sources must be an object")
        stored_sources = {}
    else:
        for relative, stored_hash in stored_sources.items():
            path = run_dir / str(relative)
            if not path.is_file():
                problems.append(f"final decision source missing: {relative}")
                continue
            if sha256_file(path) != stored_hash:
                problems.append(f"final decision source hash mismatch: {relative}")

    missing_sources = artifact.get("missing_sources")
    if not isinstance(missing_sources, list):
        problems.append("final decision missing_sources must be an array")
        missing_sources = []
    elif missing_sources:
        problems.append(
            "final decision audit sources missing: "
            + ", ".join(map(str, missing_sources))
        )
    if artifact.get("audit_complete") is not (not bool(missing_sources)):
        problems.append("final decision audit_complete is inconsistent")

    if isinstance(decision, dict):
        required = _required_source_paths_from_payload(decision)
        for relative in required:
            if relative not in stored_sources:
                problems.append(
                    f"final decision required source is not hash-bound: {relative}"
                )

    if (
        isinstance(decision, dict)
        and authorization is not None
        and snapshot is not None
        and authorization.get("policy") == _CLOCKED_POLICY
    ):
        _verify_clocked_authority(
            run_dir,
            decision,
            authorization,
            snapshot,
            problems,
        )

    return problems
