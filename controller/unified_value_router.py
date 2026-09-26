"""M14-G2 unified value-of-compute routing.

The existing conservative router still owns the BudgetLedger and resource
authorization. This module adds exactly one live route decision: after a clean
base VERIFY round, decide whether the same-process staged VERIFY extension
should be bought.

A route decision is not a resource grant and is not move authority. Skipping
the extension is the risky shortcut, so it is licensed only by an in-domain,
held-out-observed staged decision-change bucket and an in-domain regime bucket.
Missing or unsupported evidence fails closed to buying more compute; the
ordinary specialist reservation gate may still deny that work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
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
from controller.regime_calibration import (
    RegimeCalibrationError,
    RegimeSupportModel,
    load_regime_support_model,
)
from controller.regimes import (
    OWNER_ORDER,
    RefinementRegimeFeatures,
    RegimeClassification,
    RegimeDomainAssessment,
    RegimeObservation,
    RegimeStatus,
    SearchRegime,
    TimingRegimeFeatures,
    VerifierRegimeFeatures,
    classify_regimes,
)
from controller.replay_analysis import SearchTrajectory, reconstruct_stream
from controller.routing import ConservativeRouter, RoutingError, RoutingPolicy
from controller.runtime import StagedVerificationExtensionSettings
from controller.staged_decision_calibration import (
    StagedDecisionCalibrationError,
    StagedDecisionChangeModel,
    StagedValueEstimate,
    load_staged_decision_calibration,
)
from controller.verification import VerificationRun
from controller.verification_analysis import derive_relock


UNIFIED_VALUE_POLICY = "unified_value_v1"


class UnifiedValueRoutingError(RoutingError):
    """Raised when the G2 router cannot preserve its fail-closed contract."""


@dataclass(frozen=True)
class ValueRouteGate:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class UnifiedValueDecision:
    action: str
    buy_extension: bool
    reason: str
    transition: str
    features: dict[str, Any] | None
    staged_estimate: dict[str, Any] | None
    regime: dict[str, Any] | None
    gates: tuple[ValueRouteGate, ...]
    staged_model_id: str | None
    regime_model_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "buy_extension": self.buy_extension,
            "reason": self.reason,
            "transition": self.transition,
            "features": self.features,
            "staged_estimate": self.staged_estimate,
            "regime": self.regime,
            "gates": [gate.as_dict() for gate in self.gates],
            "staged_model_id": self.staged_model_id,
            "regime_model_id": self.regime_model_id,
            "authority": {
                "routing": True,
                "resource": False,
                "outward_move": False,
            },
        }


@dataclass(frozen=True)
class _LiveBaseState:
    features: dict[str, Any]
    estimate: StagedValueEstimate | None
    classification: RegimeClassification | None


class _LiveVerificationBundle:
    """Small duck type consumed by derive_relock."""

    def __init__(
        self,
        candidate_roots: tuple[str, ...],
        trajectories: tuple[SearchTrajectory, ...],
    ) -> None:
        self.candidate_roots = candidate_roots
        self.trajectories = trajectories

    def by_owner(self, owner: str) -> SearchTrajectory | None:
        return next(
            (trajectory for trajectory in self.trajectories if trajectory.owner == owner),
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
            or (isinstance(value, int) and not isinstance(value, bool))
        ):
            limits.append((name, value))
    names = {name for name, _ in limits}
    if "movetime" in names:
        mode = "movetime"
    elif names.intersection({"wtime", "btime", "winc", "binc", "movestogo"}):
        mode = "clock"
    elif "nodes" in names:
        mode = "nodes"
    elif names:
        mode = "other"
    else:
        mode = "unbounded_or_unknown"
    return TimingRegimeFeatures(request_mode=mode, limits=tuple(limits))


def _mate_alarm_families(evidence: Any) -> tuple[str, ...]:
    families: set[str] = set()
    for hint in evidence.hints:
        if hint.stage_disposition != "completed":
            continue
        if any(
            evaluation.kind == "mate"
            and evaluation.semantics.startswith(f"{hint.source_family}.")
            for evaluation in hint.evaluations
        ):
            families.add(hint.source_family)
    return tuple(owner for owner in OWNER_ORDER if owner in families)


def _trajectory_for_owner(
    verification: VerificationRun,
    owner: str,
) -> SearchTrajectory:
    stage = verification.stage_for_owner(owner)
    if stage is None or stage.disposition != "completed":
        raise UnifiedValueRoutingError(
            f"base VERIFY stage for {owner} is not cleanly completed"
        )
    stream = verification.stream(stage.instance)
    if stream is None:
        raise UnifiedValueRoutingError(f"base VERIFY stream for {owner} is missing")
    if not stream.drain_barrier(0.25):
        raise UnifiedValueRoutingError(
            f"base VERIFY stream for {owner} did not drain before route decision"
        )
    if stream.evidence_lossy or stream.tracked_events_truncated:
        raise UnifiedValueRoutingError(
            f"base VERIFY live evidence for {owner} is incomplete"
        )
    if stream.pending_events():
        raise UnifiedValueRoutingError(
            f"base VERIFY stream for {owner} still has pending events"
        )
    trajectories = reconstruct_stream(
        stream.tracked_events(),
        instance=stage.instance,
        family=stage.family,
        role="shadow",
        owner_roots={owner: verification.plan.candidate_roots},
    )
    matches = [item for item in trajectories if item.owner == owner]
    if len(matches) != 1:
        raise UnifiedValueRoutingError(
            f"base VERIFY for {owner} reconstructed {len(matches)} trajectories"
        )
    trajectory = matches[0]
    if trajectory.search_id != stage.search_id:
        raise UnifiedValueRoutingError(
            f"base VERIFY search identity mismatch for {owner}"
        )
    if (
        not trajectory.complete
        or trajectory.parse_errors
        or trajectory.final_leader != stage.bestmove
        or trajectory.final_leader not in verification.plan.candidate_roots
    ):
        raise UnifiedValueRoutingError(
            f"base VERIFY terminal evidence is inconsistent for {owner}"
        )
    return trajectory


def _serving_features(
    *,
    transition: str,
    disposition: str,
    verifiers: tuple[VerifierRegimeFeatures, ...],
) -> dict[str, Any]:
    return {
        "transition": transition,
        "base_decision_disposition": disposition,
        "min_observation_count": min(item.observation_count for item in verifiers),
        "max_leader_flips": max(item.leader_flips for item in verifiers),
        "min_stable_run_fraction": min(
            item.stable_run_fraction for item in verifiers
        ),
    }


def _holdout_bucket_validated(
    model: StagedDecisionChangeModel,
    bucket: str,
) -> tuple[bool, str]:
    holdout = model.evaluation.get("holdout") or {}
    reliability = holdout.get("reliability") or []
    for row in reliability:
        if not isinstance(row, dict) or row.get("bucket") != bucket:
            continue
        count = int(row.get("count") or 0)
        admitted = int(row.get("in_domain_rows") or 0)
        if count > 0 and admitted > 0:
            return True, (
                f"held-out bucket count {count}, in-domain rows {admitted}"
            )
        return False, (
            f"held-out bucket exists but has count={count}, in-domain={admitted}"
        )
    return False, "serving bucket has no held-out observation"


def choose_staged_route(
    *,
    transition: str,
    features: dict[str, Any] | None,
    estimate: StagedValueEstimate | None,
    classification: RegimeClassification | None,
    staged_model: StagedDecisionChangeModel | None,
    regime_model: RegimeSupportModel | None,
    skip_max_change_probability: float,
    fallback_latched: bool,
    wall_exhausted: bool,
) -> UnifiedValueDecision:
    """Pure route policy for the base to staged-VERIFY decision.

    Buying more compute is conservative. Skipping it is a shortcut and therefore
    requires every evidence gate below to pass.
    """

    if fallback_latched or wall_exhausted:
        gates = (
            ValueRouteGate(
                "resource_window_open",
                False,
                "anchor-only fallback is latched or wall envelope is exhausted",
            ),
        )
        return UnifiedValueDecision(
            action="FALLBACK_ANCHOR",
            buy_extension=False,
            reason="no new specialist work may start after the resource window closes",
            transition=transition,
            features=features,
            staged_estimate=None if estimate is None else estimate.as_dict(),
            regime=None if classification is None else classification.as_dict(),
            gates=gates,
            staged_model_id=None if staged_model is None else staged_model.model_id,
            regime_model_id=None if regime_model is None else regime_model.model_id,
        )

    holdout_ok = False
    holdout_detail = "no staged model or estimate"
    if staged_model is not None and estimate is not None:
        holdout_ok, holdout_detail = _holdout_bucket_validated(
            staged_model, estimate.bucket
        )

    regime_domain_ok = bool(
        classification is not None
        and classification.domain is not None
        and classification.domain.in_domain
    )
    regime_ood_inactive = bool(
        classification is not None
        and classification.status_for(SearchRegime.OUT_OF_DOMAIN)
        is RegimeStatus.INACTIVE
    )

    gates = (
        ValueRouteGate(
            "staged_calibration_present",
            staged_model is not None,
            "same-process staged decision-change model is loaded",
        ),
        ValueRouteGate(
            "staged_calibration_in_domain",
            bool(estimate is not None and estimate.in_domain),
            (
                "no staged estimate"
                if estimate is None
                else estimate.reason or f"bucket {estimate.bucket}"
            ),
        ),
        ValueRouteGate(
            "staged_bucket_heldout_observed",
            holdout_ok,
            holdout_detail,
        ),
        ValueRouteGate(
            "decision_change_risk_low",
            bool(
                estimate is not None
                and estimate.change_probability <= skip_max_change_probability
            ),
            (
                "no staged estimate"
                if estimate is None
                else (
                    f"P(change)={estimate.change_probability:.6f} <= "
                    f"{skip_max_change_probability:.6f}"
                )
            ),
        ),
        ValueRouteGate(
            "regime_support_present",
            regime_model is not None,
            "structural regime support model is loaded",
        ),
        ValueRouteGate(
            "regime_in_domain",
            regime_domain_ok,
            (
                "no regime domain assessment"
                if classification is None or classification.domain is None
                else classification.domain.reason
                or f"bucket {classification.domain.bucket}"
            ),
        ),
        ValueRouteGate(
            "regime_not_out_of_domain",
            regime_ood_inactive,
            "OUT_OF_DOMAIN must be inactive before a compute-skipping shortcut",
        ),
    )
    skip = all(gate.passed for gate in gates)
    if skip:
        action = "SKIP_STAGED_VERIFY"
        reason = (
            "held-out-observed staged bucket predicts sufficiently low decision "
            "change and the structural regime is in-domain"
        )
    else:
        action = "BUY_STAGED_VERIFY"
        failed = [gate.name for gate in gates if not gate.passed]
        reason = (
            "compute-skipping shortcut not licensed by "
            + ", ".join(failed)
            + "; buy more compute fail-closed"
        )
    return UnifiedValueDecision(
        action=action,
        buy_extension=not skip,
        reason=reason,
        transition=transition,
        features=features,
        staged_estimate=None if estimate is None else estimate.as_dict(),
        regime=None if classification is None else classification.as_dict(),
        gates=gates,
        staged_model_id=None if staged_model is None else staged_model.model_id,
        regime_model_id=None if regime_model is None else regime_model.model_id,
    )


class UnifiedValueRouter(ConservativeRouter):
    """ConservativeRouter plus one serve-compatible staged VERIFY route."""

    # Only the G2 policy promotes a completed staged extension into the frozen
    # counterfactual terminal plane. A research-only G1 staged configuration
    # must remain decision-inert even if a caller also enables counterfactual
    # evidence composition.
    use_staged_terminal_for_decision = True

    def __init__(
        self,
        *,
        staged_model: StagedDecisionChangeModel | None,
        staged_model_source: str | None,
        regime_support_model: RegimeSupportModel | None,
        regime_support_source: str | None,
        skip_max_change_probability: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.staged_model = staged_model
        self.staged_model_source = staged_model_source
        self.regime_support_model = regime_support_model
        self.regime_support_source = regime_support_source
        self.skip_max_change_probability = float(skip_max_change_probability)
        self._authority_value_decisions: dict[str, UnifiedValueDecision] = {}

    def on_run_start(self, context: Any) -> None:
        self._authority_value_decisions.clear()
        super().on_run_start(context)

    def _record_value_decision(
        self,
        run_id: str,
        decision: UnifiedValueDecision,
    ) -> None:
        if self.audit is not None:
            self.audit.record_value_decision(decision.as_dict())
        self._authority_value_decisions[str(run_id)] = decision

    def staged_route_authority_snapshot(self, run_id: str) -> dict[str, Any] | None:
        """Return the in-memory G2 route identity consumed by G3 authority."""
        decision = self._authority_value_decisions.get(str(run_id))
        if decision is None:
            return None
        payload = decision.as_dict()
        return {
            "action": decision.action,
            "buy_extension": decision.buy_extension,
            "decision": payload,
            "digest": canonical_digest(payload),
        }

    def on_run_end(self, context: Any) -> None:
        try:
            super().on_run_end(context)
        finally:
            # Route identity is authority-ephemeral. Keeping one payload per
            # replay run would grow without bound in a long-lived online bot.
            self._authority_value_decisions.pop(str(context.run_id), None)

    def _build_live_base_state(
        self,
        context: Any,
        verification: VerificationRun,
        extension: StagedVerificationExtensionSettings,
    ) -> _LiveBaseState:
        if verification.disposition != "completed":
            raise UnifiedValueRoutingError("base VERIFY is not completed")
        trajectories = tuple(
            _trajectory_for_owner(verification, owner) for owner in OWNER_ORDER
        )
        plan = verification.plan
        candidate_owner_by_move = {
            str(plan.nominees_by_owner[owner]): owner for owner in OWNER_ORDER
        }
        terminal_by_owner = {
            owner: str(verification.stage_for_owner(owner).bestmove)
            for owner in OWNER_ORDER
        }
        evidence_digest = canonical_digest(
            {
                "route_policy": UNIFIED_VALUE_POLICY,
                "run_id": context.run_id,
                "verification_id": plan.verification_id,
                "candidate_roots": list(plan.candidate_roots),
                "terminal_by_owner": terminal_by_owner,
            }
        )
        evaluation = evaluate_unanimous_verify_policy(
            candidate_roots=tuple(plan.candidate_roots),
            candidate_owner_by_move=candidate_owner_by_move,
            terminal_by_owner=terminal_by_owner,
            verification_complete=True,
            evidence_faults=(),
            evidence_digest=evidence_digest,
            policy=COUNTERFACTUAL_POLICY,
        )

        verifier_rows: list[VerifierRegimeFeatures] = []
        for owner in OWNER_ORDER:
            trajectory = next(item for item in trajectories if item.owner == owner)
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
        verifiers = tuple(verifier_rows)
        base_nodes = int(plan.dispatch_limit["nodes"])
        extension_nodes = int(extension.dispatch_limit["nodes"])
        transition = f"same-process:n{base_nodes}->n{extension_nodes}"
        features = _serving_features(
            transition=transition,
            disposition=evaluation.disposition.code,
            verifiers=verifiers,
        )
        estimate = (
            None if self.staged_model is None
            else self.staged_model.evaluate(features)
        )

        view = build_crossfeed_view(
            run_id=context.run_id,
            generation=context.generation,
            position_id=context.position.position_id,
            verification=verification,
            refinement=None,
        )
        adapter_evidence = build_adapter_evidence(view)
        if adapter_evidence.evidence_faults:
            raise UnifiedValueRoutingError(
                "live adapter evidence is faulted: "
                + "; ".join(adapter_evidence.evidence_faults)
            )
        live_bundle = _LiveVerificationBundle(
            tuple(plan.candidate_roots), trajectories
        )
        relock = derive_relock(live_bundle)
        terminals = tuple(
            (owner, terminal_by_owner[owner]) for owner in OWNER_ORDER
        )
        distinct = len(set(terminal_by_owner.values()))
        pattern = "unanimous" if distinct == 1 else "two_one" if distinct == 2 else "all_different"
        observation = RegimeObservation(
            run_id=context.run_id,
            generation=context.generation,
            position_id=context.position.position_id,
            candidate_roots=tuple(plan.candidate_roots),
            adapter_evidence_digest=adapter_evidence.digest,
            source_hashes=(),
            verify_terminal_by_owner=terminals,
            verify_pattern=pattern,
            relock_status=relock.status,
            relock_fraction=relock.relock_fraction,
            verifiers=verifiers,
            native_mate_alarm_families=_mate_alarm_families(adapter_evidence),
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
        domain: RegimeDomainAssessment | None = (
            None
            if self.regime_support_model is None
            else self.regime_support_model.evaluate(observation)
        )
        classification = classify_regimes(observation, domain=domain)
        return _LiveBaseState(
            features=features,
            estimate=estimate,
            classification=classification,
        )

    def decide_staged_extension(
        self,
        context: Any,
        verification: VerificationRun,
        extension: StagedVerificationExtensionSettings,
    ) -> bool:
        """Return a route recommendation; resource authority remains separate."""

        audit = self.audit
        if audit is None:
            return True
        transition = (
            f"same-process:n{verification.plan.dispatch_limit.get('nodes')}"
            f"->n{extension.dispatch_limit.get('nodes')}"
        )
        if self._fallback or self.ledger.wall_exhausted():
            decision = choose_staged_route(
                transition=transition,
                features=None,
                estimate=None,
                classification=None,
                staged_model=self.staged_model,
                regime_model=self.regime_support_model,
                skip_max_change_probability=self.skip_max_change_probability,
                fallback_latched=bool(self._fallback),
                wall_exhausted=self.ledger.wall_exhausted(),
            )
            self._record_value_decision(context.run_id, decision)
            return decision.buy_extension
        try:
            with self.ledger.controller_overhead("unified_value_route"):
                state = self._build_live_base_state(
                    context, verification, extension
                )
                decision = choose_staged_route(
                    transition=transition,
                    features=state.features,
                    estimate=state.estimate,
                    classification=state.classification,
                    staged_model=self.staged_model,
                    regime_model=self.regime_support_model,
                    skip_max_change_probability=self.skip_max_change_probability,
                    fallback_latched=bool(self._fallback),
                    wall_exhausted=self.ledger.wall_exhausted(),
                )
        except Exception as exc:  # fail closed and record the route failure
            decision = UnifiedValueDecision(
                action="BUY_STAGED_VERIFY",
                buy_extension=True,
                reason=(
                    "live route evidence could not license a compute-skipping "
                    f"shortcut; buy more compute fail-closed: {type(exc).__name__}: {exc}"
                ),
                transition=transition,
                features=None,
                staged_estimate=None,
                regime=None,
                gates=(
                    ValueRouteGate(
                        "route_evidence_complete",
                        False,
                        f"{type(exc).__name__}: {exc}",
                    ),
                ),
                staged_model_id=(
                    None if self.staged_model is None else self.staged_model.model_id
                ),
                regime_model_id=(
                    None
                    if self.regime_support_model is None
                    else self.regime_support_model.model_id
                ),
            )
        self._record_value_decision(context.run_id, decision)
        return decision.buy_extension


def _resolve_optional_model_path(config: Any, key: str) -> Path | None:
    raw = (config.routing or {}).get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise UnifiedValueRoutingError(f"routing.{key} must be a path string or null")
    path = Path(raw)
    if not path.is_absolute():
        path = (config.root / path).resolve()
    return path


def build_unified_value_router(
    *,
    config: Any,
    envelope: Any,
    policy: RoutingPolicy,
    calibration: Any,
    calibration_source: str | None,
) -> UnifiedValueRouter:
    if (
        config.verification is None
        or config.verification.staged_extension is None
    ):
        raise UnifiedValueRoutingError(
            "unified_value_v1 requires verification.staged_extension"
        )
    if config.crossfeed is None or config.counterfactual is None:
        raise UnifiedValueRoutingError(
            "unified_value_v1 requires crossfeed and counterfactual evidence layers"
        )
    if config.refinement is not None:
        raise UnifiedValueRoutingError(
            "unified_value_v1 routes before REFINE; v1 requires refinement disabled "
            "so live regime features match the current sealed M14-F support model"
        )

    staged_path = _resolve_optional_model_path(
        config, "staged_decision_calibration"
    )
    regime_path = _resolve_optional_model_path(
        config, "regime_support_calibration"
    )
    staged_model: StagedDecisionChangeModel | None = None
    regime_model: RegimeSupportModel | None = None
    if staged_path is not None:
        try:
            staged_model = load_staged_decision_calibration(staged_path)
        except StagedDecisionCalibrationError as exc:
            raise UnifiedValueRoutingError(
                f"declared staged decision calibration could not be loaded: {exc}"
            ) from exc
    if regime_path is not None:
        try:
            regime_model = load_regime_support_model(regime_path)
        except RegimeCalibrationError as exc:
            raise UnifiedValueRoutingError(
                f"declared regime support calibration could not be loaded: {exc}"
            ) from exc

    raw_threshold = (config.routing or {}).get(
        "staged_skip_max_change_probability", 0.10
    )
    if isinstance(raw_threshold, bool) or not isinstance(
        raw_threshold, (int, float)
    ):
        raise UnifiedValueRoutingError(
            "routing.staged_skip_max_change_probability must be numeric"
        )
    threshold = float(raw_threshold)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise UnifiedValueRoutingError(
            "routing.staged_skip_max_change_probability must be in [0,1]"
        )

    return UnifiedValueRouter(
        envelope=envelope,
        policy=policy,
        calibration=calibration,
        calibration_source=calibration_source,
        verify_enabled=True,
        refine_enabled=config.refinement is not None,
        staged_model=staged_model,
        staged_model_source=None if staged_path is None else str(staged_path),
        regime_support_model=regime_model,
        regime_support_source=None if regime_path is None else str(regime_path),
        skip_max_change_probability=threshold,
    )
