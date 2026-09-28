"""Replay-safe M14-J orchestration provenance contracts.

J1 only defines immutable evidence objects.  Runtime production/verification of
these records is introduced by later milestones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.resource_profiles import (
    MutationBoundary,
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _enum,
    _mapping,
    _positive_int,
    _reject_unknown,
    _safe_id,
    _sha256,
)


ORCHESTRATION_EVIDENCE_VERSION = "orchestration-evidence-v1"


def _faults(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise OrchestrationContractError(f"{label} must be an array")
    result = tuple(value)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in result):
        raise OrchestrationContractError(
            f"{label} must contain non-empty NUL-free strings"
        )
    return result


@dataclass(frozen=True)
class ProfileApplicationEvidence:
    instance: str
    profile_id: str
    profile_digest: str
    boundary: MutationBoundary
    effective_options_digest: str
    resource_state_digest: str
    success: bool
    faults: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_id(self.instance, "profile application instance")
        _safe_id(self.profile_id, "profile application profile_id")
        _sha256(self.profile_digest, "profile application profile_digest")
        boundary = _enum(self.boundary, MutationBoundary, "profile application boundary")
        object.__setattr__(self, "boundary", boundary)
        _sha256(
            self.effective_options_digest,
            "profile application effective_options_digest",
        )
        _sha256(self.resource_state_digest, "profile application resource_state_digest")
        if not isinstance(self.success, bool):
            raise OrchestrationContractError("profile application success must be boolean")
        faults = _faults(self.faults, "profile application faults")
        object.__setattr__(self, "faults", faults)
        if self.success and faults:
            raise OrchestrationContractError(
                "successful profile application may not carry faults"
            )
        if not self.success and not faults:
            raise OrchestrationContractError(
                "failed profile application must retain at least one fault"
            )

    def payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "instance": self.instance,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "boundary": self.boundary.value,
            "effective_options_digest": self.effective_options_digest,
            "resource_state_digest": self.resource_state_digest,
            "success": self.success,
            "faults": list(self.faults),
            "authority": {
                "resource_evidence": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @property
    def application_id(self) -> str:
        return canonical_digest(self.payload_dict())

    @property
    def digest(self) -> str:
        return self.application_id

    def as_dict(self) -> dict[str, Any]:
        return {"application_id": self.application_id, **self.payload_dict()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProfileApplicationEvidence":
        raw = _mapping(raw, "profile application evidence")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported profile application schema_version: "
                f"{raw.get('schema_version')!r}"
            )
        _reject_unknown(
            raw,
            {
                "application_id",
                "schema_version",
                "instance",
                "profile_id",
                "profile_digest",
                "boundary",
                "effective_options_digest",
                "resource_state_digest",
                "success",
                "faults",
                "authority",
            },
            "profile application evidence",
        )
        authority = raw.get("authority")
        if authority != {
            "resource_evidence": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError(
                "profile application authority marker is invalid"
            )
        item = cls(
            instance=raw.get("instance"),
            profile_id=raw.get("profile_id"),
            profile_digest=raw.get("profile_digest"),
            boundary=raw.get("boundary"),
            effective_options_digest=raw.get("effective_options_digest"),
            resource_state_digest=raw.get("resource_state_digest"),
            success=raw.get("success"),
            faults=_faults(raw.get("faults", []), "profile application faults"),
        )
        claimed = raw.get("application_id")
        if claimed != item.application_id:
            raise OrchestrationContractError(
                "profile application application_id does not match canonical payload"
            )
        return item


@dataclass(frozen=True)
class OrchestrationEvidence:
    evidence_version: str
    run_id: str
    generation: int
    position_id: str
    game_environment_digest: str
    composition_profile_digest: str
    profile_catalog_digest: str
    application_ids: tuple[str, ...]
    grant_ids: tuple[str, ...]
    faults: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.evidence_version != ORCHESTRATION_EVIDENCE_VERSION:
            raise OrchestrationContractError(
                f"unsupported orchestration evidence version: "
                f"{self.evidence_version!r}"
            )
        _safe_id(self.run_id, "orchestration run_id")
        _positive_int(self.generation, "orchestration generation")
        _safe_id(self.position_id, "orchestration position_id")
        _sha256(self.game_environment_digest, "game_environment_digest")
        _sha256(self.composition_profile_digest, "composition_profile_digest")
        _sha256(self.profile_catalog_digest, "profile_catalog_digest")

        applications = tuple(self.application_ids)
        for index, value in enumerate(applications):
            _sha256(value, f"application_ids[{index}]")
        if len(applications) != len(set(applications)):
            raise OrchestrationContractError("application_ids must be unique")
        object.__setattr__(self, "application_ids", applications)

        grants = tuple(self.grant_ids)
        for index, value in enumerate(grants):
            _sha256(value, f"grant_ids[{index}]")
        if len(grants) != len(set(grants)):
            raise OrchestrationContractError("grant_ids must be unique")
        object.__setattr__(self, "grant_ids", grants)

        object.__setattr__(self, "faults", _faults(self.faults, "orchestration faults"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "evidence_version": self.evidence_version,
            "run_id": self.run_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "game_environment_digest": self.game_environment_digest,
            "composition_profile_digest": self.composition_profile_digest,
            "profile_catalog_digest": self.profile_catalog_digest,
            "application_ids": list(self.application_ids),
            "grant_ids": list(self.grant_ids),
            "faults": list(self.faults),
            "authority": {
                "resource_evidence": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OrchestrationEvidence":
        raw = _mapping(raw, "orchestration evidence")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported orchestration evidence schema_version: "
                f"{raw.get('schema_version')!r}"
            )
        _reject_unknown(
            raw,
            {
                "schema_version",
                "evidence_version",
                "run_id",
                "generation",
                "position_id",
                "game_environment_digest",
                "composition_profile_digest",
                "profile_catalog_digest",
                "application_ids",
                "grant_ids",
                "faults",
                "authority",
            },
            "orchestration evidence",
        )
        authority = raw.get("authority")
        if authority != {
            "resource_evidence": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError(
                "orchestration evidence authority marker is invalid"
            )
        applications = raw.get("application_ids", [])
        grants = raw.get("grant_ids", [])
        if not isinstance(applications, list) or not isinstance(grants, list):
            raise OrchestrationContractError(
                "application_ids and grant_ids must be arrays"
            )
        return cls(
            evidence_version=raw.get("evidence_version"),
            run_id=raw.get("run_id"),
            generation=raw.get("generation"),
            position_id=raw.get("position_id"),
            game_environment_digest=raw.get("game_environment_digest"),
            composition_profile_digest=raw.get("composition_profile_digest"),
            profile_catalog_digest=raw.get("profile_catalog_digest"),
            application_ids=tuple(applications),
            grant_ids=tuple(grants),
            faults=_faults(raw.get("faults", []), "orchestration faults"),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())
