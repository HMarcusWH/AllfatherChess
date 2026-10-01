"""Immutable M14-J J8 per-move resource-plan contract.

A MoveResourcePlan is a resource ceiling below the already-qualified ONLINE
TimePlan.  It is not a WorkGrant and never grants chess-move authority.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from controller.budget import ResourceEnvelope
from controller.decision import canonical_digest
from controller.game_environment import GameEnvironment
from controller.host_capabilities import HostCapabilities
from controller.resource_profiles import (
    CompositionProfile,
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _mapping,
    _nonnegative_int,
    _positive_int,
    _reject_unknown,
    _safe_id,
    _sha256,
)


MOVE_RESOURCE_PLAN_VERSION = "move-resource-plan-v1"
ADAPTIVE_DISPOSITION = "ADAPTIVE"
FALLBACK_DISPOSITION = "FALLBACK"
_DISPOSITIONS = (ADAPTIVE_DISPOSITION, FALLBACK_DISPOSITION)
_TIME_PLAN_ID_RE = re.compile(r"^time-[0-9a-f]{64}$")
_PLAN_ID_RE = re.compile(r"^move-plan/[0-9a-f]{64}$")
_ENVELOPE_FIELDS = {
    "wall_ms",
    "cpu_ms",
    "gpu_ms",
    "verification_reserve_fraction",
    "refinement_reserve_fraction",
    "controller_overhead_reserve_ms",
}


def _finite_positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrchestrationContractError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise OrchestrationContractError(f"{label} must be finite and positive")
    return number


def _optional_finite_positive(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _finite_positive(value, label)


def envelope_dict(envelope: ResourceEnvelope) -> dict[str, float]:
    if not isinstance(envelope, ResourceEnvelope):
        raise OrchestrationContractError("resource_envelope must be ResourceEnvelope")
    return {
        "wall_ms": float(envelope.wall_ms),
        "cpu_ms": float(envelope.cpu_ms),
        "gpu_ms": float(envelope.gpu_ms),
        "verification_reserve_fraction": float(
            envelope.verification_reserve_fraction
        ),
        "refinement_reserve_fraction": float(
            envelope.refinement_reserve_fraction
        ),
        "controller_overhead_reserve_ms": float(
            envelope.controller_overhead_reserve_ms
        ),
    }


def envelope_from_dict(raw: Mapping[str, Any]) -> ResourceEnvelope:
    raw = _mapping(raw, "move resource envelope")
    _reject_unknown(raw, _ENVELOPE_FIELDS, "move resource envelope")
    if set(raw) != _ENVELOPE_FIELDS:
        missing = sorted(_ENVELOPE_FIELDS - set(raw))
        raise OrchestrationContractError(
            f"move resource envelope is missing fields: {missing}"
        )
    return ResourceEnvelope(
        wall_ms=raw["wall_ms"],
        cpu_ms=raw["cpu_ms"],
        gpu_ms=raw["gpu_ms"],
        verification_reserve_fraction=raw["verification_reserve_fraction"],
        refinement_reserve_fraction=raw["refinement_reserve_fraction"],
        controller_overhead_reserve_ms=raw["controller_overhead_reserve_ms"],
    )


@dataclass(frozen=True)
class MoveResourcePlan:
    policy_id: str
    generation: int
    position_id: str
    baseline_time_plan_id: str
    game_environment: GameEnvironment
    host_capabilities: HostCapabilities | None
    catalog_id: str
    catalog_digest: str
    composition: CompositionProfile
    resource_envelope: ResourceEnvelope
    soft_budget_ms: int
    hard_ceiling_ms: int
    prepare_budget_ms: int
    output_margin_ms: int
    effective_parallelism: float | None
    allocator_policy_id: str
    disposition: str
    fallback_policy: str
    fallback_profile: str
    fallback_reason: str | None
    host_capacity_claim: bool
    composition_qualification_established: bool = False
    generic_host_portability_established: bool = False
    plan_version: str = MOVE_RESOURCE_PLAN_VERSION

    def __post_init__(self) -> None:
        if self.plan_version != MOVE_RESOURCE_PLAN_VERSION:
            raise OrchestrationContractError(
                f"unsupported move resource plan version: {self.plan_version!r}"
            )
        _safe_id(self.policy_id, "move resource policy_id")
        _positive_int(self.generation, "move resource generation")
        if (
            not isinstance(self.position_id, str)
            or not self.position_id
            or "\x00" in self.position_id
        ):
            raise OrchestrationContractError(
                "move resource position_id must be a non-empty NUL-free string"
            )
        if (
            not isinstance(self.baseline_time_plan_id, str)
            or _TIME_PLAN_ID_RE.fullmatch(self.baseline_time_plan_id) is None
        ):
            raise OrchestrationContractError(
                "baseline_time_plan_id must be a TimePlan content identity"
            )
        if not isinstance(self.game_environment, GameEnvironment):
            raise OrchestrationContractError(
                "game_environment must be GameEnvironment"
            )
        if self.host_capabilities is not None and not isinstance(
            self.host_capabilities, HostCapabilities
        ):
            raise OrchestrationContractError(
                "host_capabilities must be HostCapabilities or null"
            )
        _safe_id(self.catalog_id, "move resource catalog_id")
        _sha256(self.catalog_digest, "move resource catalog_digest")
        if not isinstance(self.composition, CompositionProfile):
            raise OrchestrationContractError(
                "composition must be CompositionProfile"
            )
        if not isinstance(self.resource_envelope, ResourceEnvelope):
            raise OrchestrationContractError(
                "resource_envelope must be ResourceEnvelope"
            )
        if (
            self.resource_envelope.wall_ms <= 0
            or self.resource_envelope.cpu_ms <= 0
            or self.resource_envelope.gpu_ms != 0
        ):
            raise OrchestrationContractError(
                "J8 MoveResourcePlan requires a positive CPU-only envelope"
            )
        _positive_int(self.soft_budget_ms, "soft_budget_ms")
        _positive_int(self.hard_ceiling_ms, "hard_ceiling_ms")
        _nonnegative_int(self.prepare_budget_ms, "prepare_budget_ms")
        _positive_int(self.output_margin_ms, "output_margin_ms")
        if self.soft_budget_ms >= self.hard_ceiling_ms:
            raise OrchestrationContractError(
                "soft_budget_ms must be strictly below hard_ceiling_ms"
            )
        object.__setattr__(
            self,
            "effective_parallelism",
            _optional_finite_positive(
                self.effective_parallelism, "effective_parallelism"
            ),
        )
        _safe_id(self.allocator_policy_id, "allocator_policy_id")
        if self.disposition not in _DISPOSITIONS:
            raise OrchestrationContractError(
                f"disposition must be one of {list(_DISPOSITIONS)}"
            )
        _safe_id(self.fallback_policy, "fallback_policy")
        _safe_id(self.fallback_profile, "fallback_profile")

        if self.disposition == ADAPTIVE_DISPOSITION:
            if self.fallback_reason is not None:
                raise OrchestrationContractError(
                    "ADAPTIVE plan may not carry fallback_reason"
                )
            if self.host_capabilities is None:
                raise OrchestrationContractError(
                    "ADAPTIVE plan requires host capabilities"
                )
            if self.effective_parallelism is None:
                raise OrchestrationContractError(
                    "ADAPTIVE plan requires effective_parallelism"
                )
            if self.host_capacity_claim is not True:
                raise OrchestrationContractError(
                    "ADAPTIVE plan must assert its host-capacity clamp"
                )
        else:
            _safe_id(self.fallback_reason, "fallback_reason")
            if self.host_capacity_claim is not False:
                raise OrchestrationContractError(
                    "FALLBACK plan may not claim adaptive host capacity"
                )

        if not isinstance(self.host_capacity_claim, bool):
            raise OrchestrationContractError(
                "host_capacity_claim must be boolean"
            )
        if self.composition_qualification_established is not False:
            raise OrchestrationContractError(
                "J8 may not establish composition qualification"
            )
        if self.generic_host_portability_established is not False:
            raise OrchestrationContractError(
                "J8 may not establish generic host portability"
            )

    def payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "plan_version": self.plan_version,
            "policy_id": self.policy_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "baseline_time_plan_id": self.baseline_time_plan_id,
            "game_environment": self.game_environment.as_dict(),
            "host_capabilities": (
                None
                if self.host_capabilities is None
                else self.host_capabilities.as_dict()
            ),
            "catalog_id": self.catalog_id,
            "catalog_digest": self.catalog_digest,
            "composition": self.composition.as_dict(),
            "resource_envelope": envelope_dict(self.resource_envelope),
            "soft_budget_ms": self.soft_budget_ms,
            "hard_ceiling_ms": self.hard_ceiling_ms,
            "prepare_budget_ms": self.prepare_budget_ms,
            "output_margin_ms": self.output_margin_ms,
            "effective_parallelism": self.effective_parallelism,
            "allocator_policy_id": self.allocator_policy_id,
            "disposition": self.disposition,
            "fallback_policy": self.fallback_policy,
            "fallback_profile": self.fallback_profile,
            "fallback_reason": self.fallback_reason,
            "host_capacity_claim": self.host_capacity_claim,
            "composition_qualification_established": (
                self.composition_qualification_established
            ),
            "generic_host_portability_established": (
                self.generic_host_portability_established
            ),
            "authority": {
                "resource_plan": True,
                "resource_authorization": False,
                "outward_move": False,
            },
            "claim_boundary": {
                "adaptive_resource_ceiling": self.host_capacity_claim,
                "composition_qualification": False,
                "runtime_profile_selection": False,
                "work_grant": False,
                "outward_move": False,
                "strength": False,
                "elo": False,
                "equal_compute": False,
                "deployment": False,
            },
        }

    @property
    def plan_id(self) -> str:
        return f"move-plan/{canonical_digest(self.payload_dict())}"

    @property
    def digest(self) -> str:
        return self.plan_id.split("/", 1)[1]

    def as_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, **self.payload_dict()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MoveResourcePlan":
        raw = _mapping(raw, "move resource plan")
        allowed = {
            "plan_id",
            "schema_version",
            "plan_version",
            "policy_id",
            "generation",
            "position_id",
            "baseline_time_plan_id",
            "game_environment",
            "host_capabilities",
            "catalog_id",
            "catalog_digest",
            "composition",
            "resource_envelope",
            "soft_budget_ms",
            "hard_ceiling_ms",
            "prepare_budget_ms",
            "output_margin_ms",
            "effective_parallelism",
            "allocator_policy_id",
            "disposition",
            "fallback_policy",
            "fallback_profile",
            "fallback_reason",
            "host_capacity_claim",
            "composition_qualification_established",
            "generic_host_portability_established",
            "authority",
            "claim_boundary",
        }
        _reject_unknown(raw, allowed, "move resource plan")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                "unsupported move resource plan schema_version"
            )
        if raw.get("authority") != {
            "resource_plan": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError(
                "move resource plan authority marker is invalid"
            )
        host_raw = raw.get("host_capabilities")
        if host_raw is not None:
            host_raw = _mapping(host_raw, "host_capabilities")
        item = cls(
            plan_version=raw.get("plan_version"),
            policy_id=raw.get("policy_id"),
            generation=raw.get("generation"),
            position_id=raw.get("position_id"),
            baseline_time_plan_id=raw.get("baseline_time_plan_id"),
            game_environment=GameEnvironment.from_dict(
                _mapping(raw.get("game_environment"), "game_environment")
            ),
            host_capabilities=(
                None
                if host_raw is None
                else HostCapabilities.from_dict(host_raw)
            ),
            catalog_id=raw.get("catalog_id"),
            catalog_digest=raw.get("catalog_digest"),
            composition=CompositionProfile.from_dict(
                _mapping(raw.get("composition"), "composition")
            ),
            resource_envelope=envelope_from_dict(
                _mapping(raw.get("resource_envelope"), "resource_envelope")
            ),
            soft_budget_ms=raw.get("soft_budget_ms"),
            hard_ceiling_ms=raw.get("hard_ceiling_ms"),
            prepare_budget_ms=raw.get("prepare_budget_ms"),
            output_margin_ms=raw.get("output_margin_ms"),
            effective_parallelism=raw.get("effective_parallelism"),
            allocator_policy_id=raw.get("allocator_policy_id"),
            disposition=raw.get("disposition"),
            fallback_policy=raw.get("fallback_policy"),
            fallback_profile=raw.get("fallback_profile"),
            fallback_reason=raw.get("fallback_reason"),
            host_capacity_claim=raw.get("host_capacity_claim"),
            composition_qualification_established=raw.get(
                "composition_qualification_established"
            ),
            generic_host_portability_established=raw.get(
                "generic_host_portability_established"
            ),
        )
        claimed = raw.get("plan_id")
        if (
            not isinstance(claimed, str)
            or _PLAN_ID_RE.fullmatch(claimed) is None
            or claimed != item.plan_id
        ):
            raise OrchestrationContractError(
                "move resource plan plan_id does not match canonical payload"
            )
        if raw.get("claim_boundary") != item.payload_dict()["claim_boundary"]:
            raise OrchestrationContractError(
                "move resource plan claim boundary is invalid"
            )
        return item
