"""Serve-compatible staged VERIFY value-of-compute evidence for M14-G1.

This module is intentionally distinct from :mod:`controller.value_of_compute`.
The older dataset compares separately executed whole-run VERIFY budgets.  M14-G1
measures the intervention the later router can actually buy: after one completed
base VERIFY round, issue one fresh larger-budget `go` on the same managed
processes and the same candidate universe.

The label is whether the shared unanimous-VERIFY decision policy changed.  A
change is descriptive only; it is not correctness, Elo, strength, or utility.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from common.residuals import past_only_features, pv_persistence
from controller.decision import (
    COUNTERFACTUAL_POLICY,
    DecisionEvaluation,
    evaluate_unanimous_verify_policy,
)
from controller.replay import load_manifest, sha256_file
from controller.staged_verification import (
    STAGED_VERIFY_INTERVENTION,
    load_staged_verification_manifest,
    verify_staged_verification_integrity,
)
from controller.value_of_compute import VerifierFeatures, canonical_digest
from controller.verification import load_verification_manifest
from controller.verification_analysis import (
    OWNER_ORDER,
    VerificationAnalysisError,
    load_verification_bundle,
)


STAGED_VALUE_DATASET_SCHEMA_VERSION = 1
STAGED_VALUE_EXTRACTOR_VERSION = "staged-verify-decision-change-v1"


class StagedValueOfComputeError(RuntimeError):
    """Raised when staged VERIFY cannot produce an honest transition row."""


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StagedValueOfComputeError(f"{label} must be a positive integer")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StagedValueOfComputeError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise StagedValueOfComputeError(f"{label} must be finite")
    return number


def _evaluation_payload(evaluation: DecisionEvaluation) -> dict[str, Any]:
    return {
        "policy": evaluation.policy,
        "disposition": evaluation.disposition.as_dict(),
        "move": evaluation.move,
        "source_owner": evaluation.source_owner,
    }


@dataclass(frozen=True)
class StagedVerifyTransition:
    run_id: str
    position_id: str
    position_group: str
    replicate: int
    intervention: str
    base_nodes: int
    extension_nodes: int
    candidate_roots: tuple[str, ...]
    nominees_by_owner: tuple[tuple[str, str], ...]
    base_terminal_by_owner: tuple[tuple[str, str | None], ...]
    extension_terminal_by_owner: tuple[tuple[str, str | None], ...]
    base_evaluation: DecisionEvaluation
    extension_evaluation: DecisionEvaluation
    verifier_features: tuple[VerifierFeatures, ...]
    source_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.run_id or not self.position_id or not self.position_group:
            raise StagedValueOfComputeError("staged transition identities must be non-empty")
        if self.intervention != STAGED_VERIFY_INTERVENTION:
            raise StagedValueOfComputeError(
                f"unsupported staged intervention: {self.intervention!r}"
            )
        if (
            isinstance(self.replicate, bool)
            or not isinstance(self.replicate, int)
            or self.replicate < 0
        ):
            raise StagedValueOfComputeError("replicate must be a non-negative integer")
        _positive_int(self.base_nodes, "base_nodes")
        _positive_int(self.extension_nodes, "extension_nodes")
        if self.extension_nodes <= self.base_nodes:
            raise StagedValueOfComputeError(
                "extension_nodes must be strictly greater than base_nodes"
            )
        if len(self.candidate_roots) != 3 or len(set(self.candidate_roots)) != 3:
            raise StagedValueOfComputeError(
                "staged transition requires exactly three distinct candidates"
            )
        for rows, label in (
            (self.nominees_by_owner, "nominees"),
            (self.base_terminal_by_owner, "base terminals"),
            (self.extension_terminal_by_owner, "extension terminals"),
        ):
            if tuple(owner for owner, _ in rows) != OWNER_ORDER:
                raise StagedValueOfComputeError(
                    f"{label} must use canonical owner order"
                )
        if tuple(item.owner for item in self.verifier_features) != OWNER_ORDER:
            raise StagedValueOfComputeError(
                "verifier features must use canonical owner order"
            )

    @property
    def transition_key(self) -> str:
        return f"same-process:n{self.base_nodes}->n{self.extension_nodes}"

    def feature_payload(self) -> dict[str, Any]:
        """Past-only facts available after the base VERIFY round.

        Extension outcomes, stream hashes and extension terminal moves are
        deliberately unreachable from this payload.
        """

        return {
            "extractor_version": STAGED_VALUE_EXTRACTOR_VERSION,
            "position_group": self.position_group,
            "replicate": self.replicate,
            "intervention": self.intervention,
            "transition": self.transition_key,
            "base_nodes": self.base_nodes,
            "extension_nodes": self.extension_nodes,
            "candidate_roots": list(self.candidate_roots),
            "nominees_by_owner": dict(self.nominees_by_owner),
            "terminal_by_owner": dict(self.base_terminal_by_owner),
            "base_decision": _evaluation_payload(self.base_evaluation),
            "verifiers": [item.as_dict() for item in self.verifier_features],
        }

    @property
    def feature_digest(self) -> str:
        return canonical_digest(self.feature_payload())

    def label_payload(self) -> dict[str, Any]:
        base = _evaluation_payload(self.base_evaluation)
        extension = _evaluation_payload(self.extension_evaluation)
        base_has = self.base_evaluation.move is not None
        extension_has = self.extension_evaluation.move is not None
        return {
            "transition": self.transition_key,
            "extension_terminal_by_owner": dict(self.extension_terminal_by_owner),
            "extension_decision": extension,
            "decision_changed": base != extension,
            "proposal_emerged": (not base_has and extension_has),
            "proposal_disappeared": (base_has and not extension_has),
            "proposal_move_changed": (
                base_has
                and extension_has
                and self.base_evaluation.move != self.extension_evaluation.move
            ),
            "terminal_vector_changed": (
                self.base_terminal_by_owner != self.extension_terminal_by_owner
            ),
            "label_observed": True,
        }

    @property
    def label_digest(self) -> str:
        return canonical_digest(self.label_payload())

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "position_id": self.position_id,
            "position_group": self.position_group,
            "replicate": self.replicate,
            "features": self.feature_payload(),
            "labels": self.label_payload(),
            "feature_digest": self.feature_digest,
            "label_digest": self.label_digest,
            "sources": dict(self.source_hashes),
        }


def _source_hashes(run_dir: Path) -> tuple[tuple[str, str], ...]:
    paths = {
        "parent_manifest": run_dir / "manifest.json",
        "verification_manifest": run_dir / "verification" / "manifest.json",
        "staged_verification_manifest": run_dir / "staged_verification" / "manifest.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise StagedValueOfComputeError(
            f"staged transition is missing source artifacts: {missing}"
        )
    return tuple(
        (name, sha256_file(path))
        for name, path in sorted(paths.items())
    )


def _terminal_from_stages(
    stages: Sequence[dict[str, Any]],
    *,
    label: str,
) -> tuple[tuple[str, str | None], ...]:
    by_owner = {
        str(stage.get("owner")): stage
        for stage in stages
        if isinstance(stage, dict)
    }
    out: list[tuple[str, str | None]] = []
    for owner in OWNER_ORDER:
        stage = by_owner.get(owner)
        if stage is None:
            raise StagedValueOfComputeError(f"{label} is missing {owner} stage")
        if stage.get("disposition") != "completed":
            raise StagedValueOfComputeError(
                f"{label} {owner} stage is not completed"
            )
        move = stage.get("bestmove")
        out.append((owner, move if isinstance(move, str) else None))
    return tuple(out)


def load_staged_transition(
    run_dir: Path | str,
    *,
    position_group: str | None = None,
    replicate: int = 0,
) -> StagedVerifyTransition:
    """Load one completed same-process staged VERIFY intervention."""

    run_dir = Path(run_dir)
    problems = verify_staged_verification_integrity(run_dir)
    if problems:
        raise StagedValueOfComputeError(
            f"staged VERIFY integrity failed for {run_dir}: {problems}"
        )
    try:
        bundle = load_verification_bundle(run_dir)
    except VerificationAnalysisError as exc:
        raise StagedValueOfComputeError(str(exc)) from exc
    if not bundle.analysis_eligible:
        raise StagedValueOfComputeError(
            "base VERIFY is not three-way analysis eligible"
        )

    parent = load_manifest(run_dir)
    verification = load_verification_manifest(run_dir)
    staged = load_staged_verification_manifest(run_dir)
    if (staged.get("disposition") or {}).get("run") != "completed":
        raise StagedValueOfComputeError(
            "staged VERIFY extension is not completed"
        )

    base_limit = verification.get("dispatch_limit") or {}
    rounds = staged.get("rounds") or {}
    extension_limit = (rounds.get("extension") or {}).get("dispatch_limit") or {}
    if set(base_limit) != {"nodes"} or set(extension_limit) != {"nodes"}:
        raise StagedValueOfComputeError(
            "staged value-of-compute v1 requires pure node budgets"
        )
    base_nodes = _positive_int(base_limit.get("nodes"), "base VERIFY nodes")
    extension_nodes = _positive_int(
        extension_limit.get("nodes"),
        "extension VERIFY nodes",
    )

    nomination = verification.get("nomination") or {}
    candidates = tuple(
        str(move) for move in nomination.get("candidate_roots") or ()
    )
    nominees_raw = nomination.get("nominees_by_owner") or {}
    nominees = tuple(
        (owner, str(nominees_raw.get(owner)))
        for owner in OWNER_ORDER
    )
    candidate_owner_by_move = {
        move: owner
        for owner, move in nominees
    }

    base_terminal: list[tuple[str, str | None]] = []
    features: list[VerifierFeatures] = []
    base_stages = {
        str(stage.get("owner")): stage
        for stage in verification.get("stages") or []
        if isinstance(stage, dict)
    }
    for owner in OWNER_ORDER:
        trajectory = bundle.by_owner(owner)
        stage = base_stages.get(owner)
        if trajectory is None or stage is None:
            raise StagedValueOfComputeError(
                f"base VERIFY is missing {owner} trajectory/stage"
            )
        move = trajectory.final_leader
        base_terminal.append((owner, move))
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
                _finite(completed_ms, "base completed_ms")
                - _finite(dispatched_ms, "base dispatched_ms"),
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
                    None if move is None else move == nominees_raw.get(owner)
                ),
                stage_elapsed_ms=elapsed,
                native_work_value=None if work is None else float(work[0]),
                native_work_semantics=None if work is None else str(work[1]),
            )
        )

    base_terminal_tuple = tuple(base_terminal)
    extension_terminal = _terminal_from_stages(
        staged.get("stages") or (),
        label="extension",
    )

    base_policy_digest = canonical_digest(
        {
            "intervention": STAGED_VERIFY_INTERVENTION,
            "round": "base",
            "candidate_roots": candidates,
            "nominees_by_owner": dict(nominees),
            "terminal_by_owner": dict(base_terminal_tuple),
        }
    )
    extension_policy_digest = canonical_digest(
        {
            "intervention": STAGED_VERIFY_INTERVENTION,
            "round": "extension",
            "candidate_roots": candidates,
            "nominees_by_owner": dict(nominees),
            "terminal_by_owner": dict(extension_terminal),
        }
    )
    base_evaluation = evaluate_unanimous_verify_policy(
        candidate_roots=candidates,
        candidate_owner_by_move=candidate_owner_by_move,
        terminal_by_owner=dict(base_terminal_tuple),
        verification_complete=True,
        evidence_faults=(),
        evidence_digest=base_policy_digest,
        policy=COUNTERFACTUAL_POLICY,
    )
    extension_evaluation = evaluate_unanimous_verify_policy(
        candidate_roots=candidates,
        candidate_owner_by_move=candidate_owner_by_move,
        terminal_by_owner=dict(extension_terminal),
        verification_complete=True,
        evidence_faults=(),
        evidence_digest=extension_policy_digest,
        policy=COUNTERFACTUAL_POLICY,
    )

    position_id = str((parent.get("position") or {}).get("position_id") or "")
    group = position_id if position_group is None else str(position_group)
    return StagedVerifyTransition(
        run_id=str(parent.get("run_id") or ""),
        position_id=position_id,
        position_group=group,
        replicate=replicate,
        intervention=str(staged.get("intervention") or ""),
        base_nodes=base_nodes,
        extension_nodes=extension_nodes,
        candidate_roots=candidates,
        nominees_by_owner=nominees,
        base_terminal_by_owner=base_terminal_tuple,
        extension_terminal_by_owner=extension_terminal,
        base_evaluation=base_evaluation,
        extension_evaluation=extension_evaluation,
        verifier_features=tuple(features),
        source_hashes=_source_hashes(run_dir),
    )


def build_dataset(
    transitions: Sequence[StagedVerifyTransition],
) -> dict[str, Any]:
    ordered = sorted(
        transitions,
        key=lambda item: (
            item.position_group,
            item.replicate,
            item.run_id,
        ),
    )
    rows = [item.as_dict() for item in ordered]
    core = {
        "schema_version": STAGED_VALUE_DATASET_SCHEMA_VERSION,
        "extractor_version": STAGED_VALUE_EXTRACTOR_VERSION,
        "intervention": STAGED_VERIFY_INTERVENTION,
        "rows": rows,
        "claim_boundary": (
            "Observed same-process staged VERIFY decision-change labels only; "
            "decision change is not correctness, Elo, strength, or strategic utility."
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            core,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **core,
        "dataset_id": f"staged-voc-v1:{digest[:16]}",
        "content_sha256": digest,
    }


def write_dataset(dataset: dict[str, Any], root: Path | str) -> Path:
    root = Path(root)
    dataset_id = str(dataset.get("dataset_id") or "")
    if not dataset_id.startswith("staged-voc-v1:"):
        raise StagedValueOfComputeError("staged dataset id is malformed")
    target = root / dataset_id.replace(":", "-") / "dataset.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dataset, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_dataset(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagedValueOfComputeError(
            f"cannot load staged value dataset {path}: {exc}"
        ) from exc
    if not isinstance(dataset, dict):
        raise StagedValueOfComputeError("staged value dataset root must be an object")
    core = {
        "schema_version": dataset.get("schema_version"),
        "extractor_version": dataset.get("extractor_version"),
        "intervention": dataset.get("intervention"),
        "rows": dataset.get("rows"),
        "claim_boundary": dataset.get("claim_boundary"),
    }
    digest = hashlib.sha256(
        json.dumps(
            core,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if dataset.get("content_sha256") != digest:
        raise StagedValueOfComputeError("staged value dataset digest mismatch")
    if dataset.get("extractor_version") != STAGED_VALUE_EXTRACTOR_VERSION:
        raise StagedValueOfComputeError("staged value dataset extractor mismatch")
    if dataset.get("intervention") != STAGED_VERIFY_INTERVENTION:
        raise StagedValueOfComputeError("staged value dataset intervention mismatch")
    return dataset
