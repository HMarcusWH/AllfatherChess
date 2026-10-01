"""M14-J J10 deterministic adaptive resource allocator.

J10 does not authorize compute and does not authorize chess moves.  It makes
exactly one adaptive nomination: after a clean n16 three-engine VERIFY round,
either buy the frozen three-engine n32 staged-VERIFY bundle or stop buying
compute.  Every actual search still requires J9 WorkGrant + BudgetLedger
admission.

The v1 promotion policy is deliberately conservative:
- MoveResourcePlan FALLBACK -> FALLBACK
- unpromoted/missing/unsupported calibration -> BUY_BUNDLE
- unseen/under-supported/out-of-domain evidence -> BUY_BUNDLE
- only fully supported low decision-change risk may STOP_BUYING

The probability being estimated is a descriptive change in the frozen
unanimous-VERIFY policy, not move correctness, Elo, win probability or strength.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from controller.decision import canonical_digest
from controller.move_resource_plan import (
    ADAPTIVE_DISPOSITION,
    FALLBACK_DISPOSITION,
    MoveResourcePlan,
)
from controller.regimes import RegimeClassification, RegimeStatus, SearchRegime
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _mapping,
    _positive_int,
    _reject_unknown,
    _safe_id,
    _sha256,
)
from controller.staged_decision_calibration import (
    StagedDecisionChangeModel,
    StagedValueEstimate,
)


ADAPTIVE_RESOURCE_POLICY = "adaptive_resource_v1"
ALLOCATION_CATALOG_ID = "adaptive-resource-allocation-v1"
STAGED_BUNDLE_ID = "bundle/staged-verify-v1"
STAGED_BUNDLE_CHUNK_IDS = (
    "compat/stockfish/staged-verify/n32",
    "compat/reckless/staged-verify/n32",
    "compat/lc0/staged-verify/n32",
)
_STAGED_BUNDLE_CHUNK_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "compat/stockfish/staged-verify/n32": {
        "allocation_round": 2,
        "profile_id": "stockfish/specialist-engine-opt-v2",
        "family": "stockfish",
        "phase": "STAGED_VERIFY",
        "purpose": "verify",
        "native_limit": {
            "kind": "nodes",
            "value": 32,
            "semantics": "stockfish.uci_nodes",
        },
    },
    "compat/reckless/staged-verify/n32": {
        "allocation_round": 2,
        "profile_id": "reckless/specialist-engine-opt-v2",
        "family": "reckless",
        "phase": "STAGED_VERIFY",
        "purpose": "verify",
        "native_limit": {
            "kind": "nodes",
            "value": 32,
            "semantics": "reckless.uci_nodes",
        },
    },
    "compat/lc0/staged-verify/n32": {
        "allocation_round": 2,
        "profile_id": "lc0/specialist-engine-opt-v2",
        "family": "lc0",
        "phase": "STAGED_VERIFY",
        "purpose": "verify",
        "native_limit": {
            "kind": "nodes",
            "value": 32,
            "semantics": "lc0.uci_nodes",
        },
    },
}

BUY_BUNDLE = "BUY_BUNDLE"
STOP_BUYING = "STOP_BUYING"
FALLBACK = "FALLBACK"
_ACTIONS = (BUY_BUNDLE, STOP_BUYING, FALLBACK)


class ResourceAllocatorError(OrchestrationContractError):
    """Malformed J10 allocator configuration/artifact."""


@dataclass(frozen=True)
class ResourceAllocatorSettings:
    policy: str = ADAPTIVE_RESOURCE_POLICY
    catalog: str = "qualification/adaptive-resource-allocation-v1.json"

    def __post_init__(self) -> None:
        if self.policy != ADAPTIVE_RESOURCE_POLICY:
            raise ResourceAllocatorError(
                f"unsupported resource allocator policy: {self.policy!r}"
            )
        if not isinstance(self.catalog, str) or not self.catalog:
            raise ResourceAllocatorError(
                "resource_allocator.catalog must be a non-empty relative path"
            )
        path = PurePosixPath(self.catalog)
        if path.is_absolute() or any(
            part in ("", ".", "..") for part in path.parts
        ):
            raise ResourceAllocatorError(
                "resource_allocator.catalog must be normalized and repository-relative"
            )

    @classmethod
    def from_config(cls, raw: Any) -> "ResourceAllocatorSettings | None":
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ResourceAllocatorError("resource_allocator must be an object")
        if not isinstance(raw.get("enabled"), bool):
            raise ResourceAllocatorError(
                "resource_allocator.enabled must be boolean"
            )
        _reject_unknown(
            raw,
            {"enabled", "policy", "catalog"},
            "resource_allocator",
        )
        item = cls(
            **{
                key: value
                for key, value in raw.items()
                if key != "enabled"
            }
        )
        return item if raw["enabled"] else None


@dataclass(frozen=True)
class AllocationGate:
    name: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        _safe_id(self.name, "allocation gate name")
        if not isinstance(self.passed, bool):
            raise ResourceAllocatorError("allocation gate passed must be boolean")
        if not isinstance(self.detail, str):
            raise ResourceAllocatorError("allocation gate detail must be string")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AllocationDecision:
    policy_id: str
    allocation_policy_digest: str
    generation: int
    position_id: str
    move_resource_plan_id: str
    allocation_round: int
    candidate_bundle_ids: tuple[str, ...]
    selected_bundle_id: str | None
    action: str
    feature_digest: str
    budget_snapshot_digest: str
    staged_model_id: str | None
    regime_model_id: str | None
    change_probability: float | None
    support: int | None
    position_group_support: int | None
    heldout_bucket_observed: bool
    regime_bucket: str | None
    regime_in_domain: bool
    gates: tuple[AllocationGate, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.policy_id != ADAPTIVE_RESOURCE_POLICY:
            raise ResourceAllocatorError("AllocationDecision policy drift")
        _sha256(
            self.allocation_policy_digest,
            "allocation_policy_digest",
        )
        _positive_int(self.generation, "allocation generation")
        if (
            not isinstance(self.position_id, str)
            or not self.position_id
            or "\x00" in self.position_id
        ):
            raise ResourceAllocatorError(
                "allocation position_id must be non-empty and NUL-free"
            )
        _safe_id(self.move_resource_plan_id, "move_resource_plan_id")
        if self.allocation_round != 2:
            raise ResourceAllocatorError(
                "J10 v1 adapts only allocation round 2"
            )
        candidates = tuple(self.candidate_bundle_ids)
        if candidates != (STAGED_BUNDLE_ID,):
            raise ResourceAllocatorError(
                "J10 v1 candidate bundle set must contain only staged-verify-v1"
            )
        if self.action not in _ACTIONS:
            raise ResourceAllocatorError(
                f"allocation action must be one of {list(_ACTIONS)}"
            )
        if self.action == BUY_BUNDLE:
            if self.selected_bundle_id != STAGED_BUNDLE_ID:
                raise ResourceAllocatorError(
                    "BUY_BUNDLE must select staged-verify-v1"
                )
        else:
            if self.selected_bundle_id is not None:
                raise ResourceAllocatorError(
                    f"{self.action} may not select a bundle"
                )
        _sha256(self.feature_digest, "allocation feature_digest")
        _sha256(
            self.budget_snapshot_digest,
            "allocation budget_snapshot_digest",
        )
        for value, label in (
            (self.staged_model_id, "staged_model_id"),
            (self.regime_model_id, "regime_model_id"),
        ):
            if value is not None and (
                not isinstance(value, str) or not value
            ):
                raise ResourceAllocatorError(f"{label} must be string or null")
        if self.change_probability is not None:
            if (
                isinstance(self.change_probability, bool)
                or not isinstance(self.change_probability, (int, float))
                or not math.isfinite(float(self.change_probability))
                or not 0.0 <= float(self.change_probability) <= 1.0
            ):
                raise ResourceAllocatorError(
                    "change_probability must be finite in [0,1]"
                )
        for value, label in (
            (self.support, "support"),
            (self.position_group_support, "position_group_support"),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ResourceAllocatorError(
                    f"{label} must be non-negative integer or null"
                )
        if not isinstance(self.heldout_bucket_observed, bool):
            raise ResourceAllocatorError(
                "heldout_bucket_observed must be boolean"
            )
        if self.regime_bucket is not None and (
            not isinstance(self.regime_bucket, str)
            or not self.regime_bucket
        ):
            raise ResourceAllocatorError(
                "regime_bucket must be non-empty string or null"
            )
        if not isinstance(self.regime_in_domain, bool):
            raise ResourceAllocatorError(
                "regime_in_domain must be boolean"
            )
        if not isinstance(self.reason, str) or not self.reason:
            raise ResourceAllocatorError(
                "allocation reason must be non-empty"
            )

    def payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "allocation_policy_digest": self.allocation_policy_digest,
            "generation": self.generation,
            "position_id": self.position_id,
            "move_resource_plan_id": self.move_resource_plan_id,
            "allocation_round": self.allocation_round,
            "candidate_bundle_ids": list(self.candidate_bundle_ids),
            "selected_bundle_id": self.selected_bundle_id,
            "action": self.action,
            "feature_digest": self.feature_digest,
            "budget_snapshot_digest": self.budget_snapshot_digest,
            "staged_model_id": self.staged_model_id,
            "regime_model_id": self.regime_model_id,
            "change_probability": self.change_probability,
            "support": self.support,
            "position_group_support": self.position_group_support,
            "heldout_bucket_observed": self.heldout_bucket_observed,
            "regime_bucket": self.regime_bucket,
            "regime_in_domain": self.regime_in_domain,
            "gates": [gate.as_dict() for gate in self.gates],
            "reason": self.reason,
            "authority": {
                "allocation_nomination": True,
                "resource_authorization": False,
                "outward_move": False,
            },
            "claim_boundary": {
                "decision_change_probability_only": True,
                "move_quality": False,
                "correctness": False,
                "elo": False,
                "strength": False,
                "win_probability": False,
                "resource_authorization": False,
                "outward_move": False,
                "deployment": False,
            },
        }

    @property
    def allocation_id(self) -> str:
        return "allocation/" + canonical_digest(self.payload_dict())

    @property
    def digest(self) -> str:
        return self.allocation_id.split("/", 1)[1]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            **self.payload_dict(),
        }


@dataclass(frozen=True)
class AllocationBundle:
    bundle_id: str
    allocation_round: int
    chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.bundle_id != STAGED_BUNDLE_ID:
            raise ResourceAllocatorError(
                f"unsupported J10 bundle: {self.bundle_id!r}"
            )
        if self.allocation_round != 2:
            raise ResourceAllocatorError(
                "J10 staged bundle must belong to allocation round 2"
            )
        if len(self.chunk_ids) != 3 or len(set(self.chunk_ids)) != 3:
            raise ResourceAllocatorError(
                "J10 staged bundle must contain exactly three unique chunks"
            )
        for chunk_id in self.chunk_ids:
            _safe_id(chunk_id, "allocation bundle chunk_id")
        if self.chunk_ids != STAGED_BUNDLE_CHUNK_IDS:
            raise ResourceAllocatorError(
                "J10 staged bundle must contain the exact frozen J9 n32 staged trio"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "allocation_round": self.allocation_round,
            "chunk_ids": list(self.chunk_ids),
        }


@dataclass(frozen=True)
class AllocationPolicy:
    catalog_id: str
    policy_id: str
    work_scheduler_catalog_id: str
    work_scheduler_catalog_digest: str
    bundle: AllocationBundle
    skip_max_change_probability: float
    max_allocation_rounds: int
    stop_promotion: bool
    promotion_reason: str
    minimum_independent_groups: int
    calibration_independent_groups: int
    calibration_corpus_id: str
    staged_model_path: str | None
    regime_model_path: str | None

    def __post_init__(self) -> None:
        if self.catalog_id != ALLOCATION_CATALOG_ID:
            raise ResourceAllocatorError("allocation catalog id drift")
        if self.policy_id != ADAPTIVE_RESOURCE_POLICY:
            raise ResourceAllocatorError("allocation policy id drift")
        _safe_id(
            self.work_scheduler_catalog_id,
            "work scheduler catalog id",
        )
        _sha256(
            self.work_scheduler_catalog_digest,
            "work scheduler catalog digest",
        )
        if not isinstance(self.bundle, AllocationBundle):
            raise ResourceAllocatorError("bundle must be AllocationBundle")
        if (
            isinstance(self.skip_max_change_probability, bool)
            or not isinstance(
                self.skip_max_change_probability, (int, float)
            )
            or not math.isfinite(
                float(self.skip_max_change_probability)
            )
            or not 0.0
            <= float(self.skip_max_change_probability)
            <= 1.0
        ):
            raise ResourceAllocatorError(
                "skip_max_change_probability must be finite in [0,1]"
            )
        if self.max_allocation_rounds != 3:
            raise ResourceAllocatorError(
                "J10 v1 requires exactly three allocation rounds"
            )
        if not isinstance(self.stop_promotion, bool):
            raise ResourceAllocatorError(
                "stop_promotion must be boolean"
            )
        if (
            not isinstance(self.promotion_reason, str)
            or not self.promotion_reason
        ):
            raise ResourceAllocatorError(
                "promotion_reason must be non-empty"
            )
        _positive_int(
            self.minimum_independent_groups,
            "minimum_independent_groups",
        )
        if (
            isinstance(self.calibration_independent_groups, bool)
            or not isinstance(self.calibration_independent_groups, int)
            or self.calibration_independent_groups < 0
        ):
            raise ResourceAllocatorError(
                "calibration_independent_groups must be non-negative"
            )
        _safe_id(self.calibration_corpus_id, "calibration_corpus_id")
        if self.stop_promotion and (
            self.calibration_independent_groups
            < self.minimum_independent_groups
        ):
            raise ResourceAllocatorError(
                "STOP promotion cannot be enabled below independent-group minimum"
            )
        for value, label in (
            (self.staged_model_path, "staged_model_path"),
            (self.regime_model_path, "regime_model_path"),
        ):
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ResourceAllocatorError(
                        f"{label} must be non-empty string or null"
                    )
                path = PurePosixPath(value)
                if path.is_absolute() or any(
                    part in ("", ".", "..") for part in path.parts
                ):
                    raise ResourceAllocatorError(
                        f"{label} must be normalized and repository-relative"
                    )
        if self.stop_promotion and (
            self.staged_model_path is None
            or self.regime_model_path is None
        ):
            raise ResourceAllocatorError(
                "STOP promotion requires both staged and regime model artifacts"
            )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "policy_id": self.policy_id,
            "work_scheduler_catalog": {
                "catalog_id": self.work_scheduler_catalog_id,
                "catalog_digest": self.work_scheduler_catalog_digest,
            },
            "bundle": self.bundle.as_dict(),
            "skip_max_change_probability": float(
                self.skip_max_change_probability
            ),
            "max_allocation_rounds": self.max_allocation_rounds,
            "promotion": {
                "stop_promotion": self.stop_promotion,
                "reason": self.promotion_reason,
                "minimum_independent_groups": (
                    self.minimum_independent_groups
                ),
                "calibration_independent_groups": (
                    self.calibration_independent_groups
                ),
                "calibration_corpus_id": self.calibration_corpus_id,
                "staged_model_path": self.staged_model_path,
                "regime_model_path": self.regime_model_path,
            },
            "authority": {
                "allocation_nomination": True,
                "resource_authorization": False,
                "outward_move": False,
            },
            "claim_boundary": {
                "adaptive_round": 2,
                "owner_specific_allocation": False,
                "decision_change_probability_only": True,
                "move_quality": False,
                "elo": False,
                "strength": False,
                "deployment": False,
            },
        }


def load_allocation_policy(path: Path | str) -> AllocationPolicy:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceAllocatorError(
            f"cannot load allocation policy {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ResourceAllocatorError(
            "allocation policy root must be object"
        )
    _reject_unknown(
        raw,
        {
            "schema_version",
            "catalog_id",
            "policy_id",
            "work_scheduler_catalog",
            "bundle",
            "skip_max_change_probability",
            "max_allocation_rounds",
            "promotion",
            "authority",
            "claim_boundary",
        },
        "allocation policy",
    )
    if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
        raise ResourceAllocatorError(
            "unsupported allocation policy schema_version"
        )
    if raw.get("authority") != {
        "allocation_nomination": True,
        "resource_authorization": False,
        "outward_move": False,
    }:
        raise ResourceAllocatorError(
            "allocation policy authority marker is invalid"
        )
    scheduler = _mapping(
        raw.get("work_scheduler_catalog"),
        "work_scheduler_catalog",
    )
    bundle_raw = _mapping(raw.get("bundle"), "bundle")
    promotion = _mapping(raw.get("promotion"), "promotion")
    chunk_ids = bundle_raw.get("chunk_ids")
    if not isinstance(chunk_ids, list):
        raise ResourceAllocatorError(
            "allocation bundle chunk_ids must be array"
        )
    item = AllocationPolicy(
        catalog_id=raw.get("catalog_id"),
        policy_id=raw.get("policy_id"),
        work_scheduler_catalog_id=scheduler.get("catalog_id"),
        work_scheduler_catalog_digest=scheduler.get("catalog_digest"),
        bundle=AllocationBundle(
            bundle_id=bundle_raw.get("bundle_id"),
            allocation_round=bundle_raw.get("allocation_round"),
            chunk_ids=tuple(chunk_ids),
        ),
        skip_max_change_probability=raw.get(
            "skip_max_change_probability"
        ),
        max_allocation_rounds=raw.get("max_allocation_rounds"),
        stop_promotion=promotion.get("stop_promotion"),
        promotion_reason=promotion.get("reason"),
        minimum_independent_groups=promotion.get(
            "minimum_independent_groups"
        ),
        calibration_independent_groups=promotion.get(
            "calibration_independent_groups"
        ),
        calibration_corpus_id=promotion.get("calibration_corpus_id"),
        staged_model_path=promotion.get("staged_model_path"),
        regime_model_path=promotion.get("regime_model_path"),
    )
    if raw.get("claim_boundary") != item.as_dict()["claim_boundary"]:
        raise ResourceAllocatorError(
            "allocation policy claim boundary is invalid"
        )
    return item


def _heldout_bucket_observed(
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
            f"held-out bucket exists but count={count}, in-domain={admitted}"
        )
    return False, "serving bucket has no held-out observation"


class DeterministicAdaptiveAllocator:
    def __init__(
        self,
        *,
        policy: AllocationPolicy,
        staged_model: StagedDecisionChangeModel | None,
        regime_model_id: str | None,
    ) -> None:
        self.policy = policy
        self.staged_model = staged_model
        self.regime_model_id = regime_model_id

    def decide(
        self,
        *,
        move_plan: MoveResourcePlan,
        feature_digest: str,
        budget_snapshot_digest: str,
        estimate: StagedValueEstimate | None,
        classification: RegimeClassification | None,
    ) -> AllocationDecision:
        if not isinstance(move_plan, MoveResourcePlan):
            raise ResourceAllocatorError(
                "allocator requires MoveResourcePlan"
            )
        _sha256(feature_digest, "feature_digest")
        _sha256(
            budget_snapshot_digest,
            "budget_snapshot_digest",
        )
        if move_plan.disposition == FALLBACK_DISPOSITION:
            return AllocationDecision(
                policy_id=ADAPTIVE_RESOURCE_POLICY,
                allocation_policy_digest=self.policy.digest,
                generation=move_plan.generation,
                position_id=move_plan.position_id,
                move_resource_plan_id=move_plan.plan_id,
                allocation_round=2,
                candidate_bundle_ids=(STAGED_BUNDLE_ID,),
                selected_bundle_id=None,
                action=FALLBACK,
                feature_digest=feature_digest,
                budget_snapshot_digest=budget_snapshot_digest,
                staged_model_id=(
                    None
                    if self.staged_model is None
                    else self.staged_model.model_id
                ),
                regime_model_id=self.regime_model_id,
                change_probability=(
                    None
                    if estimate is None
                    else estimate.change_probability
                ),
                support=None if estimate is None else estimate.support,
                position_group_support=(
                    None
                    if estimate is None
                    else estimate.position_group_support
                ),
                heldout_bucket_observed=False,
                regime_bucket=(
                    None
                    if classification is None
                    or classification.domain is None
                    else classification.domain.bucket
                ),
                regime_in_domain=False,
                gates=(
                    AllocationGate(
                        "parent_plan_adaptive",
                        False,
                        "MoveResourcePlan is FALLBACK",
                    ),
                ),
                reason="parent MoveResourcePlan is FALLBACK",
            )
        if move_plan.disposition != ADAPTIVE_DISPOSITION:
            raise ResourceAllocatorError(
                f"unknown MoveResourcePlan disposition: {move_plan.disposition!r}"
            )

        gates: list[AllocationGate] = []
        if not self.policy.stop_promotion:
            gates.append(
                AllocationGate(
                    "stop_policy_promoted",
                    False,
                    self.policy.promotion_reason,
                )
            )
            return self._buy(
                move_plan=move_plan,
                feature_digest=feature_digest,
                budget_snapshot_digest=budget_snapshot_digest,
                estimate=estimate,
                classification=classification,
                heldout=False,
                gates=tuple(gates),
                reason=(
                    "STOP policy is not promoted; buy staged evidence "
                    "fail-closed"
                ),
            )

        if self.staged_model is None or estimate is None:
            gates.append(
                AllocationGate(
                    "staged_calibration_present",
                    False,
                    "no staged decision-change model/estimate",
                )
            )
            return self._buy(
                move_plan=move_plan,
                feature_digest=feature_digest,
                budget_snapshot_digest=budget_snapshot_digest,
                estimate=estimate,
                classification=classification,
                heldout=False,
                gates=tuple(gates),
                reason="missing staged calibration; buy more compute",
            )

        heldout, heldout_detail = _heldout_bucket_observed(
            self.staged_model,
            estimate.bucket,
        )
        regime_domain = bool(
            classification is not None
            and classification.domain is not None
            and classification.domain.in_domain
        )
        regime_ood_inactive = bool(
            classification is not None
            and classification.status_for(SearchRegime.OUT_OF_DOMAIN)
            is RegimeStatus.INACTIVE
        )
        gates.extend(
            [
                AllocationGate(
                    "stop_policy_promoted",
                    True,
                    self.policy.promotion_reason,
                ),
                AllocationGate(
                    "staged_calibration_in_domain",
                    bool(estimate.in_domain),
                    estimate.reason or f"bucket {estimate.bucket}",
                ),
                AllocationGate(
                    "staged_bucket_heldout_observed",
                    heldout,
                    heldout_detail,
                ),
                AllocationGate(
                    "decision_change_risk_low",
                    estimate.change_probability
                    <= self.policy.skip_max_change_probability,
                    (
                        f"P(change)={estimate.change_probability:.6f} <= "
                        f"{self.policy.skip_max_change_probability:.6f}"
                    ),
                ),
                AllocationGate(
                    "regime_in_domain",
                    regime_domain,
                    (
                        "no regime domain assessment"
                        if classification is None
                        or classification.domain is None
                        else classification.domain.reason
                        or f"bucket {classification.domain.bucket}"
                    ),
                ),
                AllocationGate(
                    "regime_not_out_of_domain",
                    regime_ood_inactive,
                    "OUT_OF_DOMAIN must be inactive",
                ),
            ]
        )
        if all(gate.passed for gate in gates):
            return AllocationDecision(
                policy_id=ADAPTIVE_RESOURCE_POLICY,
                allocation_policy_digest=self.policy.digest,
                generation=move_plan.generation,
                position_id=move_plan.position_id,
                move_resource_plan_id=move_plan.plan_id,
                allocation_round=2,
                candidate_bundle_ids=(STAGED_BUNDLE_ID,),
                selected_bundle_id=None,
                action=STOP_BUYING,
                feature_digest=feature_digest,
                budget_snapshot_digest=budget_snapshot_digest,
                staged_model_id=self.staged_model.model_id,
                regime_model_id=self.regime_model_id,
                change_probability=estimate.change_probability,
                support=estimate.support,
                position_group_support=estimate.position_group_support,
                heldout_bucket_observed=heldout,
                regime_bucket=(
                    None
                    if classification is None
                    or classification.domain is None
                    else classification.domain.bucket
                ),
                regime_in_domain=regime_domain,
                gates=tuple(gates),
                reason=(
                    "independently supported low decision-change risk licenses "
                    "stopping after base VERIFY"
                ),
            )
        failed = ", ".join(
            gate.name for gate in gates if not gate.passed
        )
        return self._buy(
            move_plan=move_plan,
            feature_digest=feature_digest,
            budget_snapshot_digest=budget_snapshot_digest,
            estimate=estimate,
            classification=classification,
            heldout=heldout,
            gates=tuple(gates),
            reason=(
                "STOP shortcut not licensed by "
                + failed
                + "; buy staged evidence fail-closed"
            ),
        )

    def _buy(
        self,
        *,
        move_plan: MoveResourcePlan,
        feature_digest: str,
        budget_snapshot_digest: str,
        estimate: StagedValueEstimate | None,
        classification: RegimeClassification | None,
        heldout: bool,
        gates: tuple[AllocationGate, ...],
        reason: str,
    ) -> AllocationDecision:
        return AllocationDecision(
            policy_id=ADAPTIVE_RESOURCE_POLICY,
            allocation_policy_digest=self.policy.digest,
            generation=move_plan.generation,
            position_id=move_plan.position_id,
            move_resource_plan_id=move_plan.plan_id,
            allocation_round=2,
            candidate_bundle_ids=(STAGED_BUNDLE_ID,),
            selected_bundle_id=STAGED_BUNDLE_ID,
            action=BUY_BUNDLE,
            feature_digest=feature_digest,
            budget_snapshot_digest=budget_snapshot_digest,
            staged_model_id=(
                None
                if self.staged_model is None
                else self.staged_model.model_id
            ),
            regime_model_id=self.regime_model_id,
            change_probability=(
                None if estimate is None else estimate.change_probability
            ),
            support=None if estimate is None else estimate.support,
            position_group_support=(
                None
                if estimate is None
                else estimate.position_group_support
            ),
            heldout_bucket_observed=heldout,
            regime_bucket=(
                None
                if classification is None
                or classification.domain is None
                else classification.domain.bucket
            ),
            regime_in_domain=bool(
                classification is not None
                and classification.domain is not None
                and classification.domain.in_domain
            ),
            gates=gates,
            reason=reason,
        )


def validate_bundle_against_scheduler(
    policy: AllocationPolicy,
    *,
    scheduler_catalog_id: str,
    scheduler_catalog_digest: str,
    scheduler_chunks: Sequence[Any],
) -> None:
    if policy.work_scheduler_catalog_id != scheduler_catalog_id:
        raise ResourceAllocatorError(
            "J10 bundle policy binds a different J9 scheduler catalog id"
        )
    if policy.work_scheduler_catalog_digest != scheduler_catalog_digest:
        raise ResourceAllocatorError(
            "J10 bundle policy binds a different J9 scheduler catalog digest"
        )

    rows_by_id: dict[str, Any] = {}
    for row in scheduler_chunks:
        chunk = getattr(row, "chunk", None)
        chunk_id = getattr(chunk, "chunk_id", None)
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ResourceAllocatorError(
                "J9 scheduler supplied a malformed chunk while validating J10 bundle"
            )
        if chunk_id in rows_by_id:
            raise ResourceAllocatorError(
                f"J9 scheduler contains duplicate chunk id {chunk_id!r}"
            )
        rows_by_id[chunk_id] = row

    missing = [
        chunk_id
        for chunk_id in policy.bundle.chunk_ids
        if chunk_id not in rows_by_id
    ]
    if missing:
        raise ResourceAllocatorError(
            f"J10 bundle contains unknown J9 chunks: {missing}"
        )

    for chunk_id in policy.bundle.chunk_ids:
        row = rows_by_id[chunk_id]
        chunk = row.chunk
        native_limit = getattr(chunk, "native_limit", None)
        kind = getattr(native_limit, "kind", None)
        if hasattr(kind, "value"):
            kind = kind.value
        actual = {
            "allocation_round": getattr(row, "allocation_round", None),
            "profile_id": getattr(row, "profile_id", None),
            "family": getattr(chunk, "family", None),
            "phase": getattr(chunk, "phase", None),
            "purpose": getattr(chunk, "purpose", None),
            "native_limit": {
                "kind": kind,
                "value": getattr(native_limit, "value", None),
                "semantics": getattr(native_limit, "semantics", None),
            },
        }
        expected = dict(_STAGED_BUNDLE_CHUNK_CONTRACTS[chunk_id])
        if actual != expected:
            raise ResourceAllocatorError(
                f"J10 bundle chunk {chunk_id!r} does not match the frozen "
                "round/owner/phase/native-limit contract"
            )


def build_allocator(
    *,
    settings: ResourceAllocatorSettings,
    root: Path,
    staged_model: StagedDecisionChangeModel | None = None,
    regime_model_id: str | None = None,
) -> DeterministicAdaptiveAllocator:
    if not isinstance(settings, ResourceAllocatorSettings):
        raise ResourceAllocatorError(
            "settings must be ResourceAllocatorSettings"
        )
    policy = load_allocation_policy(
        (Path(root) / settings.catalog).resolve()
    )
    if policy.policy_id != settings.policy:
        raise ResourceAllocatorError(
            "runtime resource_allocator policy differs from frozen catalog"
        )
    return DeterministicAdaptiveAllocator(
        policy=policy,
        staged_model=staged_model,
        regime_model_id=regime_model_id,
    )
