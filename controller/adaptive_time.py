"""M14-J J8 adaptive outer resource planning.

The historical clock_envelope_v1 TimePlan remains the clock/deadline authority.
J8 adds a clamp-only MoveResourcePlan beneath it.  The plan may reduce the
resource envelope from live host facts, but may never extend the TimePlan,
create a WorkGrant, select a J7 operating point, or authorize an outward move.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from common.search_request import SearchRequestError, parse_go_request
from controller.budget import ResourceEnvelope
from controller.game_environment import EnvironmentSource, GameEnvironment
from controller.host_capabilities import HostCapabilities
from controller.move_resource_plan import (
    ADAPTIVE_DISPOSITION,
    FALLBACK_DISPOSITION,
    MoveResourcePlan,
)
from controller.online_time import (
    POLICY as BASELINE_CLOCK_POLICY,
    OnlineTimeSettings,
    TimePlan,
)
from controller.resource_profiles import (
    CompositionProfile,
    OrchestrationContractError,
    _mapping,
    _positive_int,
    _reject_unknown,
    _safe_id,
)


ADAPTIVE_CLOCK_POLICY = "adaptive_clock_envelope_v1"
ALLOCATOR_POLICY = "legacy-fixed-stage-compat-v1"
ADAPTIVE_ALLOCATOR_POLICY = "adaptive-resource-v1"
SUPPORTED_ALLOCATOR_POLICIES = (
    ALLOCATOR_POLICY,
    ADAPTIVE_ALLOCATOR_POLICY,
)
FALLBACK_PROFILE = "engine-opt-v2"
NETWORK_POLICY = "clock-envelope-v1"


class AdaptiveTimeError(OrchestrationContractError):
    """J8 configuration or deterministic plan reconstruction failed."""


@dataclass(frozen=True)
class AdaptiveTimeSettings:
    policy: str = ADAPTIVE_CLOCK_POLICY
    catalog: str = "qualification/resource-profile-catalog-v1.json"
    composition_id: str = "composition/engine-opt-v2-exact-host"
    fallback_clock_policy: str = BASELINE_CLOCK_POLICY
    fallback_profile: str = FALLBACK_PROFILE
    allocator_policy_id: str = ALLOCATOR_POLICY
    concurrency: int = 1
    network_policy: str = NETWORK_POLICY

    def __post_init__(self) -> None:
        if self.policy != ADAPTIVE_CLOCK_POLICY:
            raise AdaptiveTimeError(
                f"unsupported adaptive clock policy: {self.policy!r}"
            )
        if self.fallback_clock_policy != BASELINE_CLOCK_POLICY:
            raise AdaptiveTimeError(
                "J8 fallback clock policy must remain clock_envelope_v1"
            )
        if self.fallback_profile != FALLBACK_PROFILE:
            raise AdaptiveTimeError(
                "J8 fallback profile must remain engine-opt-v2"
            )
        if self.allocator_policy_id not in SUPPORTED_ALLOCATOR_POLICIES:
            raise AdaptiveTimeError(
                "allocator_policy_id must be one of "
                f"{list(SUPPORTED_ALLOCATOR_POLICIES)}"
            )
        _safe_id(self.composition_id, "J8 composition_id")
        _safe_id(self.network_policy, "J8 network_policy")
        _positive_int(self.concurrency, "J8 concurrency")
        if not isinstance(self.catalog, str) or not self.catalog:
            raise AdaptiveTimeError("J8 catalog must be a non-empty relative path")
        path = PurePosixPath(self.catalog)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise AdaptiveTimeError(
                "J8 catalog must be a normalized repository-relative path"
            )

    @classmethod
    def from_config(cls, raw: Any) -> "AdaptiveTimeSettings | None":
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise AdaptiveTimeError("orchestration must be an object")
        if not isinstance(raw.get("enabled"), bool):
            raise AdaptiveTimeError("orchestration.enabled must be boolean")
        allowed = {
            "enabled",
            "policy",
            "catalog",
            "composition_id",
            "fallback_clock_policy",
            "fallback_profile",
            "allocator_policy_id",
            "concurrency",
            "network_policy",
        }
        _reject_unknown(raw, allowed, "orchestration")
        item = cls(
            **{
                key: value
                for key, value in raw.items()
                if key != "enabled"
            }
        )
        return item if raw["enabled"] else None


def _limit_map(command: str) -> dict[str, int]:
    try:
        parsed = parse_go_request(command)
    except SearchRequestError as exc:
        raise AdaptiveTimeError(str(exc)) from exc
    if parsed.get("unknown_tokens"):
        raise AdaptiveTimeError(
            "J8 cannot derive GameEnvironment from malformed go request"
        )
    limits: dict[str, int] = {}
    for item in parsed.get("limits", []):
        if not isinstance(item, dict):
            raise AdaptiveTimeError("go request contains malformed limit")
        name = item.get("name")
        value = item.get("value")
        if (
            not isinstance(name, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or name in limits
        ):
            raise AdaptiveTimeError("go request limit map is not canonical")
        limits[name] = value
    return limits


def game_environment_from_time_plan(
    plan: TimePlan,
    *,
    settings: AdaptiveTimeSettings,
    host: HostCapabilities | None,
    composition: CompositionProfile,
) -> GameEnvironment:
    if not isinstance(plan, TimePlan):
        raise AdaptiveTimeError("baseline plan must be TimePlan")
    limits = _limit_map(plan.external_go_command)
    host_id = None if host is None else host.capability_id
    common = dict(
        concurrency=settings.concurrency,
        network_policy=settings.network_policy,
        network_reserve_ms=plan.network_reserve_ms,
        host_profile_id=host_id,
        candidate_composition_ids=(composition.composition_id,),
    )
    if plan.request_class == "clock_v1":
        if not {"wtime", "btime"}.issubset(limits):
            raise AdaptiveTimeError(
                "clock_v1 TimePlan no longer contains both UCI clocks"
            )
        return GameEnvironment(
            source=EnvironmentSource.UCI_OBSERVED,
            base_ms=None,
            white_increment_ms=limits.get("winc", 0),
            black_increment_ms=limits.get("binc", 0),
            moves_to_go=limits.get("movestogo"),
            white_time_ms=limits["wtime"],
            black_time_ms=limits["btime"],
            **common,
        )
    if plan.request_class == "movetime_deadline_v1":
        return GameEnvironment(
            source=EnvironmentSource.UNKNOWN,
            base_ms=None,
            white_increment_ms=None,
            black_increment_ms=None,
            moves_to_go=None,
            white_time_ms=None,
            black_time_ms=None,
            **common,
        )
    raise AdaptiveTimeError(
        f"unsupported TimePlan request class: {plan.request_class!r}"
    )


def _scaled_envelope(
    baseline: ResourceEnvelope,
    *,
    effective_parallelism: float,
) -> ResourceEnvelope:
    if baseline.gpu_ms != 0:
        raise AdaptiveTimeError("J8 currently supports CPU-only TimePlans")
    if effective_parallelism <= 0 or not math.isfinite(effective_parallelism):
        raise AdaptiveTimeError("effective parallelism must be finite and positive")
    cpu_ms = min(
        float(baseline.cpu_ms),
        float(baseline.wall_ms) * float(effective_parallelism),
    )
    if cpu_ms <= 0:
        raise AdaptiveTimeError("adaptive CPU ceiling collapsed to zero")
    return baseline.clamped_cpu_envelope(
        wall_ms=float(baseline.wall_ms),
        cpu_ms=cpu_ms,
    )


def _fallback_reason(
    host: HostCapabilities | None,
    composition: CompositionProfile,
) -> str | None:
    if host is None:
        return "HOST_DISCOVERY_UNAVAILABLE"
    if host.allowed_cpus is None:
        return "HOST_CPU_SET_UNKNOWN"
    if host.cpu_quota_status == "unknown":
        return "HOST_CPU_QUOTA_UNKNOWN"
    if (
        host.cgroup_memory_status == "unknown"
        or host.effective_memory_limit_bytes is None
        or host.effective_memory_limit_bytes <= 0
    ):
        return "HOST_MEMORY_UNKNOWN"
    if not host.capacity_complete:
        return "HOST_CAPACITY_INCOMPLETE"
    if (
        host.effective_memory_limit_bytes
        < composition.expected_memory_mib * 1024 * 1024
    ):
        return "HOST_MEMORY_INSUFFICIENT"
    return None


def build_move_resource_plan(
    *,
    baseline: TimePlan,
    settings: AdaptiveTimeSettings,
    host: HostCapabilities | None,
    composition: CompositionProfile,
    catalog_id: str,
    catalog_digest: str,
) -> MoveResourcePlan:
    if not isinstance(baseline, TimePlan):
        raise AdaptiveTimeError("baseline must be TimePlan")
    if baseline.policy != BASELINE_CLOCK_POLICY:
        raise AdaptiveTimeError(
            "J8 must be layered under clock_envelope_v1"
        )
    if not isinstance(settings, AdaptiveTimeSettings):
        raise AdaptiveTimeError("settings must be AdaptiveTimeSettings")
    if not isinstance(composition, CompositionProfile):
        raise AdaptiveTimeError("composition must be CompositionProfile")
    if composition.composition_id != settings.composition_id:
        raise AdaptiveTimeError(
            "configured J8 composition differs from planning composition"
        )
    _safe_id(catalog_id, "J8 catalog_id")
    if (
        not isinstance(catalog_digest, str)
        or len(catalog_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in catalog_digest)
    ):
        raise AdaptiveTimeError("J8 catalog digest must be lowercase SHA-256")

    baseline_dict = baseline.as_dict()
    environment = game_environment_from_time_plan(
        baseline,
        settings=settings,
        host=host,
        composition=composition,
    )
    reason = _fallback_reason(host, composition)
    effective_parallelism: float | None = None

    if reason is None:
        assert host is not None and host.allowed_cpus is not None
        host_parallelism = float(len(host.allowed_cpus))
        if host.cpu_quota_status == "limited":
            assert host.cpu_quota_equivalents is not None
            host_parallelism = min(
                host_parallelism,
                float(host.cpu_quota_equivalents),
            )
        effective_parallelism = min(
            float(baseline.settings.cpu_parallelism),
            float(composition.declared_cpu_slots),
            host_parallelism,
        )
        if effective_parallelism <= 0:
            reason = "HOST_CPU_CAPACITY_ZERO"

    if reason is None:
        envelope = _scaled_envelope(
            baseline.envelope,
            effective_parallelism=float(effective_parallelism),
        )
        disposition = ADAPTIVE_DISPOSITION
        host_capacity_claim = True
    else:
        envelope = baseline.envelope
        disposition = FALLBACK_DISPOSITION
        host_capacity_claim = False
        effective_parallelism = None

    if envelope.wall_ms > baseline.envelope.wall_ms + 1e-12:
        raise AdaptiveTimeError("J8 wall envelope exceeded TimePlan")
    if envelope.cpu_ms > baseline.envelope.cpu_ms + 1e-12:
        raise AdaptiveTimeError("J8 CPU envelope exceeded TimePlan")
    if envelope.gpu_ms > baseline.envelope.gpu_ms + 1e-12:
        raise AdaptiveTimeError("J8 GPU envelope exceeded TimePlan")

    return MoveResourcePlan(
        policy_id=settings.policy,
        generation=baseline.generation,
        position_id=baseline.position_id,
        baseline_time_plan_id=baseline_dict["plan_id"],
        game_environment=environment,
        host_capabilities=host,
        catalog_id=catalog_id,
        catalog_digest=catalog_digest,
        composition=composition,
        resource_envelope=envelope,
        soft_budget_ms=baseline.soft_budget_ms,
        hard_ceiling_ms=baseline.hard_budget_ms,
        prepare_budget_ms=baseline.prepare_budget_ms,
        output_margin_ms=baseline.output_margin_ms,
        effective_parallelism=effective_parallelism,
        allocator_policy_id=settings.allocator_policy_id,
        disposition=disposition,
        fallback_policy=settings.fallback_clock_policy,
        fallback_profile=settings.fallback_profile,
        fallback_reason=reason,
        host_capacity_claim=host_capacity_claim,
        composition_qualification_established=False,
        generic_host_portability_established=False,
    )


def _time_plan_from_dict(raw: Mapping[str, Any]) -> TimePlan:
    raw = _mapping(raw, "time_plan")
    settings_raw = _mapping(raw.get("settings"), "time_plan.settings")
    envelope_raw = _mapping(raw.get("envelope"), "time_plan.envelope")
    declared_raw = _mapping(
        raw.get("declared_envelope"), "time_plan.declared_envelope"
    )
    try:
        item = TimePlan(
            generation=raw.get("generation"),
            position_id=raw.get("position_id"),
            side_to_move=raw.get("side_to_move"),
            request_class=raw.get("request_class"),
            external_go_command=raw.get("external_go_command"),
            anchor_go_command=raw.get("anchor_go_command"),
            received_monotonic=raw.get("received_monotonic"),
            controller_cpu_started_ns=raw.get("controller_cpu_started_ns"),
            available_clock_ms=raw.get("available_clock_ms"),
            increment_ms=raw.get("increment_ms"),
            moves_to_go=raw.get("moves_to_go"),
            soft_budget_ms=raw.get("soft_budget_ms"),
            hard_budget_ms=raw.get("hard_budget_ms"),
            prepare_budget_ms=raw.get("prepare_budget_ms"),
            network_reserve_ms=raw.get("network_reserve_ms"),
            output_margin_ms=raw.get("output_margin_ms"),
            envelope=ResourceEnvelope(**dict(envelope_raw)),
            declared_envelope=ResourceEnvelope(**dict(declared_raw)),
            settings=OnlineTimeSettings(**dict(settings_raw)),
            policy=raw.get("policy"),
        )
    except (TypeError, ValueError) as exc:
        raise AdaptiveTimeError(f"cannot reconstruct TimePlan: {exc}") from exc
    if item.as_dict() != dict(raw):
        raise AdaptiveTimeError(
            "MoveResourcePlan parent TimePlan does not reconstruct exactly"
        )
    return item


def verify_move_resource_plan_manifest(
    manifest: Mapping[str, Any],
) -> list[str]:
    """Reconstruct a sealed J8 plan from replay inputs.

    The historical TimePlan verifier remains independent and unchanged.  This
    verifier checks only the additive J8 resource ceiling.
    """

    raw = manifest.get("move_resource_plan")
    if raw is None:
        return []
    try:
        if not isinstance(raw, Mapping):
            raise AdaptiveTimeError("move_resource_plan must be an object")
        baseline_raw = manifest.get("time_plan")
        if not isinstance(baseline_raw, Mapping):
            raise AdaptiveTimeError(
                "move_resource_plan requires its baseline time_plan"
            )
        baseline = _time_plan_from_dict(baseline_raw)
        item = MoveResourcePlan.from_dict(raw)

        # A content digest is not an authentication mechanism. Reconstruct the
        # plan against the repository's frozen J3 catalog rather than accepting
        # an arbitrarily self-consistent embedded composition/catalog identity.
        from controller.resource_profile_catalog import load_resource_profile_catalog
        catalog_path = (
            Path(__file__).resolve().parents[1]
            / "qualification"
            / "resource-profile-catalog-v1.json"
        )
        catalog = load_resource_profile_catalog(catalog_path)
        if catalog.selection_enabled:
            raise AdaptiveTimeError(
                "J8 replay verifier refuses runtime-enabled profile selection"
            )
        if (
            item.catalog_id != catalog.catalog_id
            or item.catalog_digest != catalog.digest
        ):
            raise AdaptiveTimeError(
                "MoveResourcePlan catalog identity differs from frozen J3 catalog"
            )
        frozen_composition = catalog.composition(item.composition.composition_id)
        if item.composition.as_dict() != frozen_composition.as_dict():
            raise AdaptiveTimeError(
                "MoveResourcePlan composition differs from frozen J3 catalog"
            )
        if item.fallback_profile != catalog.fallback_profile:
            raise AdaptiveTimeError(
                "MoveResourcePlan fallback differs from frozen J3 catalog"
            )

        settings = AdaptiveTimeSettings(
            policy=item.policy_id,
            composition_id=item.composition.composition_id,
            fallback_clock_policy=item.fallback_policy,
            fallback_profile=item.fallback_profile,
            allocator_policy_id=item.allocator_policy_id,
            concurrency=item.game_environment.concurrency,
            network_policy=item.game_environment.network_policy,
        )
        expected = build_move_resource_plan(
            baseline=baseline,
            settings=settings,
            host=item.host_capabilities,
            composition=item.composition,
            catalog_id=item.catalog_id,
            catalog_digest=item.catalog_digest,
        )
        if expected.as_dict() != dict(raw):
            raise AdaptiveTimeError(
                "move_resource_plan does not reconstruct from sealed inputs"
            )
        if item.generation != manifest.get("generation"):
            raise AdaptiveTimeError(
                "move_resource_plan generation differs from replay"
            )
        position = manifest.get("position")
        if (
            not isinstance(position, Mapping)
            or position.get("position_id") != item.position_id
        ):
            raise AdaptiveTimeError(
                "move_resource_plan position differs from replay"
            )
        return []
    except (
        AdaptiveTimeError,
        OrchestrationContractError,
        TypeError,
        ValueError,
    ) as exc:
        return [f"move resource plan integrity: {exc}"]
