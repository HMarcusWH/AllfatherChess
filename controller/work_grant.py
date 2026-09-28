"""Typed compute contracts for M14-J.

WorkChunk describes a prequalified unit of engine-native work.  WorkGrant is the
immutable resource authorization for one concrete generation/position.  Neither
object grants outward chess-move authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    EngineResourceProfile,
    OrchestrationContractError,
    _family,
    _finite_nonnegative,
    _finite_positive,
    _mapping,
    _phase,
    _positive_int,
    _safe_id,
    _sha256,
)


class NativeLimitKind(str, Enum):
    NODES = "nodes"
    MOVETIME_MS = "movetime_ms"


_PHASE_PURPOSE = {
    "EXPLORE": "solver",
    "VERIFY": "verify",
    "STAGED_VERIFY": "verify",
    "REFINE": "refine",
}


def _limit_kind(value: Any) -> NativeLimitKind:
    if isinstance(value, NativeLimitKind):
        return value
    try:
        return NativeLimitKind(value)
    except (TypeError, ValueError) as exc:
        raise OrchestrationContractError(
            f"native limit kind must be one of "
            f"{[item.value for item in NativeLimitKind]}, got {value!r}"
        ) from exc


def _expected_semantics(family: str, kind: NativeLimitKind) -> str:
    if kind is NativeLimitKind.NODES:
        return f"{family}.uci_nodes"
    return f"{family}.uci_time"


@dataclass(frozen=True)
class NativeLimit:
    kind: NativeLimitKind
    value: int
    semantics: str

    def __post_init__(self) -> None:
        kind = _limit_kind(self.kind)
        object.__setattr__(self, "kind", kind)
        _positive_int(self.value, "native limit value")
        _safe_id(self.semantics, "native limit semantics")

    def validate_for_family(self, family: str) -> None:
        family = _family(family)
        expected = _expected_semantics(family, self.kind)
        if self.semantics != expected:
            raise OrchestrationContractError(
                f"{family} {self.kind.value} limit must use semantics "
                f"{expected!r}, got {self.semantics!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "value": self.value,
            "semantics": self.semantics,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NativeLimit":
        raw = _mapping(raw, "native limit")
        return cls(
            kind=raw.get("kind"),
            value=raw.get("value"),
            semantics=raw.get("semantics"),
        )


@dataclass(frozen=True)
class WorkChunk:
    chunk_id: str
    family: str
    phase: str
    purpose: str
    native_limit: NativeLimit
    reserved_cpu_ms: float
    reserved_gpu_ms: float
    wall_bound_ms: float
    cost_evidence_digest: str

    def __post_init__(self) -> None:
        _safe_id(self.chunk_id, "chunk_id")
        family = _family(self.family)
        phase = _phase(self.phase)
        if self.purpose != _PHASE_PURPOSE[phase]:
            raise OrchestrationContractError(
                f"{phase} work must use purpose {_PHASE_PURPOSE[phase]!r}, "
                f"got {self.purpose!r}"
            )
        if not isinstance(self.native_limit, NativeLimit):
            raise OrchestrationContractError("native_limit must be NativeLimit")
        self.native_limit.validate_for_family(family)
        cpu = _finite_nonnegative(self.reserved_cpu_ms, "reserved_cpu_ms")
        gpu = _finite_nonnegative(self.reserved_gpu_ms, "reserved_gpu_ms")
        if cpu == 0.0 and gpu == 0.0:
            raise OrchestrationContractError(
                "work chunk must reserve positive CPU or GPU capacity"
            )
        _finite_positive(self.wall_bound_ms, "wall_bound_ms")
        _sha256(self.cost_evidence_digest, "cost_evidence_digest")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "chunk_id": self.chunk_id,
            "family": self.family,
            "phase": self.phase,
            "purpose": self.purpose,
            "native_limit": self.native_limit.as_dict(),
            "reserved_cpu_ms": float(self.reserved_cpu_ms),
            "reserved_gpu_ms": float(self.reserved_gpu_ms),
            "wall_bound_ms": float(self.wall_bound_ms),
            "cost_evidence_digest": self.cost_evidence_digest,
            "authority": {
                "resource_template": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkChunk":
        raw = _mapping(raw, "work chunk")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported work chunk schema_version: {raw.get('schema_version')!r}"
            )
        authority = raw.get("authority")
        if authority is not None and authority != {
            "resource_template": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError("work chunk authority marker is invalid")
        return cls(
            chunk_id=raw.get("chunk_id"),
            family=raw.get("family"),
            phase=raw.get("phase"),
            purpose=raw.get("purpose"),
            native_limit=NativeLimit.from_dict(raw.get("native_limit", {})),
            reserved_cpu_ms=raw.get("reserved_cpu_ms"),
            reserved_gpu_ms=raw.get("reserved_gpu_ms"),
            wall_bound_ms=raw.get("wall_bound_ms"),
            cost_evidence_digest=raw.get("cost_evidence_digest"),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


@dataclass(frozen=True)
class WorkGrant:
    generation: int
    position_id: str
    owner: str
    instance: str
    profile_id: str
    profile_digest: str
    phase: str
    purpose: str
    work_chunk_id: str
    work_chunk_digest: str
    native_limit: NativeLimit
    reserved_cpu_ms: float
    reserved_gpu_ms: float
    wall_deadline_ms: float
    effective_options_digest: str
    allocator_decision_digest: str

    def __post_init__(self) -> None:
        _positive_int(self.generation, "generation")
        _safe_id(self.position_id, "position_id")
        owner = _family(self.owner, "owner")
        _safe_id(self.instance, "instance")
        _safe_id(self.profile_id, "profile_id")
        _sha256(self.profile_digest, "profile_digest")
        phase = _phase(self.phase)
        if self.purpose != _PHASE_PURPOSE[phase]:
            raise OrchestrationContractError(
                f"{phase} grant must use purpose {_PHASE_PURPOSE[phase]!r}, "
                f"got {self.purpose!r}"
            )
        _safe_id(self.work_chunk_id, "work_chunk_id")
        _sha256(self.work_chunk_digest, "work_chunk_digest")
        if not isinstance(self.native_limit, NativeLimit):
            raise OrchestrationContractError("native_limit must be NativeLimit")
        self.native_limit.validate_for_family(owner)
        cpu = _finite_nonnegative(self.reserved_cpu_ms, "reserved_cpu_ms")
        gpu = _finite_nonnegative(self.reserved_gpu_ms, "reserved_gpu_ms")
        if cpu == 0.0 and gpu == 0.0:
            raise OrchestrationContractError(
                "work grant must reserve positive CPU or GPU capacity"
            )
        _finite_positive(self.wall_deadline_ms, "wall_deadline_ms")
        _sha256(self.effective_options_digest, "effective_options_digest")
        _sha256(self.allocator_decision_digest, "allocator_decision_digest")

    def payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "generation": self.generation,
            "position_id": self.position_id,
            "owner": self.owner,
            "instance": self.instance,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "phase": self.phase,
            "purpose": self.purpose,
            "work_chunk_id": self.work_chunk_id,
            "work_chunk_digest": self.work_chunk_digest,
            "native_limit": self.native_limit.as_dict(),
            "reserved_cpu_ms": float(self.reserved_cpu_ms),
            "reserved_gpu_ms": float(self.reserved_gpu_ms),
            "wall_deadline_ms": float(self.wall_deadline_ms),
            "effective_options_digest": self.effective_options_digest,
            "allocator_decision_digest": self.allocator_decision_digest,
            "authority": {
                "resource_authorization": True,
                "outward_move": False,
            },
        }

    @property
    def grant_id(self) -> str:
        return canonical_digest(self.payload_dict())

    @property
    def digest(self) -> str:
        return self.grant_id

    def as_dict(self) -> dict[str, Any]:
        return {"grant_id": self.grant_id, **self.payload_dict()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkGrant":
        raw = _mapping(raw, "work grant")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported work grant schema_version: {raw.get('schema_version')!r}"
            )
        authority = raw.get("authority")
        if authority is not None and authority != {
            "resource_authorization": True,
            "outward_move": False,
        }:
            raise OrchestrationContractError("work grant authority marker is invalid")
        grant = cls(
            generation=raw.get("generation"),
            position_id=raw.get("position_id"),
            owner=raw.get("owner"),
            instance=raw.get("instance"),
            profile_id=raw.get("profile_id"),
            profile_digest=raw.get("profile_digest"),
            phase=raw.get("phase"),
            purpose=raw.get("purpose"),
            work_chunk_id=raw.get("work_chunk_id"),
            work_chunk_digest=raw.get("work_chunk_digest"),
            native_limit=NativeLimit.from_dict(raw.get("native_limit", {})),
            reserved_cpu_ms=raw.get("reserved_cpu_ms"),
            reserved_gpu_ms=raw.get("reserved_gpu_ms"),
            wall_deadline_ms=raw.get("wall_deadline_ms"),
            effective_options_digest=raw.get("effective_options_digest"),
            allocator_decision_digest=raw.get("allocator_decision_digest"),
        )
        claimed = raw.get("grant_id")
        if claimed is not None and claimed != grant.grant_id:
            raise OrchestrationContractError(
                "work grant grant_id does not match canonical payload"
            )
        return grant

    @classmethod
    def from_chunk(
        cls,
        *,
        profile: EngineResourceProfile,
        chunk: WorkChunk,
        generation: int,
        position_id: str,
        owner: str,
        instance: str,
        wall_deadline_ms: float,
        effective_options_digest: str,
        allocator_decision_digest: str,
    ) -> "WorkGrant":
        if not isinstance(profile, EngineResourceProfile):
            raise OrchestrationContractError("profile must be EngineResourceProfile")
        if not isinstance(chunk, WorkChunk):
            raise OrchestrationContractError("chunk must be WorkChunk")
        if owner != profile.family or chunk.family != profile.family:
            raise OrchestrationContractError(
                "work grant owner, profile family and chunk family must match"
            )
        if chunk.chunk_id not in profile.work_chunk_ids:
            raise OrchestrationContractError(
                f"profile {profile.profile_id!r} does not license chunk "
                f"{chunk.chunk_id!r}"
            )
        return cls(
            generation=generation,
            position_id=position_id,
            owner=owner,
            instance=instance,
            profile_id=profile.profile_id,
            profile_digest=profile.digest,
            phase=chunk.phase,
            purpose=chunk.purpose,
            work_chunk_id=chunk.chunk_id,
            work_chunk_digest=chunk.digest,
            native_limit=chunk.native_limit,
            reserved_cpu_ms=chunk.reserved_cpu_ms,
            reserved_gpu_ms=chunk.reserved_gpu_ms,
            wall_deadline_ms=wall_deadline_ms,
            effective_options_digest=effective_options_digest,
            allocator_decision_digest=allocator_decision_digest,
        )
