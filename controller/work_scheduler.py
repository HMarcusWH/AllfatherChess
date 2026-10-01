"""M14-J J9 compatibility WorkGrant scheduler.

J9 does not decide which computation is valuable. It translates the historical
fixed EXPLORE/VERIFY/STAGED_VERIFY sequence into typed WorkGrants whose native
limits, profile identity, parent MoveResourcePlan, deadline and compatibility
license are explicit.

The router remains the sole BudgetLedger authority. A scheduler can propose a
WorkGrant; only a GrantAdmission backed by one router reservation may dispatch.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.move_resource_plan import ADAPTIVE_DISPOSITION, MoveResourcePlan
from controller.resource_profile_catalog import (
    ResourceProfileCatalog,
    ResourceProfileCatalogError,
    load_resource_profile_catalog,
)
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _family,
    _mapping,
    _nonnegative_int,
    _positive_int,
    _reject_unknown,
    _safe_id,
)
from controller.work_grant import (
    NativeLimit,
    WorkChunk,
    WorkChunkLicense,
    WorkGrant,
)


WORK_SCHEDULER_POLICY = "legacy_fixed_workgrant_v1"
WORK_SCHEDULER_CATALOG_ID = "work-grant-scheduler-v1"
RESERVATION_BASIS = "legacy_reservation_compatibility_v1"
PARENT_FALLBACK_POLICY = "j8_legacy_path"
GRANT_DENIAL_POLICY = "no_dispatch"


class WorkSchedulerError(OrchestrationContractError):
    """Malformed J9 scheduler configuration or frozen catalog."""


class WorkSchedulerDenied(WorkSchedulerError):
    """A requested compatibility grant is not legal in the current plan."""


@dataclass(frozen=True)
class WorkSchedulerSettings:
    policy: str = WORK_SCHEDULER_POLICY
    catalog: str = "qualification/work-grant-scheduler-v1.json"
    round_count: int = 3
    max_grants_per_round: int = 3
    on_parent_plan_fallback: str = PARENT_FALLBACK_POLICY
    on_grant_denial: str = GRANT_DENIAL_POLICY

    def __post_init__(self) -> None:
        if self.policy != WORK_SCHEDULER_POLICY:
            raise WorkSchedulerError(
                f"unsupported WorkGrant scheduler policy: {self.policy!r}"
            )
        if not isinstance(self.catalog, str) or not self.catalog:
            raise WorkSchedulerError(
                "work_scheduler.catalog must be a non-empty relative path"
            )
        path = PurePosixPath(self.catalog)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise WorkSchedulerError(
                "work_scheduler.catalog must be normalized and repository-relative"
            )
        _positive_int(self.round_count, "work_scheduler.round_count")
        _positive_int(
            self.max_grants_per_round,
            "work_scheduler.max_grants_per_round",
        )
        if self.round_count != 3:
            raise WorkSchedulerError("J9 compatibility scheduler requires exactly 3 rounds")
        if self.max_grants_per_round != 3:
            raise WorkSchedulerError(
                "J9 compatibility scheduler requires exactly 3 grants per round"
            )
        if self.on_parent_plan_fallback != PARENT_FALLBACK_POLICY:
            raise WorkSchedulerError(
                "J9 parent-plan fallback must preserve the J8 legacy path"
            )
        if self.on_grant_denial != GRANT_DENIAL_POLICY:
            raise WorkSchedulerError(
                "J9 grant denial must mean no dispatch"
            )

    @classmethod
    def from_config(cls, raw: Any) -> "WorkSchedulerSettings | None":
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise WorkSchedulerError("work_scheduler must be an object")
        if not isinstance(raw.get("enabled"), bool):
            raise WorkSchedulerError("work_scheduler.enabled must be boolean")
        allowed = {
            "enabled",
            "policy",
            "catalog",
            "round_count",
            "max_grants_per_round",
            "on_parent_plan_fallback",
            "on_grant_denial",
        }
        _reject_unknown(raw, allowed, "work_scheduler")
        item = cls(
            **{
                key: value
                for key, value in raw.items()
                if key != "enabled"
            }
        )
        return item if raw["enabled"] else None


@dataclass(frozen=True)
class GrantAdmission:
    """Runtime proof that one WorkGrant owns one BudgetLedger reservation."""

    token: str
    grant: WorkGrant
    reservation_id: int
    search_id: str

    def __post_init__(self) -> None:
        _safe_id(self.token, "grant admission token")
        if not isinstance(self.grant, WorkGrant):
            raise WorkSchedulerError("GrantAdmission.grant must be WorkGrant")
        _positive_int(self.reservation_id, "grant admission reservation_id")
        if not isinstance(self.search_id, str) or not self.search_id:
            raise WorkSchedulerError("grant admission search_id must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "reservation_id": self.reservation_id,
            "search_id": self.search_id,
            "grant": self.grant.as_dict(),
        }


@dataclass(frozen=True)
class SchedulerChunk:
    allocation_round: int
    profile_id: str
    chunk: WorkChunk

    def __post_init__(self) -> None:
        _nonnegative_int(self.allocation_round, "allocation_round")
        _safe_id(self.profile_id, "scheduler chunk profile_id")
        if not isinstance(self.chunk, WorkChunk):
            raise WorkSchedulerError("scheduler chunk requires WorkChunk")


@dataclass(frozen=True)
class WorkSchedulerCatalog:
    catalog_id: str
    policy_id: str
    resource_catalog_path: str
    resource_catalog_id: str
    resource_catalog_digest: str
    round_count: int
    max_grants_per_round: int
    reservation_basis: str
    cost_bound_claim: bool
    source_policy: Mapping[str, Any]
    chunks: tuple[SchedulerChunk, ...]
    licenses: tuple[WorkChunkLicense, ...]

    def __post_init__(self) -> None:
        if self.catalog_id != WORK_SCHEDULER_CATALOG_ID:
            raise WorkSchedulerError(
                f"unexpected scheduler catalog id: {self.catalog_id!r}"
            )
        if self.policy_id != WORK_SCHEDULER_POLICY:
            raise WorkSchedulerError(
                f"unexpected scheduler policy id: {self.policy_id!r}"
            )
        _safe_id(self.resource_catalog_id, "resource_catalog_id")
        if (
            not isinstance(self.resource_catalog_digest, str)
            or len(self.resource_catalog_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in self.resource_catalog_digest)
        ):
            raise WorkSchedulerError("resource_catalog_digest must be lowercase SHA-256")
        _positive_int(self.round_count, "scheduler catalog round_count")
        _positive_int(
            self.max_grants_per_round,
            "scheduler catalog max_grants_per_round",
        )
        if self.round_count != 3 or self.max_grants_per_round != 3:
            raise WorkSchedulerError(
                "J9 compatibility catalog must freeze 3 rounds x 3 owners"
            )
        if self.reservation_basis != RESERVATION_BASIS:
            raise WorkSchedulerError("unexpected J9 reservation basis")
        if self.cost_bound_claim is not False:
            raise WorkSchedulerError(
                "J9 legacy reservation estimates may not be promoted as cost bounds"
            )
        if len(self.chunks) != 9:
            raise WorkSchedulerError(
                "J9 compatibility catalog must contain exactly nine WorkChunks"
            )
        keys = [
            (
                item.allocation_round,
                item.chunk.family,
                item.chunk.phase,
            )
            for item in self.chunks
        ]
        if len(keys) != len(set(keys)):
            raise WorkSchedulerError(
                "J9 compatibility catalog contains duplicate round/family/phase chunks"
            )
        expected = {
            (0, family, "EXPLORE")
            for family in ("stockfish", "reckless", "lc0")
        } | {
            (1, family, "VERIFY")
            for family in ("stockfish", "reckless", "lc0")
        } | {
            (2, family, "STAGED_VERIFY")
            for family in ("stockfish", "reckless", "lc0")
        }
        if set(keys) != expected:
            raise WorkSchedulerError(
                "J9 compatibility chunk grid must be EXPLORE/VERIFY/STAGED_VERIFY "
                "for stockfish/reckless/lc0"
            )

    @property
    def source_policy_digest(self) -> str:
        return canonical_digest(dict(self.source_policy))

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "policy_id": self.policy_id,
            "resource_catalog": {
                "path": self.resource_catalog_path,
                "catalog_id": self.resource_catalog_id,
                "catalog_digest": self.resource_catalog_digest,
            },
            "round_count": self.round_count,
            "max_grants_per_round": self.max_grants_per_round,
            "reservation_basis": self.reservation_basis,
            "cost_bound_claim": self.cost_bound_claim,
            "source_policy": dict(self.source_policy),
            "chunks": [
                {
                    "allocation_round": item.allocation_round,
                    "profile_id": item.profile_id,
                    **item.chunk.as_dict(),
                }
                for item in sorted(
                    self.chunks,
                    key=lambda row: (
                        row.allocation_round,
                        row.chunk.family,
                        row.chunk.phase,
                    ),
                )
            ],
            "licenses": [
                item.as_dict()
                for item in sorted(self.licenses, key=lambda row: row.license_id)
            ],
            "authority": {
                "resource_template": True,
                "resource_authorization": False,
                "outward_move": False,
            },
            "claim_boundary": {
                "legacy_reservation_compatibility": True,
                "measured_cost_bound": False,
                "adaptive_allocation": False,
                "work_grant_authority": True,
                "outward_move": False,
                "strength": False,
                "elo": False,
                "equal_compute": False,
                "deployment": False,
            },
        }

    def chunk_for(
        self,
        *,
        allocation_round: int,
        family: str,
        phase: str,
    ) -> SchedulerChunk:
        family = _family(family)
        matches = [
            row
            for row in self.chunks
            if (
                row.allocation_round == allocation_round
                and row.chunk.family == family
                and row.chunk.phase == phase
            )
        ]
        if len(matches) != 1:
            raise WorkSchedulerDenied(
                f"no unique J9 compatibility chunk for "
                f"round={allocation_round}, family={family}, phase={phase}"
            )
        return matches[0]

    def license_for(self, profile_id: str, chunk_id: str) -> WorkChunkLicense:
        matches = [
            item
            for item in self.licenses
            if item.profile_id == profile_id and chunk_id in item.work_chunk_ids
        ]
        if len(matches) != 1:
            raise WorkSchedulerDenied(
                f"profile {profile_id!r} has no unique compatibility license "
                f"for chunk {chunk_id!r}"
            )
        return matches[0]


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise WorkSchedulerError(f"{label} must be boolean")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkSchedulerError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkSchedulerError(f"{path}: root must be object")
    return value


def load_work_scheduler_catalog(
    path: Path | str,
    *,
    resource_catalog: ResourceProfileCatalog | None = None,
) -> tuple[WorkSchedulerCatalog, ResourceProfileCatalog]:
    """Load the frozen J9 compatibility overlay against the unchanged J3 catalog."""

    path = Path(path)
    raw = _load_json(path)
    allowed = {
        "schema_version",
        "catalog_id",
        "policy_id",
        "resource_catalog",
        "round_count",
        "max_grants_per_round",
        "reservation_basis",
        "cost_bound_claim",
        "source_policy",
        "chunks",
        "authority",
        "claim_boundary",
    }
    _reject_unknown(raw, allowed, "work scheduler catalog")
    if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
        raise WorkSchedulerError("unsupported work scheduler schema_version")
    if raw.get("authority") != {
        "resource_template": True,
        "resource_authorization": False,
        "outward_move": False,
    }:
        raise WorkSchedulerError("work scheduler catalog authority marker is invalid")
    claim = raw.get("claim_boundary")
    if claim != {
        "legacy_reservation_compatibility": True,
        "measured_cost_bound": False,
        "adaptive_allocation": False,
        "work_grant_authority": True,
        "outward_move": False,
        "strength": False,
        "elo": False,
        "equal_compute": False,
        "deployment": False,
    }:
        raise WorkSchedulerError("work scheduler claim boundary is invalid")

    resource_raw = _mapping(raw.get("resource_catalog"), "resource_catalog")
    _reject_unknown(
        resource_raw,
        {"path", "catalog_id", "catalog_digest"},
        "resource_catalog",
    )
    resource_path_value = resource_raw.get("path")
    if not isinstance(resource_path_value, str) or not resource_path_value:
        raise WorkSchedulerError("resource_catalog.path must be non-empty")
    resource_path = PurePosixPath(resource_path_value)
    if (
        resource_path.is_absolute()
        or any(part in ("", ".", "..") for part in resource_path.parts)
    ):
        raise WorkSchedulerError(
            "resource_catalog.path must be normalized and repository-relative"
        )
    if resource_catalog is None:
        root = path.resolve().parents[1]
        resource_catalog = load_resource_profile_catalog(root / resource_path_value)
    if resource_catalog.catalog_id != resource_raw.get("catalog_id"):
        raise WorkSchedulerError("J9 resource catalog identity mismatch")
    if resource_catalog.digest != resource_raw.get("catalog_digest"):
        raise WorkSchedulerError("J9 resource catalog digest mismatch")
    if resource_catalog.selection_enabled:
        raise WorkSchedulerError(
            "J9 compatibility scheduler refuses runtime-enabled profile selection"
        )

    source_policy = _mapping(raw.get("source_policy"), "source_policy")
    if dict(source_policy) != resource_catalog.legacy_policy:
        raise WorkSchedulerError(
            "J9 source_policy differs from frozen J3 legacy policy"
        )
    source_policy_digest = canonical_digest(dict(source_policy))
    reservation_basis = raw.get("reservation_basis")
    if reservation_basis != RESERVATION_BASIS:
        raise WorkSchedulerError("J9 reservation basis drift")
    cost_bound_claim = _require_bool(raw.get("cost_bound_claim"), "cost_bound_claim")
    if cost_bound_claim:
        raise WorkSchedulerError(
            "legacy compatibility reservations may not be promoted to cost bounds"
        )

    chunks_raw = raw.get("chunks")
    if not isinstance(chunks_raw, list):
        raise WorkSchedulerError("chunks must be an array")
    chunks: list[SchedulerChunk] = []
    by_profile: dict[str, list[str]] = {}
    for index, row_value in enumerate(chunks_raw):
        row = _mapping(row_value, f"chunks[{index}]")
        allowed_chunk = {
            "allocation_round",
            "profile_id",
            "chunk_id",
            "family",
            "phase",
            "purpose",
            "native_limit",
            "reserved_cpu_ms",
            "reserved_gpu_ms",
            "wall_bound_ms",
        }
        _reject_unknown(row, allowed_chunk, f"chunks[{index}]")
        allocation_round = _nonnegative_int(
            row.get("allocation_round"),
            f"chunks[{index}].allocation_round",
        )
        profile_id = row.get("profile_id")
        _safe_id(profile_id, f"chunks[{index}].profile_id")
        profile = resource_catalog.profile(profile_id)
        family = _family(row.get("family"))
        if profile.family != family:
            raise WorkSchedulerError(
                f"chunks[{index}] family differs from profile family"
            )
        phase = row.get("phase")
        expected_round_phase = {
            0: "EXPLORE",
            1: "VERIFY",
            2: "STAGED_VERIFY",
        }
        if expected_round_phase.get(allocation_round) != phase:
            raise WorkSchedulerError(
                f"chunks[{index}] round/phase mapping is not the frozen J9 sequence"
            )
        native_limit = NativeLimit.from_dict(
            _mapping(row.get("native_limit"), f"chunks[{index}].native_limit")
        )
        native_limit.validate_for_family(family)

        legacy_phase = str(phase)
        legacy_limit = resource_catalog.legacy_policy["dispatch_limits"][legacy_phase]
        if (
            native_limit.kind.value != "nodes"
            or set(legacy_limit) != {"nodes"}
            or native_limit.value != int(legacy_limit["nodes"])
        ):
            raise WorkSchedulerError(
                f"chunks[{index}] native limit differs from frozen J3 dispatch limit"
            )
        cost_phase = "explore" if phase == "EXPLORE" else "verify"
        expected_cpu = float(
            resource_catalog.legacy_policy["resource_estimates_ms"][cost_phase][family]
        )
        reserved_cpu = row.get("reserved_cpu_ms")
        reserved_gpu = row.get("reserved_gpu_ms")
        if (
            isinstance(reserved_cpu, bool)
            or not isinstance(reserved_cpu, (int, float))
            or not math.isfinite(float(reserved_cpu))
            or float(reserved_cpu) != expected_cpu
        ):
            raise WorkSchedulerError(
                f"chunks[{index}] CPU reservation differs from J3 legacy policy"
            )
        if (
            isinstance(reserved_gpu, bool)
            or not isinstance(reserved_gpu, (int, float))
            or float(reserved_gpu) != 0.0
        ):
            raise WorkSchedulerError("J9 compatibility chunks are CPU-only")
        wall_bound = row.get("wall_bound_ms")
        if (
            isinstance(wall_bound, bool)
            or not isinstance(wall_bound, (int, float))
            or not math.isfinite(float(wall_bound))
            or float(wall_bound) != 4000.0
        ):
            raise WorkSchedulerError(
                "J9 compatibility wall_bound_ms is the frozen 4000ms parent move ceiling"
            )
        chunk_id = row.get("chunk_id")
        _safe_id(chunk_id, f"chunks[{index}].chunk_id")
        cost_evidence_digest = canonical_digest(
            {
                "reservation_basis": reservation_basis,
                "resource_catalog_digest": resource_catalog.digest,
                "source_policy_digest": source_policy_digest,
                "allocation_round": allocation_round,
                "profile_id": profile_id,
                "chunk_id": chunk_id,
                "family": family,
                "phase": phase,
                "native_limit": native_limit.as_dict(),
                "reserved_cpu_ms": expected_cpu,
                "reserved_gpu_ms": 0.0,
                "wall_bound_ms": 4000.0,
                "cost_bound_claim": False,
            }
        )
        chunk = WorkChunk(
            chunk_id=chunk_id,
            family=family,
            phase=phase,
            purpose=row.get("purpose"),
            native_limit=native_limit,
            reserved_cpu_ms=expected_cpu,
            reserved_gpu_ms=0.0,
            wall_bound_ms=4000.0,
            cost_evidence_digest=cost_evidence_digest,
        )
        chunks.append(
            SchedulerChunk(
                allocation_round=allocation_round,
                profile_id=profile_id,
                chunk=chunk,
            )
        )
        by_profile.setdefault(profile_id, []).append(chunk.chunk_id)

    licenses: list[WorkChunkLicense] = []
    for profile_id, chunk_ids in sorted(by_profile.items()):
        profile = resource_catalog.profile(profile_id)
        licenses.append(
            WorkChunkLicense(
                license_id=f"compat-license/{profile.family}/engine-opt-v2",
                catalog_id=resource_catalog.catalog_id,
                catalog_digest=resource_catalog.digest,
                profile_id=profile_id,
                profile_digest=profile.digest,
                work_chunk_ids=tuple(sorted(chunk_ids)),
                source_policy_digest=source_policy_digest,
            )
        )

    catalog = WorkSchedulerCatalog(
        catalog_id=raw.get("catalog_id"),
        policy_id=raw.get("policy_id"),
        resource_catalog_path=resource_path_value,
        resource_catalog_id=resource_catalog.catalog_id,
        resource_catalog_digest=resource_catalog.digest,
        round_count=raw.get("round_count"),
        max_grants_per_round=raw.get("max_grants_per_round"),
        reservation_basis=reservation_basis,
        cost_bound_claim=cost_bound_claim,
        source_policy=dict(source_policy),
        chunks=tuple(chunks),
        licenses=tuple(licenses),
    )
    return catalog, resource_catalog


class LegacyFixedWorkGrantScheduler:
    """Pure compatibility translator from one fixed historical stage to a WorkGrant."""

    def __init__(
        self,
        *,
        catalog: WorkSchedulerCatalog,
        resource_catalog: ResourceProfileCatalog,
    ) -> None:
        if not isinstance(catalog, WorkSchedulerCatalog):
            raise WorkSchedulerError("catalog must be WorkSchedulerCatalog")
        if not isinstance(resource_catalog, ResourceProfileCatalog):
            raise WorkSchedulerError("resource_catalog must be ResourceProfileCatalog")
        if (
            catalog.resource_catalog_id != resource_catalog.catalog_id
            or catalog.resource_catalog_digest != resource_catalog.digest
        ):
            raise WorkSchedulerError(
                "scheduler/resource catalog provenance mismatch"
            )
        self.catalog = catalog
        self.resource_catalog = resource_catalog

    @property
    def policy_id(self) -> str:
        return self.catalog.policy_id

    def create_grant(
        self,
        *,
        move_plan: MoveResourcePlan,
        owner: str,
        instance: str,
        phase: str,
        allocation_round: int,
        effective_options_digest: str,
        elapsed_ms: float,
        target_id: str | None = None,
    ) -> WorkGrant:
        if not isinstance(move_plan, MoveResourcePlan):
            raise WorkSchedulerDenied("J9 requires MoveResourcePlan")
        if move_plan.disposition != ADAPTIVE_DISPOSITION:
            raise WorkSchedulerDenied(
                "J9 compatibility WorkGrants require an ADAPTIVE parent MoveResourcePlan"
            )
        family = _family(owner)
        _nonnegative_int(allocation_round, "allocation_round")
        if allocation_round >= self.catalog.round_count:
            raise WorkSchedulerDenied("allocation round exceeds J9 compatibility schedule")
        if (
            isinstance(elapsed_ms, bool)
            or not isinstance(elapsed_ms, (int, float))
            or not math.isfinite(float(elapsed_ms))
            or float(elapsed_ms) < 0.0
        ):
            raise WorkSchedulerDenied("elapsed_ms must be finite and non-negative")
        elapsed = float(elapsed_ms)
        scheduled = self.catalog.chunk_for(
            allocation_round=allocation_round,
            family=family,
            phase=phase,
        )
        profile = self.resource_catalog.profile(scheduled.profile_id)
        try:
            bound_profile = self.resource_catalog.profile_for_instance(
                instance,
                composition_id=move_plan.composition.composition_id,
            )
        except ResourceProfileCatalogError as exc:
            raise WorkSchedulerDenied(str(exc)) from exc
        if (
            bound_profile.profile_id != profile.profile_id
            or bound_profile.digest != profile.digest
        ):
            raise WorkSchedulerDenied(
                f"{instance}: J9 chunk profile differs from parent composition"
            )
        if profile.family != family:
            raise WorkSchedulerDenied(
                "J9 grant owner differs from licensed profile family"
            )
        deadline = min(
            float(move_plan.soft_budget_ms),
            elapsed + scheduled.chunk.wall_bound_ms,
        )
        if deadline <= elapsed:
            raise WorkSchedulerDenied(
                "J9 WorkGrant would already be expired at admission"
            )
        if not isinstance(effective_options_digest, str) or len(effective_options_digest) != 64:
            raise WorkSchedulerDenied("effective_options_digest must be SHA-256")
        target = None if target_id is None else str(target_id)
        decision_digest = canonical_digest(
            {
                "decision_kind": "compat-decision-v1",
                "scheduler_policy_id": self.policy_id,
                "move_resource_plan_id": move_plan.plan_id,
                "generation": move_plan.generation,
                "position_id": move_plan.position_id,
                "allocation_round": allocation_round,
                "phase": phase,
                "owner": family,
                "instance": instance,
                "work_chunk_id": scheduled.chunk.chunk_id,
                "target_id": target,
            }
        )
        license_item = self.catalog.license_for(
            profile.profile_id,
            scheduled.chunk.chunk_id,
        )
        return WorkGrant.from_chunk(
            profile=profile,
            chunk=scheduled.chunk,
            compatibility_license=license_item,
            generation=move_plan.generation,
            position_id=move_plan.position_id,
            owner=family,
            instance=instance,
            move_resource_plan_id=move_plan.plan_id,
            scheduler_policy_id=self.policy_id,
            allocation_round=allocation_round,
            wall_deadline_ms=deadline,
            effective_options_digest=effective_options_digest,
            allocator_decision_digest=decision_digest,
        )


def build_work_scheduler(
    *,
    settings: WorkSchedulerSettings,
    root: Path,
) -> LegacyFixedWorkGrantScheduler:
    if not isinstance(settings, WorkSchedulerSettings):
        raise WorkSchedulerError("settings must be WorkSchedulerSettings")
    catalog_path = (Path(root) / settings.catalog).resolve()
    catalog, resource_catalog = load_work_scheduler_catalog(catalog_path)
    if catalog.policy_id != settings.policy:
        raise WorkSchedulerError("runtime scheduler policy differs from frozen catalog")
    if catalog.round_count != settings.round_count:
        raise WorkSchedulerError("runtime scheduler round_count differs from catalog")
    if catalog.max_grants_per_round != settings.max_grants_per_round:
        raise WorkSchedulerError(
            "runtime scheduler max_grants_per_round differs from catalog"
        )
    return LegacyFixedWorkGrantScheduler(
        catalog=catalog,
        resource_catalog=resource_catalog,
    )
