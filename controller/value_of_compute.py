"""Prospective VERIFY value-of-compute evidence.

This module does not decide chess moves and does not authorize compute.  It
compares *actual completed VERIFY runs* performed at different declared budgets
under the same upstream experimental condition.

The causal question is intentionally narrow:

    Given the evidence available when the lower-budget VERIFY run stopped,
    did buying the declared additional VERIFY budget change the frozen
    counterfactual decision?

A changed decision is not a better decision.  No label in this module is named
or interpreted as correctness, Elo, or move quality.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.residuals import past_only_features, pv_persistence
from controller.counterfactual import (
    load_counterfactual_artifact,
    verify_counterfactual_integrity,
)
from controller.replay import load_manifest, sha256_file
from controller.verification import load_verification_manifest
from controller.verification_analysis import (
    OWNER_ORDER,
    VerificationAnalysisError,
    load_verification_bundle,
)


VALUE_DATASET_SCHEMA_VERSION = 1
VALUE_EXTRACTOR_VERSION = "verify-value-of-compute-v1"


class ValueOfComputeError(RuntimeError):
    """Raised when a VERIFY intervention cannot be compared honestly."""


def canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueOfComputeError(f"value-of-compute payload is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueOfComputeError(f"{label} must be a positive integer")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueOfComputeError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueOfComputeError(f"{label} must be finite")
    return number


@dataclass(frozen=True)
class VerifierFeatures:
    owner: str
    terminal_move: str | None
    observation_count: int
    leader_flips: int
    stable_run_fraction: float
    pv_persistence: float | None
    self_retained: bool | None
    stage_elapsed_ms: float | None
    native_work_value: float | None
    native_work_semantics: str | None

    def __post_init__(self) -> None:
        if self.owner not in OWNER_ORDER:
            raise ValueOfComputeError(f"unknown verifier owner: {self.owner!r}")
        if self.observation_count < 0 or self.leader_flips < 0:
            raise ValueOfComputeError("verifier counts must be non-negative")
        if not 0.0 <= float(self.stable_run_fraction) <= 1.0:
            raise ValueOfComputeError("stable_run_fraction must be in [0,1]")
        for label, value in (
            ("pv_persistence", self.pv_persistence),
            ("stage_elapsed_ms", self.stage_elapsed_ms),
            ("native_work_value", self.native_work_value),
        ):
            if value is not None:
                _finite(value, label)
        if self.native_work_value is not None and not self.native_work_semantics:
            raise ValueOfComputeError(
                "native work value must retain its source semantics tag"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "terminal_move": self.terminal_move,
            "observation_count": self.observation_count,
            "leader_flips": self.leader_flips,
            "stable_run_fraction": self.stable_run_fraction,
            "pv_persistence": self.pv_persistence,
            "self_retained": self.self_retained,
            "stage_elapsed_ms": self.stage_elapsed_ms,
            "native_work": (
                None
                if self.native_work_value is None
                else {
                    "value": self.native_work_value,
                    "semantics": self.native_work_semantics,
                }
            ),
        }


@dataclass(frozen=True)
class VerifyBudgetPoint:
    run_id: str
    position_id: str
    position_group: str
    replicate: int
    verify_nodes: int
    upstream_fingerprint: str
    candidate_roots: tuple[str, ...]
    nominees_by_owner: tuple[tuple[str, str], ...]
    terminal_by_owner: tuple[tuple[str, str | None], ...]
    proposal_disposition: str
    proposal_move: str | None
    proposal_source_owner: str | None
    proposal_pre_anchor: bool
    verifier_features: tuple[VerifierFeatures, ...]
    evidence_complete: bool
    source_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.run_id or not self.position_id or not self.position_group:
            raise ValueOfComputeError("budget point identities must be non-empty")
        if isinstance(self.replicate, bool) or not isinstance(self.replicate, int) or self.replicate < 0:
            raise ValueOfComputeError("replicate must be a non-negative integer")
        _positive_int(self.verify_nodes, "verify_nodes")
        if len(self.upstream_fingerprint) != 64:
            raise ValueOfComputeError("upstream_fingerprint must be SHA-256")
        if len(self.candidate_roots) != 3 or len(set(self.candidate_roots)) != 3:
            raise ValueOfComputeError(
                "value-of-compute v1 requires exactly three VERIFY candidates"
            )
        nominee_owners = tuple(owner for owner, _ in self.nominees_by_owner)
        terminal_owners = tuple(owner for owner, _ in self.terminal_by_owner)
        feature_owners = tuple(item.owner for item in self.verifier_features)
        if nominee_owners != OWNER_ORDER or terminal_owners != OWNER_ORDER or feature_owners != OWNER_ORDER:
            raise ValueOfComputeError(
                "owner-indexed evidence must use canonical stockfish/reckless/lc0 order"
            )
        if not isinstance(self.proposal_disposition, str) or not self.proposal_disposition:
            raise ValueOfComputeError("proposal disposition must be non-empty")
        if not isinstance(self.proposal_pre_anchor, bool):
            raise ValueOfComputeError("proposal_pre_anchor must be boolean")
        if not isinstance(self.evidence_complete, bool):
            raise ValueOfComputeError("evidence_complete must be boolean")

    def feature_payload(self) -> dict[str, Any]:
        """Past-only lower-arm representation used for calibration.

        No upper-budget or full-budget value is reachable through this method.
        """

        return {
            "extractor_version": VALUE_EXTRACTOR_VERSION,
            "position_group": self.position_group,
            "replicate": self.replicate,
            "verify_nodes": self.verify_nodes,
            "upstream_fingerprint": self.upstream_fingerprint,
            "candidate_roots": list(self.candidate_roots),
            "nominees_by_owner": dict(self.nominees_by_owner),
            "terminal_by_owner": dict(self.terminal_by_owner),
            "proposal": {
                "disposition": self.proposal_disposition,
                "move": self.proposal_move,
                "source_owner": self.proposal_source_owner,
                "pre_anchor": self.proposal_pre_anchor,
            },
            "verifiers": [item.as_dict() for item in self.verifier_features],
            "evidence_complete": self.evidence_complete,
        }

    @property
    def feature_digest(self) -> str:
        return canonical_digest(self.feature_payload())

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "position_id": self.position_id,
            **self.feature_payload(),
            "feature_digest": self.feature_digest,
            "source_hashes": dict(self.source_hashes),
        }


@dataclass(frozen=True)
class ComputeTransition:
    lower: VerifyBudgetPoint
    upper: VerifyBudgetPoint
    decision_changed: bool
    proposal_emerged: bool
    proposal_disappeared: bool
    proposal_move_changed: bool
    terminal_vector_changed: bool
    label_observed: bool = True

    def __post_init__(self) -> None:
        if self.lower.position_group != self.upper.position_group:
            raise ValueOfComputeError("transition crosses position groups")
        if self.lower.replicate != self.upper.replicate:
            raise ValueOfComputeError("transition crosses replicate identities")
        if self.lower.upstream_fingerprint != self.upper.upstream_fingerprint:
            raise ValueOfComputeError(
                "transition is not causally eligible: upstream fingerprints differ"
            )
        if self.upper.verify_nodes <= self.lower.verify_nodes:
            raise ValueOfComputeError(
                "transition upper VERIFY budget must exceed lower budget"
            )
        if not self.lower.evidence_complete or not self.upper.evidence_complete:
            raise ValueOfComputeError(
                "transition requires complete lower and upper evidence"
            )

    @property
    def transition_key(self) -> str:
        return f"n{self.lower.verify_nodes}->n{self.upper.verify_nodes}"

    @property
    def additional_nodes_per_owner(self) -> int:
        return self.upper.verify_nodes - self.lower.verify_nodes

    @property
    def feature_digest(self) -> str:
        return self.lower.feature_digest

    def label_payload(self) -> dict[str, Any]:
        return {
            "transition": self.transition_key,
            "upper_run_id": self.upper.run_id,
            "decision_changed": self.decision_changed,
            "proposal_emerged": self.proposal_emerged,
            "proposal_disappeared": self.proposal_disappeared,
            "proposal_move_changed": self.proposal_move_changed,
            "terminal_vector_changed": self.terminal_vector_changed,
            "label_observed": self.label_observed,
        }

    @property
    def label_digest(self) -> str:
        return canonical_digest(self.label_payload())

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_group": self.lower.position_group,
            "replicate": self.lower.replicate,
            "transition": self.transition_key,
            "additional_nodes_per_owner": self.additional_nodes_per_owner,
            "lower": self.lower.as_dict(),
            "upper_run_id": self.upper.run_id,
            "upper_verify_nodes": self.upper.verify_nodes,
            "labels": self.label_payload(),
            "feature_digest": self.feature_digest,
            "label_digest": self.label_digest,
        }


def _upstream_payload(
    parent: dict[str, Any],
    verification: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Everything that must stay fixed when VERIFY budget is the intervention."""

    parent_stages = []
    for stage in parent.get("stages") or []:
        if not isinstance(stage, dict) or stage.get("role") != "shadow":
            continue
        parent_stages.append(
            {
                "instance": stage.get("instance"),
                "family": stage.get("family"),
                "role": stage.get("role"),
                "owner": stage.get("owner"),
                "command": stage.get("command"),
                "dispatched_roots": stage.get("dispatched_roots"),
                "stage_index": stage.get("stage_index"),
            }
        )
    parent_stages.sort(
        key=lambda row: (
            str(row.get("owner")),
            int(row.get("stage_index") or 0),
            str(row.get("instance")),
        )
    )

    nomination = verification.get("nomination") or {}
    return {
        "position": {
            key: (parent.get("position") or {}).get(key)
            for key in ("position_id", "variant", "base_fen", "moves", "command")
        },
        "external_request": parent.get("external_request"),
        "engines": parent.get("engines"),
        "partition_method": (parent.get("controller") or {}).get("partition_method"),
        "owner_roots": (parent.get("ledger") or {}).get("owner_roots"),
        "explore_stages": parent_stages,
        "verification": {
            "nomination_method": nomination.get("method"),
            "nominees_by_owner": nomination.get("nominees_by_owner"),
            "candidate_roots": nomination.get("candidate_roots"),
            "participants": verification.get("participants"),
        },
        "crossfeed_policy": (
            (decision.get("source") or {}).get("crossfeed_id", "").split(":crossfeed-v1:")[0]
            and "typed_verify_refine_v1"
        ),
        "decision_policy": decision.get("policy"),
    }


def _source_hashes(run_dir: Path) -> tuple[tuple[str, str], ...]:
    paths = {
        "parent_manifest": run_dir / "manifest.json",
        "verification_manifest": run_dir / "verification" / "manifest.json",
        "crossfeed_manifest": run_dir / "crossfeed" / "manifest.json",
        "counterfactual_artifact": run_dir / "decision" / "counterfactual.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueOfComputeError(f"budget point is missing source artifacts: {missing}")
    return tuple((name, sha256_file(path)) for name, path in sorted(paths.items()))


def load_budget_point(
    run_dir: Path | str,
    *,
    position_group: str | None = None,
    replicate: int = 0,
) -> VerifyBudgetPoint:
    """Load one completed real VERIFY arm into a past-only budget point."""

    run_dir = Path(run_dir)
    problems = verify_counterfactual_integrity(run_dir)
    if problems:
        raise ValueOfComputeError(
            f"counterfactual integrity failed for {run_dir}: {problems}"
        )
    try:
        bundle = load_verification_bundle(run_dir)
    except VerificationAnalysisError as exc:
        raise ValueOfComputeError(str(exc)) from exc
    if not bundle.analysis_eligible:
        raise ValueOfComputeError("VERIFY arm is not three-way analysis eligible")

    parent = load_manifest(run_dir)
    verification = load_verification_manifest(run_dir)
    decision = load_counterfactual_artifact(run_dir)

    dispatch_limit = verification.get("dispatch_limit") or {}
    nodes = _positive_int(dispatch_limit.get("nodes"), "verification.dispatch_limit.nodes")
    if set(dispatch_limit) != {"nodes"}:
        raise ValueOfComputeError(
            "value-of-compute v1 requires a pure VERIFY nodes intervention"
        )

    nomination = verification.get("nomination") or {}
    candidates = tuple(str(move) for move in nomination.get("candidate_roots") or ())
    nominees_raw = nomination.get("nominees_by_owner") or {}
    nominees = tuple((owner, str(nominees_raw.get(owner))) for owner in OWNER_ORDER)

    terminal: list[tuple[str, str | None]] = []
    features: list[VerifierFeatures] = []
    stages = {
        str(stage.get("owner")): stage
        for stage in verification.get("stages") or []
        if isinstance(stage, dict)
    }
    for owner in OWNER_ORDER:
        trajectory = bundle.by_owner(owner)
        stage = stages.get(owner)
        if trajectory is None or stage is None:
            raise ValueOfComputeError(f"VERIFY arm is missing {owner} trajectory/stage")
        move = trajectory.final_leader
        terminal.append((owner, move))
        primary_moves = trajectory.primary_moves_until(trajectory.span_ms)
        history = past_only_features(primary_moves)
        primary_pvs = [
            item.pv
            for item in trajectory.observations
            if item.multipv_index == 1 and item.pv
        ]
        work = trajectory.work_at(trajectory.span_ms)
        dispatched_ms = stage.get("dispatched_ms")
        completed_ms = stage.get("completed_ms")
        elapsed: float | None = None
        if dispatched_ms is not None and completed_ms is not None:
            elapsed = max(
                0.0,
                _finite(completed_ms, "completed_ms")
                - _finite(dispatched_ms, "dispatched_ms"),
            )
        features.append(
            VerifierFeatures(
                owner=owner,
                terminal_move=move,
                observation_count=history.observation_count,
                leader_flips=history.leader_flips,
                stable_run_fraction=history.stable_run_fraction,
                pv_persistence=pv_persistence(primary_pvs),
                self_retained=(
                    None if move is None else move == dict(nominees).get(owner)
                ),
                stage_elapsed_ms=elapsed,
                native_work_value=None if work is None else float(work[0]),
                native_work_semantics=None if work is None else str(work[1]),
            )
        )

    proposal = decision.get("proposal") or {}
    disposition = proposal.get("disposition") or {}
    if not isinstance(disposition, dict):
        raise ValueOfComputeError("counterfactual proposal disposition is malformed")

    upstream = canonical_digest(_upstream_payload(parent, verification, decision))
    position_id = str((parent.get("position") or {}).get("position_id") or "")
    group = position_id if position_group is None else str(position_group)
    evidence_complete = (
        bool(bundle.analysis_eligible)
        and not bundle.load_errors
        and (verification.get("disposition") or {}).get("run") == "completed"
    )

    return VerifyBudgetPoint(
        run_id=str(parent.get("run_id") or ""),
        position_id=position_id,
        position_group=group,
        replicate=replicate,
        verify_nodes=nodes,
        upstream_fingerprint=upstream,
        candidate_roots=candidates,
        nominees_by_owner=nominees,
        terminal_by_owner=tuple(terminal),
        proposal_disposition=str(disposition.get("code") or ""),
        proposal_move=(
            proposal.get("move") if isinstance(proposal.get("move"), str) else None
        ),
        proposal_source_owner=(
            proposal.get("source_owner")
            if isinstance(proposal.get("source_owner"), str)
            else None
        ),
        proposal_pre_anchor=proposal.get("frozen_before_anchor") is True,
        verifier_features=tuple(features),
        evidence_complete=evidence_complete,
        source_hashes=_source_hashes(run_dir),
    )


def build_transition(
    lower: VerifyBudgetPoint,
    upper: VerifyBudgetPoint,
) -> ComputeTransition:
    """Build one observed lower->upper VERIFY intervention label."""

    lower_decision = (lower.proposal_disposition, lower.proposal_move)
    upper_decision = (upper.proposal_disposition, upper.proposal_move)
    lower_has = lower.proposal_move is not None
    upper_has = upper.proposal_move is not None
    return ComputeTransition(
        lower=lower,
        upper=upper,
        decision_changed=lower_decision != upper_decision,
        proposal_emerged=(not lower_has and upper_has),
        proposal_disappeared=(lower_has and not upper_has),
        proposal_move_changed=(
            lower_has and upper_has and lower.proposal_move != upper.proposal_move
        ),
        terminal_vector_changed=lower.terminal_by_owner != upper.terminal_by_owner,
        label_observed=True,
    )


def _decision_signature(point: VerifyBudgetPoint) -> tuple[str, str | None]:
    return (point.proposal_disposition, point.proposal_move)


def build_transitions(
    points: Sequence[VerifyBudgetPoint],
    *,
    adjacent_only: bool = True,
) -> tuple[ComputeTransition, ...]:
    """Pair only causally eligible arms from the same position/replicate."""

    groups: dict[tuple[str, int, str], list[VerifyBudgetPoint]] = {}
    for point in points:
        key = (point.position_group, point.replicate, point.upstream_fingerprint)
        groups.setdefault(key, []).append(point)

    transitions: list[ComputeTransition] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda item: item.verify_nodes)
        seen: set[int] = set()
        for point in ordered:
            if point.verify_nodes in seen:
                raise ValueOfComputeError(
                    f"duplicate VERIFY budget {point.verify_nodes} for group {key}"
                )
            seen.add(point.verify_nodes)
        pairs = zip(ordered, ordered[1:]) if adjacent_only else (
            (ordered[i], ordered[j])
            for i in range(len(ordered))
            for j in range(i + 1, len(ordered))
        )
        for lower, upper in pairs:
            transitions.append(build_transition(lower, upper))
    return tuple(transitions)


def full_budget_labels(
    points: Sequence[VerifyBudgetPoint],
) -> list[dict[str, Any]]:
    """Attach future-facing stabilization labels without contaminating features."""

    groups: dict[tuple[str, int, str], list[VerifyBudgetPoint]] = {}
    for point in points:
        key = (point.position_group, point.replicate, point.upstream_fingerprint)
        groups.setdefault(key, []).append(point)

    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda item: item.verify_nodes)
        if len(ordered) < 2:
            continue
        full = ordered[-1]
        full_signature = _decision_signature(full)
        for index, point in enumerate(ordered[:-1]):
            later = ordered[index + 1 :]
            proposed = point.proposal_move
            rows.append(
                {
                    "position_group": point.position_group,
                    "replicate": point.replicate,
                    "upstream_fingerprint": point.upstream_fingerprint,
                    "checkpoint_nodes": point.verify_nodes,
                    "full_budget_nodes": full.verify_nodes,
                    "feature_digest": point.feature_digest,
                    "candidate_survived_full_budget": (
                        None if proposed is None else proposed == full.proposal_move
                    ),
                    "decision_stabilized": all(
                        _decision_signature(item) == full_signature for item in later
                    )
                    and _decision_signature(point) == full_signature,
                }
            )
    return rows


def build_dataset(
    points: Sequence[VerifyBudgetPoint],
    *,
    adjacent_only: bool = True,
) -> dict[str, Any]:
    transitions = build_transitions(points, adjacent_only=adjacent_only)
    sources = [
        {
            "run_id": point.run_id,
            "position_group": point.position_group,
            "replicate": point.replicate,
            "verify_nodes": point.verify_nodes,
            "upstream_fingerprint": point.upstream_fingerprint,
            "source_hashes": dict(point.source_hashes),
        }
        for point in sorted(
            points,
            key=lambda item: (
                item.position_group,
                item.replicate,
                item.verify_nodes,
                item.run_id,
            ),
        )
    ]
    core = {
        "schema_version": VALUE_DATASET_SCHEMA_VERSION,
        "extractor_version": VALUE_EXTRACTOR_VERSION,
        "sources": sources,
        "transitions": [item.as_dict() for item in transitions],
        "full_budget_labels": full_budget_labels(points),
    }
    dataset_id = f"voc-{canonical_digest(core)[:16]}"
    return {
        **core,
        "dataset_id": dataset_id,
        "claim": (
            "Prospective intervention labels only: decision_changed means a "
            "larger completed VERIFY budget changed the frozen counterfactual "
            "decision under a matching upstream fingerprint. It does not mean "
            "the later move was better or correct."
        ),
    }


def write_dataset(dataset: dict[str, Any], root: Path | str) -> Path:
    root = Path(root)
    dataset_id = dataset.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueOfComputeError("dataset is missing dataset_id")
    target = root / dataset_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "dataset.json"
    path.write_text(
        json.dumps(dataset, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_dataset(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "dataset.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueOfComputeError(f"cannot load value-of-compute dataset {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueOfComputeError("value-of-compute dataset root must be an object")
    if data.get("schema_version") != VALUE_DATASET_SCHEMA_VERSION:
        raise ValueOfComputeError("unsupported value-of-compute dataset schema")
    if data.get("extractor_version") != VALUE_EXTRACTOR_VERSION:
        raise ValueOfComputeError("value-of-compute extractor version mismatch")
    core = {
        key: value
        for key, value in data.items()
        if key not in {"dataset_id", "claim"}
    }
    expected = f"voc-{canonical_digest(core)[:16]}"
    if data.get("dataset_id") != expected:
        raise ValueOfComputeError(
            f"dataset declares {data.get('dataset_id')!r} but contents address to {expected!r}"
        )
    return data


def points_from_collection(entries: Iterable[dict[str, Any]]) -> tuple[VerifyBudgetPoint, ...]:
    points: list[VerifyBudgetPoint] = []
    for entry in entries:
        run_dir = entry.get("run_dir")
        if not isinstance(run_dir, (str, Path)):
            raise ValueOfComputeError("collection entry is missing run_dir")
        points.append(
            load_budget_point(
                Path(run_dir),
                position_group=str(entry.get("position_group") or ""),
                replicate=int(entry.get("replicate", 0)),
            )
        )
    return tuple(points)
