"""Shared J10 allocation feature extraction from past-only base VERIFY evidence.

This module intentionally uses public lower-level primitives rather than
importing private helpers from unified_value_router.py.  It is decision-inert:
it extracts evidence and structural regime context but grants neither compute
nor move authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.crossfeed.evidence import (
    CrossFeedAdapterEvidenceError,
    build_adapter_evidence,
)
from common.residuals import past_only_features, pv_persistence
from common.search_request import SearchRequestError, parse_go_request
from controller.crossfeed import CrossFeedError, build_crossfeed_view
from controller.decision import (
    COUNTERFACTUAL_POLICY,
    canonical_digest,
    evaluate_unanimous_verify_policy,
)
from controller.regime_calibration import RegimeSupportModel
from controller.regimes import (
    OWNER_ORDER,
    RefinementRegimeFeatures,
    RegimeClassification,
    RegimeObservation,
    TimingRegimeFeatures,
    VerifierRegimeFeatures,
    classify_regimes,
)
from controller.replay_analysis import SearchTrajectory, reconstruct_stream
from controller.verification import VerificationRun
from controller.verification_analysis import derive_relock


class AllocationFeatureError(RuntimeError):
    """Live J10 features could not be reconstructed honestly."""


def staged_serving_features(
    *,
    transition: str,
    disposition: str,
    verifiers: tuple[VerifierRegimeFeatures, ...],
) -> dict[str, Any]:
    """Shared semantic surface for the staged decision-change model.

    This intentionally mirrors the already-qualified G2 serving feature
    semantics without importing unified_value_router private helpers.
    """

    if not verifiers:
        raise AllocationFeatureError("staged serving features require verifiers")
    return {
        "transition": transition,
        "base_decision_disposition": disposition,
        "min_observation_count": min(
            item.observation_count for item in verifiers
        ),
        "max_leader_flips": max(
            item.leader_flips for item in verifiers
        ),
        "min_stable_run_fraction": min(
            item.stable_run_fraction for item in verifiers
        ),
    }


@dataclass(frozen=True)
class AllocationFeatures:
    staged_features: dict[str, Any]
    regime_observation: RegimeObservation
    regime_classification: RegimeClassification
    terminal_by_owner: tuple[tuple[str, str], ...]
    candidate_roots: tuple[str, ...]

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "extractor": "adaptive-resource-features-v1",
            "staged_features": self.staged_features,
            "regime_observation_digest": self.regime_observation.digest,
            "regime": self.regime_classification.as_dict(),
            "candidate_roots": list(self.candidate_roots),
            "terminal_by_owner": dict(self.terminal_by_owner),
            "authority": {
                "allocation_nomination": False,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload)


class _LiveVerificationBundle:
    """Duck type used by derive_relock over already reconstructed trajectories."""

    def __init__(
        self,
        candidate_roots: tuple[str, ...],
        trajectories: tuple[SearchTrajectory, ...],
    ) -> None:
        self.candidate_roots = candidate_roots
        self.trajectories = trajectories

    def by_owner(self, owner: str) -> SearchTrajectory | None:
        return next(
            (
                trajectory
                for trajectory in self.trajectories
                if trajectory.owner == owner
            ),
            None,
        )

    @property
    def analysis_eligible(self) -> bool:
        if len(self.trajectories) != len(OWNER_ORDER):
            return False
        for owner in OWNER_ORDER:
            trajectory = self.by_owner(owner)
            if (
                trajectory is None
                or not trajectory.complete
                or trajectory.parse_errors
                or trajectory.final_leader not in self.candidate_roots
            ):
                return False
        return True


def _timing_features(command: str) -> TimingRegimeFeatures:
    try:
        request = parse_go_request(command)
    except SearchRequestError:
        return TimingRegimeFeatures(
            request_mode="unbounded_or_unknown",
            limits=(),
        )
    limits: list[tuple[str, int | bool]] = []
    for item in request.get("limits") or ():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and (
            isinstance(value, bool)
            or (
                isinstance(value, int)
                and not isinstance(value, bool)
            )
        ):
            limits.append((name, value))
    names = {name for name, _ in limits}
    if "movetime" in names:
        mode = "movetime"
    elif names.intersection(
        {"wtime", "btime", "winc", "binc", "movestogo"}
    ):
        mode = "clock"
    elif "nodes" in names:
        mode = "nodes"
    elif names:
        mode = "other"
    else:
        mode = "unbounded_or_unknown"
    return TimingRegimeFeatures(
        request_mode=mode,
        limits=tuple(limits),
    )


def _mate_alarm_families(evidence: Any) -> tuple[str, ...]:
    families: set[str] = set()
    for hint in evidence.hints:
        if hint.stage_disposition != "completed":
            continue
        if any(
            evaluation.kind == "mate"
            and evaluation.semantics.startswith(
                f"{hint.source_family}."
            )
            for evaluation in hint.evaluations
        ):
            families.add(hint.source_family)
    return tuple(
        owner for owner in OWNER_ORDER if owner in families
    )


def _trajectory_for_owner(
    verification: VerificationRun,
    owner: str,
) -> SearchTrajectory:
    stage = verification.stage_for_owner(owner)
    if stage is None or stage.disposition != "completed":
        raise AllocationFeatureError(
            f"base VERIFY stage for {owner} is not cleanly completed"
        )
    stream = verification.stream(stage.instance)
    if stream is None:
        raise AllocationFeatureError(
            f"base VERIFY stream for {owner} is missing"
        )
    if not stream.drain_barrier(0.25):
        raise AllocationFeatureError(
            f"base VERIFY stream for {owner} did not drain"
        )
    if stream.evidence_lossy or stream.tracked_events_truncated:
        raise AllocationFeatureError(
            f"base VERIFY live evidence for {owner} is incomplete"
        )
    if stream.pending_events():
        raise AllocationFeatureError(
            f"base VERIFY stream for {owner} still has pending events"
        )
    trajectories = reconstruct_stream(
        stream.tracked_events(),
        instance=stage.instance,
        family=stage.family,
        role="shadow",
        owner_roots={owner: verification.plan.candidate_roots},
    )
    matches = [
        item for item in trajectories if item.owner == owner
    ]
    if len(matches) != 1:
        raise AllocationFeatureError(
            f"base VERIFY for {owner} reconstructed "
            f"{len(matches)} trajectories"
        )
    trajectory = matches[0]
    if (
        trajectory.search_id != stage.search_id
        or not trajectory.complete
        or trajectory.parse_errors
        or trajectory.final_leader != stage.bestmove
        or trajectory.final_leader
        not in verification.plan.candidate_roots
    ):
        raise AllocationFeatureError(
            f"base VERIFY terminal evidence is inconsistent for {owner}"
        )
    return trajectory


def build_allocation_features(
    *,
    context: Any,
    verification: VerificationRun,
    extension_nodes: int,
    regime_model: RegimeSupportModel | None = None,
) -> AllocationFeatures:
    if verification.disposition != "completed":
        raise AllocationFeatureError(
            "base VERIFY must be completed"
        )
    if (
        isinstance(extension_nodes, bool)
        or not isinstance(extension_nodes, int)
        or extension_nodes <= 0
    ):
        raise AllocationFeatureError(
            "extension_nodes must be positive integer"
        )

    trajectories = tuple(
        _trajectory_for_owner(verification, owner)
        for owner in OWNER_ORDER
    )
    plan = verification.plan
    candidate_owner_by_move = {
        str(plan.nominees_by_owner[owner]): owner
        for owner in OWNER_ORDER
    }
    terminal_by_owner = tuple(
        (
            owner,
            str(verification.stage_for_owner(owner).bestmove),
        )
        for owner in OWNER_ORDER
    )
    evidence_digest = canonical_digest(
        {
            "route_policy": "adaptive_resource_v1",
            "run_id": context.run_id,
            "verification_id": plan.verification_id,
            "candidate_roots": list(plan.candidate_roots),
            "terminal_by_owner": dict(terminal_by_owner),
        }
    )
    evaluation = evaluate_unanimous_verify_policy(
        candidate_roots=tuple(plan.candidate_roots),
        candidate_owner_by_move=candidate_owner_by_move,
        terminal_by_owner=dict(terminal_by_owner),
        verification_complete=True,
        evidence_faults=(),
        evidence_digest=evidence_digest,
        policy=COUNTERFACTUAL_POLICY,
    )

    verifier_rows: list[VerifierRegimeFeatures] = []
    for owner, trajectory in zip(OWNER_ORDER, trajectories):
        history = past_only_features(
            trajectory.primary_moves_until(trajectory.span_ms)
        )
        pvs = [
            item.pv
            for item in trajectory.observations
            if item.multipv_index == 1 and item.pv
        ]
        verifier_rows.append(
            VerifierRegimeFeatures(
                owner=owner,
                terminal_move=trajectory.final_leader,
                observation_count=history.observation_count,
                leader_flips=history.leader_flips,
                stable_run_fraction=history.stable_run_fraction,
                pv_persistence=pv_persistence(pvs),
            )
        )

    base_nodes = int(plan.dispatch_limit["nodes"])
    staged_features = staged_serving_features(
        transition=(
            f"same-process:n{base_nodes}->n{extension_nodes}"
        ),
        disposition=evaluation.disposition.code,
        verifiers=tuple(verifier_rows),
    )

    try:
        view = build_crossfeed_view(
            run_id=context.run_id,
            generation=context.generation,
            position_id=context.position.position_id,
            verification=verification,
            refinement=None,
        )
        adapter_evidence = build_adapter_evidence(view)
    except (CrossFeedError, CrossFeedAdapterEvidenceError) as exc:
        raise AllocationFeatureError(str(exc)) from exc
    if adapter_evidence.evidence_faults:
        raise AllocationFeatureError(
            "live adapter evidence is faulted: "
            + "; ".join(adapter_evidence.evidence_faults)
        )

    live_bundle = _LiveVerificationBundle(
        tuple(plan.candidate_roots),
        trajectories,
    )
    relock = derive_relock(live_bundle)
    distinct = len({move for _, move in terminal_by_owner})
    pattern = (
        "unanimous"
        if distinct == 1
        else "two_one"
        if distinct == 2
        else "all_different"
    )

    regime_verifiers = tuple(verifier_rows)
    observation = RegimeObservation(
        run_id=context.run_id,
        generation=context.generation,
        position_id=context.position.position_id,
        candidate_roots=tuple(plan.candidate_roots),
        adapter_evidence_digest=adapter_evidence.digest,
        source_hashes=(),
        verify_terminal_by_owner=terminal_by_owner,
        verify_pattern=pattern,
        relock_status=relock.status,
        relock_fraction=relock.relock_fraction,
        verifiers=regime_verifiers,
        native_mate_alarm_families=_mate_alarm_families(
            adapter_evidence
        ),
        refinement=RefinementRegimeFeatures(
            present=False,
            run_disposition=None,
            max_depth=None,
            max_expansions=None,
            target_count=0,
            completed_nonterminal_targets=0,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=(),
        ),
        timing=_timing_features(context.external_go_command),
    )
    domain = (
        None
        if regime_model is None
        else regime_model.evaluate(observation)
    )
    classification = classify_regimes(
        observation,
        domain=domain,
    )
    return AllocationFeatures(
        staged_features=staged_features,
        regime_observation=observation,
        regime_classification=classification,
        terminal_by_owner=terminal_by_owner,
        candidate_roots=tuple(plan.candidate_roots),
    )
