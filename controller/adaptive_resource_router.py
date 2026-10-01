"""M14-J J10 adaptive allocation router.

This layer nominates whether to buy the frozen staged-VERIFY bundle.  It
inherits ConservativeRouter for all resource authority: AllocationDecision is
never a BudgetLedger reservation and never move authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from controller.allocation_features import (
    AllocationFeatureError,
    build_allocation_features,
)
from controller.decision import canonical_digest
from controller.move_resource_plan import MoveResourcePlan
from controller.regime_calibration import (
    RegimeCalibrationError,
    RegimeSupportModel,
    load_regime_support_model,
)
from controller.resource_allocator import (
    ADAPTIVE_RESOURCE_POLICY,
    BUY_BUNDLE,
    FALLBACK,
    STOP_BUYING,
    AllocationDecision,
    DeterministicAdaptiveAllocator,
    ResourceAllocatorError,
    ResourceAllocatorSettings,
    build_allocator,
    validate_bundle_against_scheduler,
)
from controller.routing import ConservativeRouter, RoutingError
from controller.runtime import StagedVerificationExtensionSettings
from controller.staged_decision_calibration import (
    StagedDecisionCalibrationError,
    StagedDecisionChangeModel,
    load_staged_decision_calibration,
)
from controller.verification import VerificationRun
from controller.work_grant import WorkGrant
from controller.work_scheduler import (
    GrantAdmission,
    LegacyFixedWorkGrantScheduler,
    WorkSchedulerDenied,
)


class AdaptiveResourceRoutingError(RoutingError):
    """J10 router cannot preserve its declared allocation contract."""


class AdaptiveResourceRouter(ConservativeRouter):
    """ConservativeRouter plus J10 round-2 bundle nomination."""

    def __init__(
        self,
        *,
        allocator: DeterministicAdaptiveAllocator,
        staged_model: StagedDecisionChangeModel | None,
        regime_model: RegimeSupportModel | None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.allocator = allocator
        self.staged_model = staged_model
        self.regime_model = regime_model
        self._allocation_decisions: dict[str, AllocationDecision] = {}
        self._active_context: Any | None = None

    def on_run_start(self, context: Any) -> None:
        self._allocation_decisions.clear()
        self._active_context = context
        super().on_run_start(context)

    def allocation_decision_snapshot(
        self,
        run_id: str,
    ) -> AllocationDecision | None:
        return self._allocation_decisions.get(str(run_id))

    def decision_authority_snapshot(self) -> dict[str, object]:
        """Expose a frozen J11 provenance binding without granting move authority.

        J10 still cannot compose with HYBRID authority.  This surface exists so
        J11 can prove the future J12 authority input before that composition is
        enabled.  Historical G3 snapshots are unchanged because they use the
        non-adaptive router and therefore carry no orchestration_provenance key.
        """

        payload = super().decision_authority_snapshot()
        context = self._active_context
        audit = self.audit
        scheduler = self.work_scheduler
        if (
            context is None
            or audit is None
            or scheduler is None
        ):
            return payload
        plan = getattr(context, "move_resource_plan", None)
        decision = self._allocation_decisions.get(str(context.run_id))
        if (
            not isinstance(plan, MoveResourcePlan)
            or decision is None
            or plan.host_capabilities is None
        ):
            return payload

        trace_digest = canonical_digest(
            {
                "allocation_decisions": audit.allocation_decisions,
                "allocation_contexts": audit.allocation_contexts,
                "work_grants": audit.work_grants,
            }
        )
        payload["orchestration_provenance"] = {
            "version": "j11-orchestration-authority-binding-v1",
            "move_resource_plan_id": plan.plan_id,
            "move_resource_plan_digest": plan.digest,
            "allocation_policy_digest": self.allocator.policy.digest,
            "allocation_decision_digest": decision.digest,
            "allocation_trace_digest": trace_digest,
            "work_scheduler_catalog_digest": scheduler.catalog.digest,
            "profile_catalog_digest": plan.catalog_digest,
            "composition_profile_digest": plan.composition.digest,
            "game_environment_digest": plan.game_environment.digest,
            "host_capabilities_digest": plan.host_capabilities.digest,
            "work_grant_settlement_complete": not self._work_grant_unresolved,
            "open_work_grant_reservations": len(self._work_grant_reservations),
            "authority": {
                "resource_evidence": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }
        return payload

    def _record_allocation_decision(
        self,
        run_id: str,
        decision: AllocationDecision,
    ) -> None:
        self._allocation_decisions[str(run_id)] = decision
        if self.audit is not None:
            record = getattr(
                self.audit,
                "record_allocation_decision",
                None,
            )
            if record is not None:
                record(decision.as_dict())
            else:
                # Compatibility fallback for older audit containers.
                self.audit.record_value_decision(
                    {
                        "kind": "resource_allocation",
                        **decision.as_dict(),
                    }
                )

    def decide_staged_extension(
        self,
        context: Any,
        verification: VerificationRun,
        extension: StagedVerificationExtensionSettings,
    ) -> bool:
        """Nominate BUY/STOP for round 2; resource authority remains J9/router."""

        plan = getattr(context, "move_resource_plan", None)
        if not isinstance(plan, MoveResourcePlan):
            raise AdaptiveResourceRoutingError(
                "J10 requires MoveResourcePlan"
            )

        feature_digest: str
        estimate = None
        classification = None
        feature_error: str | None = None
        try:
            with self.ledger.controller_overhead(
                "adaptive_resource_features"
            ):
                nodes = extension.dispatch_limit.get("nodes")
                if (
                    isinstance(nodes, bool)
                    or not isinstance(nodes, int)
                    or nodes <= 0
                ):
                    raise AllocationFeatureError(
                        "J10 staged extension requires positive node limit"
                    )
                features = build_allocation_features(
                    context=context,
                    verification=verification,
                    extension_nodes=nodes,
                    regime_model=self.regime_model,
                )
                feature_digest = features.digest
                classification = features.regime_classification
                if self.staged_model is not None:
                    estimate = self.staged_model.evaluate(
                        features.staged_features
                    )
        except Exception as exc:
            # Feature failure can never license STOP. Preserve an auditable
            # digest of the failure and let the allocator fail closed to BUY.
            feature_error = f"{type(exc).__name__}: {exc}"
            feature_digest = canonical_digest(
                {
                    "extractor": "adaptive-resource-features-v1",
                    "error": feature_error,
                    "run_id": context.run_id,
                    "generation": context.generation,
                    "position_id": context.position.position_id,
                }
            )
            estimate = None
            classification = None

        snapshot = self.ledger.snapshot()
        budget_snapshot_digest = canonical_digest(snapshot)
        with self.ledger.controller_overhead(
            "adaptive_resource_decision"
        ):
            decision = self.allocator.decide(
                move_plan=plan,
                feature_digest=feature_digest,
                budget_snapshot_digest=budget_snapshot_digest,
                estimate=estimate,
                classification=classification,
            )
        if feature_error is not None and decision.action == STOP_BUYING:
            raise AdaptiveResourceRoutingError(
                "feature failure attempted to license STOP_BUYING"
            )

        self._record_allocation_decision(
            context.run_id,
            decision,
        )
        if self.audit is not None:
            journal = self.ledger.journal()
            self.audit.record_allocation_context(
                {
                    "allocation_id": decision.allocation_id,
                    "budget_snapshot": snapshot,
                    "budget_snapshot_digest": canonical_digest(snapshot),
                    "budget_journal_event_count": len(journal),
                    "budget_journal_digest": canonical_digest(journal),
                }
            )
        if decision.action == BUY_BUNDLE:
            return True
        if decision.action in (STOP_BUYING, FALLBACK):
            return False
        raise AdaptiveResourceRoutingError(
            f"unknown AllocationDecision action: {decision.action!r}"
        )

    def _active_staged_decision(
        self,
        context: Any,
    ) -> AllocationDecision:
        decision = self._allocation_decisions.get(str(context.run_id))
        if decision is None:
            raise WorkSchedulerDenied(
                "no J10 AllocationDecision exists for staged grant"
            )
        if (
            decision.action != BUY_BUNDLE
            or decision.selected_bundle_id
            != self.allocator.policy.bundle.bundle_id
        ):
            raise WorkSchedulerDenied(
                "J10 AllocationDecision did not select staged bundle"
            )
        if (
            decision.allocation_policy_digest
            != self.allocator.policy.digest
        ):
            raise WorkSchedulerDenied(
                "J10 AllocationDecision does not bind the active allocation policy"
            )
        if (
            decision.generation != context.generation
            or decision.position_id != context.position.position_id
        ):
            raise WorkSchedulerDenied(
                "J10 AllocationDecision generation/position mismatch"
            )
        plan = getattr(context, "move_resource_plan", None)
        if (
            not isinstance(plan, MoveResourcePlan)
            or decision.move_resource_plan_id != plan.plan_id
        ):
            raise WorkSchedulerDenied(
                "J10 AllocationDecision parent MoveResourcePlan mismatch"
            )
        return decision

    def propose_work_grant(
        self,
        context: Any,
        *,
        owner: str,
        instance: str,
        phase: str,
        allocation_round: int,
        effective_options_digest: str,
        target_id: str | None = None,
    ) -> WorkGrant | None:
        if phase != "STAGED_VERIFY" or allocation_round != 2:
            return super().propose_work_grant(
                context,
                owner=owner,
                instance=instance,
                phase=phase,
                allocation_round=allocation_round,
                effective_options_digest=effective_options_digest,
                target_id=target_id,
            )

        scheduler = self.work_scheduler
        plan = getattr(context, "move_resource_plan", None)
        if (
            scheduler is None
            or not isinstance(plan, MoveResourcePlan)
        ):
            return None
        try:
            decision = self._active_staged_decision(context)
            scheduled = scheduler.catalog.chunk_for(
                allocation_round=allocation_round,
                family=owner,
                phase=phase,
            )
            if (
                scheduled.chunk.chunk_id
                not in self.allocator.policy.bundle.chunk_ids
            ):
                raise WorkSchedulerDenied(
                    "requested staged chunk is outside selected J10 bundle"
                )
            with self.ledger.controller_overhead(
                "propose_adaptive_work_grant"
            ):
                return scheduler.create_grant(
                    move_plan=plan,
                    owner=owner,
                    instance=instance,
                    phase=phase,
                    allocation_round=allocation_round,
                    effective_options_digest=effective_options_digest,
                    elapsed_ms=float(context.elapsed_ms()),
                    target_id=target_id,
                    allocator_decision_digest=decision.digest,
                )
        except WorkSchedulerDenied as exc:
            if self.audit is not None:
                self.audit.record_work_grant(
                    {
                        "event": "propose",
                        "checkpoint_ms": round(
                            float(context.elapsed_ms()),
                            3,
                        ),
                        "owner": owner,
                        "instance": instance,
                        "phase": phase,
                        "allocation_round": allocation_round,
                        "search_id": None,
                        "grant_id": None,
                        "granted": False,
                        "reason": str(exc),
                    }
                )
            return None

    def authorize_work_grant_bundle(
        self,
        context: Any,
        *,
        items: Sequence[tuple[WorkGrant, str]],
    ) -> tuple[GrantAdmission, ...] | None:
        """Require the exact selected J10 bundle before transactional admission."""

        decision = self._active_staged_decision(context)
        rows = tuple(items)
        if len(rows) != 3:
            if self.audit is not None:
                self.audit.note(
                    "J10 staged bundle denied: expected exactly three grants"
                )
            return None
        grants = tuple(grant for grant, _ in rows)
        if (
            any(
                grant.phase != "STAGED_VERIFY"
                or grant.allocation_round != 2
                or grant.allocator_decision_digest != decision.digest
                for grant in grants
            )
            or {grant.owner for grant in grants}
            != {"stockfish", "reckless", "lc0"}
            or {grant.work_chunk_id for grant in grants}
            != set(self.allocator.policy.bundle.chunk_ids)
            or len({search_id for _, search_id in rows}) != 3
        ):
            if self.audit is not None:
                self.audit.note(
                    "J10 staged bundle denied: grant set differs from selected bundle"
                )
            return None
        return super().authorize_work_grant_bundle(
            context,
            items=rows,
        )

    def expected_allocator_decision_digest(
        self,
        context: Any,
        grant: WorkGrant,
    ) -> str | None:
        if (
            grant.phase != "STAGED_VERIFY"
            or grant.allocation_round != 2
        ):
            return None
        decision = self._active_staged_decision(context)
        scheduled = self.work_scheduler.catalog.chunk_for(
            allocation_round=grant.allocation_round,
            family=grant.owner,
            phase=grant.phase,
        )
        if (
            scheduled.chunk.chunk_id
            not in self.allocator.policy.bundle.chunk_ids
        ):
            raise WorkSchedulerDenied(
                "grant chunk is outside selected J10 bundle"
            )
        return decision.digest

    def on_run_end(self, context: Any) -> None:
        try:
            super().on_run_end(context)
        finally:
            self._allocation_decisions.pop(str(context.run_id), None)
            self._active_context = None


def _validate_promoted_model_bindings(
    *,
    root: Path,
    allocation_policy: Any,
    staged_model: StagedDecisionChangeModel | None,
    regime_model: RegimeSupportModel | None,
) -> None:
    """Require promoted models to bind the actual frozen independent corpus."""

    if not allocation_policy.stop_promotion:
        return
    if staged_model is None or regime_model is None:
        raise AdaptiveResourceRoutingError(
            "promoted J10 STOP policy requires both serving models"
        )
    corpus_path = (
        Path(root) / "qualification/j10-calibration-corpus-v1.json"
    ).resolve()
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdaptiveResourceRoutingError(
            f"J10 calibration corpus could not be loaded: {exc}"
        ) from exc
    if not isinstance(corpus, dict):
        raise AdaptiveResourceRoutingError(
            "J10 calibration corpus root must be an object"
        )
    if corpus.get("corpus_id") != allocation_policy.calibration_corpus_id:
        raise AdaptiveResourceRoutingError(
            "promoted J10 models bind a different calibration corpus id"
        )
    groups_raw = corpus.get("source_groups")
    if (
        not isinstance(groups_raw, list)
        or not groups_raw
        or any(not isinstance(group, str) or not group for group in groups_raw)
        or len(groups_raw) != len(set(groups_raw))
    ):
        raise AdaptiveResourceRoutingError(
            "promoted J10 corpus must freeze unique non-empty source_groups"
        )
    groups = set(groups_raw)
    frozen_floor = corpus.get("minimum_independent_groups_for_stop_promotion")
    if (
        isinstance(frozen_floor, bool)
        or not isinstance(frozen_floor, int)
        or frozen_floor <= 0
    ):
        raise AdaptiveResourceRoutingError(
            "promoted J10 corpus has an invalid frozen independent-group floor"
        )
    if frozen_floor != allocation_policy.minimum_independent_groups:
        raise AdaptiveResourceRoutingError(
            "promoted J10 policy minimum differs from the frozen independent-group floor"
        )
    expected_count = allocation_policy.calibration_independent_groups
    if (
        corpus.get("independent_groups") != expected_count
        or len(groups) != expected_count
        or expected_count < frozen_floor
    ):
        raise AdaptiveResourceRoutingError(
            "promoted J10 corpus does not meet the frozen independent-group floor"
        )
    if (
        corpus.get("promotion_eligible") is not True
        or corpus.get("labels_frozen") is not True
    ):
        raise AdaptiveResourceRoutingError(
            "promoted J10 corpus is not frozen/eligible for STOP promotion"
        )
    staged_groups = set(staged_model.split_by_position)
    regime_groups = set(regime_model.split_by_position)
    if staged_groups != groups:
        raise AdaptiveResourceRoutingError(
            "staged decision model group set differs from promoted calibration corpus"
        )
    if regime_groups != groups:
        raise AdaptiveResourceRoutingError(
            "regime support model group set differs from promoted calibration corpus"
        )
    if (
        len(staged_groups) < frozen_floor
        or len(regime_groups) < frozen_floor
    ):
        raise AdaptiveResourceRoutingError(
            "loaded J10 models do not meet independent-group floor"
        )


def _resolve_model(
    root: Path,
    value: str | None,
) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def build_adaptive_resource_router(
    *,
    config: Any,
    envelope: Any,
    policy: Any,
    calibration: Any,
    calibration_source: str | None,
    work_scheduler: LegacyFixedWorkGrantScheduler,
) -> AdaptiveResourceRouter:
    settings = getattr(config, "resource_allocator", None)
    if not isinstance(settings, ResourceAllocatorSettings):
        raise AdaptiveResourceRoutingError(
            "adaptive resource router requires resource_allocator settings"
        )
    if not isinstance(
        work_scheduler,
        LegacyFixedWorkGrantScheduler,
    ):
        raise AdaptiveResourceRoutingError(
            "adaptive resource router requires J9 WorkGrant scheduler"
        )

    base_allocator = build_allocator(
        settings=settings,
        root=config.root,
    )
    allocation_policy = base_allocator.policy

    validate_bundle_against_scheduler(
        allocation_policy,
        scheduler_catalog_id=work_scheduler.catalog.catalog_id,
        scheduler_catalog_digest=work_scheduler.catalog.digest,
        scheduler_chunks=work_scheduler.catalog.chunks,
    )

    staged_model: StagedDecisionChangeModel | None = None
    regime_model: RegimeSupportModel | None = None
    staged_path = _resolve_model(
        config.root,
        allocation_policy.staged_model_path,
    )
    regime_path = _resolve_model(
        config.root,
        allocation_policy.regime_model_path,
    )
    if staged_path is not None:
        try:
            staged_model = load_staged_decision_calibration(
                staged_path
            )
        except StagedDecisionCalibrationError as exc:
            raise AdaptiveResourceRoutingError(
                f"J10 staged model could not be loaded: {exc}"
            ) from exc
    if regime_path is not None:
        try:
            regime_model = load_regime_support_model(regime_path)
        except RegimeCalibrationError as exc:
            raise AdaptiveResourceRoutingError(
                f"J10 regime model could not be loaded: {exc}"
            ) from exc

    _validate_promoted_model_bindings(
        root=config.root,
        allocation_policy=allocation_policy,
        staged_model=staged_model,
        regime_model=regime_model,
    )

    allocator = DeterministicAdaptiveAllocator(
        policy=allocation_policy,
        staged_model=staged_model,
        regime_model_id=(
            None if regime_model is None else regime_model.model_id
        ),
    )
    return AdaptiveResourceRouter(
        envelope=envelope,
        policy=policy,
        calibration=calibration,
        calibration_source=calibration_source,
        verify_enabled=True,
        refine_enabled=False,
        work_scheduler=work_scheduler,
        allocator=allocator,
        staged_model=staged_model,
        regime_model=regime_model,
    )
