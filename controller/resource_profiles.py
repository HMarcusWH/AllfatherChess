"""Immutable M14-J resource-profile contracts.

J1 is deliberately schema-only.  These objects describe prequalified engine
operating points and whole-machine compositions, but they do not inspect the
host, mutate a running backend, allocate compute, or grant outward move
authority.

All claim-bearing identities use the same canonical JSON digest primitive as
the decision plane so orchestration provenance cannot silently acquire a second
serialization convention.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from controller.decision import canonical_digest


ORCHESTRATION_SCHEMA_VERSION = 1
SOLVER_FAMILIES = ("stockfish", "reckless", "lc0")
ORCHESTRATION_PHASES = ("EXPLORE", "VERIFY", "STAGED_VERIFY", "REFINE")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class OrchestrationContractError(ValueError):
    """An orchestration contract is malformed or semantically inconsistent."""


class MutationBoundary(str, Enum):
    PROCESS = "process"
    GAME = "game"
    SEARCH = "search"


class AcceleratorKind(str, Enum):
    CPU = "cpu"
    GPU = "gpu"


class CompositionRole(str, Enum):
    ANCHOR = "anchor"
    SPECIALIST = "specialist"


class EnforcementMode(str, Enum):
    OBSERVED = "observed"
    AFFINITY = "affinity"
    CGROUP_V2 = "cgroup_v2"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OrchestrationContractError(f"{label} must be an object")
    return value


def _reject_unknown(
    raw: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise OrchestrationContractError(
            f"{label} contains unsupported keys: {unknown}"
        )


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise OrchestrationContractError(
            f"{label} must match {_SAFE_ID_RE.pattern}, got {value!r}"
        )
    if "/" in value and any(part in ("", ".", "..") for part in value.split("/")):
        raise OrchestrationContractError(
            f"{label} may not contain empty, '.' or '..' path-like segments"
        )
    return value


def _instance_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _INSTANCE_RE.fullmatch(value) is None:
        raise OrchestrationContractError(
            f"{label} must match runtime instance identity {_INSTANCE_RE.pattern}, "
            f"got {value!r}"
        )
    return value


def _environment_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ENVIRONMENT_NAME_RE.fullmatch(value) is None:
        raise OrchestrationContractError(
            f"{label} must be a valid environment-variable name"
        )
    return value


def _nul_free_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise OrchestrationContractError(f"{label} must be a NUL-free string")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise OrchestrationContractError(f"{label} must be a non-empty NUL-free string")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise OrchestrationContractError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _git_oid(value: Any, label: str) -> str:
    if not isinstance(value, str) or _GIT_OID_RE.fullmatch(value) is None:
        raise OrchestrationContractError(
            f"{label} must be a lowercase 40- or 64-character Git object id"
        )
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OrchestrationContractError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OrchestrationContractError(f"{label} must be a non-negative integer")
    return value


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrchestrationContractError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise OrchestrationContractError(f"{label} must be finite and non-negative")
    return 0.0 if number == 0.0 else number


def _finite_positive(value: Any, label: str) -> float:
    number = _finite_nonnegative(value, label)
    if number <= 0.0:
        raise OrchestrationContractError(f"{label} must be positive")
    return number


def _family(value: Any, label: str = "family") -> str:
    if value not in SOLVER_FAMILIES:
        raise OrchestrationContractError(
            f"{label} must be one of {list(SOLVER_FAMILIES)}, got {value!r}"
        )
    return str(value)


def _phase(value: Any, label: str = "phase") -> str:
    if value not in ORCHESTRATION_PHASES:
        raise OrchestrationContractError(
            f"{label} must be one of {list(ORCHESTRATION_PHASES)}, got {value!r}"
        )
    return str(value)


def _enum(value: Any, cls: type[Enum], label: str) -> Enum:
    if isinstance(value, cls):
        return value
    try:
        return cls(value)
    except (TypeError, ValueError) as exc:
        allowed = [member.value for member in cls]
        raise OrchestrationContractError(
            f"{label} must be one of {allowed}, got {value!r}"
        ) from exc


def _option_scalar(value: Any, label: str) -> str | int | float | bool:
    if isinstance(value, str):
        if "\x00" in value:
            raise OrchestrationContractError(f"{label} may not contain NUL")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OrchestrationContractError(f"{label} must be finite")
        return 0.0 if value == 0.0 else value
    raise OrchestrationContractError(
        f"{label} must be a JSON scalar string/int/float/bool, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class ArtifactIdentity:
    name: str
    sha256: str

    def __post_init__(self) -> None:
        _safe_id(self.name, "artifact name")
        _sha256(self.sha256, f"artifact {self.name} sha256")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArtifactIdentity":
        raw = _mapping(raw, "artifact identity")
        _reject_unknown(raw, {"name", "sha256"}, "artifact identity")
        return cls(name=raw.get("name"), sha256=raw.get("sha256"))


@dataclass(frozen=True)
class ProcessIdentity:
    """Byte/config identity that may only change at process construction."""

    binary_sha256: str
    artifacts: tuple[ArtifactIdentity, ...] = ()
    backend: str | None = None
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _sha256(self.binary_sha256, "binary_sha256")

        artifacts = tuple(self.artifacts)
        if not all(isinstance(item, ArtifactIdentity) for item in artifacts):
            raise OrchestrationContractError("artifacts must contain ArtifactIdentity values")
        names = [item.name for item in artifacts]
        if len(names) != len(set(names)):
            raise OrchestrationContractError("artifact names must be unique")
        object.__setattr__(self, "artifacts", tuple(sorted(artifacts, key=lambda item: item.name)))

        if self.backend is not None:
            _safe_id(self.backend, "backend")

        args = tuple(self.args)
        for index, value in enumerate(args):
            _nonempty_string(value, f"args[{index}]")
        object.__setattr__(self, "args", args)

        environment = tuple(self.environment)
        names = []
        normalized: list[tuple[str, str]] = []
        for index, item in enumerate(environment):
            if not isinstance(item, tuple) or len(item) != 2:
                raise OrchestrationContractError(
                    f"environment[{index}] must be a (name, value) tuple"
                )
            name, value = item
            _environment_name(name, f"environment[{index}] name")
            _nul_free_string(value, f"environment[{index}] value")
            names.append(name)
            normalized.append((name, value))
        if len(names) != len(set(names)):
            raise OrchestrationContractError("environment variable names must be unique")
        object.__setattr__(self, "environment", tuple(sorted(normalized)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "binary_sha256": self.binary_sha256,
            "artifacts": [item.as_dict() for item in self.artifacts],
            "backend": self.backend,
            "args": list(self.args),
            "environment": {name: value for name, value in self.environment},
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProcessIdentity":
        raw = _mapping(raw, "process identity")
        _reject_unknown(
            raw,
            {"binary_sha256", "artifacts", "backend", "args", "environment"},
            "process identity",
        )
        artifacts_raw = raw.get("artifacts", [])
        if not isinstance(artifacts_raw, list):
            raise OrchestrationContractError("process identity artifacts must be an array")
        env_raw = raw.get("environment", {})
        env_raw = _mapping(env_raw, "process identity environment")
        args_raw = raw.get("args", [])
        if not isinstance(args_raw, list):
            raise OrchestrationContractError("process identity args must be an array")
        return cls(
            binary_sha256=raw.get("binary_sha256"),
            artifacts=tuple(ArtifactIdentity.from_dict(item) for item in artifacts_raw),
            backend=raw.get("backend"),
            args=tuple(args_raw),
            environment=tuple(env_raw.items()),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


@dataclass(frozen=True)
class QualificationIdentity:
    """Evidence identity that licenses one frozen operating point."""

    source_commit: str
    evidence_sha256: str
    evidence_id: str
    host_domain: str

    def __post_init__(self) -> None:
        _git_oid(self.source_commit, "source_commit")
        _sha256(self.evidence_sha256, "evidence_sha256")
        _safe_id(self.evidence_id, "evidence_id")
        _safe_id(self.host_domain, "host_domain")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_commit": self.source_commit,
            "evidence_sha256": self.evidence_sha256,
            "evidence_id": self.evidence_id,
            "host_domain": self.host_domain,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "QualificationIdentity":
        raw = _mapping(raw, "qualification identity")
        _reject_unknown(
            raw,
            {"source_commit", "evidence_sha256", "evidence_id", "host_domain"},
            "qualification identity",
        )
        return cls(
            source_commit=raw.get("source_commit"),
            evidence_sha256=raw.get("evidence_sha256"),
            evidence_id=raw.get("evidence_id"),
            host_domain=raw.get("host_domain"),
        )


@dataclass(frozen=True)
class ProfileOption:
    """One option plus the boundary at which it may legally change."""

    name: str
    value: str | int | float | bool
    boundary: MutationBoundary
    phase: str | None = None

    def __post_init__(self) -> None:
        name = _nonempty_string(self.name, "option name")
        if name != name.strip():
            raise OrchestrationContractError("option name may not have edge whitespace")
        if any(ch in name for ch in ("\r", "\n", "\t")):
            raise OrchestrationContractError("option name may not contain control whitespace")
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "value",
            _option_scalar(self.value, f"option {self.name} value"),
        )
        boundary = _enum(self.boundary, MutationBoundary, "mutation boundary")
        object.__setattr__(self, "boundary", boundary)

        if boundary is MutationBoundary.SEARCH:
            _phase(self.phase, f"search option {self.name} phase")
        elif self.phase is not None:
            raise OrchestrationContractError(
                f"{boundary.value} option {self.name} may not declare a search phase"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "boundary": self.boundary.value,
            "phase": self.phase,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProfileOption":
        raw = _mapping(raw, "profile option")
        _reject_unknown(
            raw, {"name", "value", "boundary", "phase"}, "profile option"
        )
        return cls(
            name=raw.get("name"),
            value=raw.get("value"),
            boundary=raw.get("boundary"),
            phase=raw.get("phase"),
        )


@dataclass(frozen=True)
class EngineResourceProfile:
    """A single prequalified operating point for one solver family."""

    profile_id: str
    family: str
    process_identity: ProcessIdentity
    options: tuple[ProfileOption, ...]
    cpu_slots: int
    expected_memory_mib: int
    accelerator: AcceleratorKind
    accelerator_memory_mib: int
    work_chunk_ids: tuple[str, ...]
    qualification: QualificationIdentity

    def __post_init__(self) -> None:
        _safe_id(self.profile_id, "profile_id")
        _family(self.family)
        if not isinstance(self.process_identity, ProcessIdentity):
            raise OrchestrationContractError("process_identity must be ProcessIdentity")
        if not isinstance(self.qualification, QualificationIdentity):
            raise OrchestrationContractError("qualification must be QualificationIdentity")
        _positive_int(self.cpu_slots, "cpu_slots")
        _positive_int(self.expected_memory_mib, "expected_memory_mib")

        accelerator = _enum(self.accelerator, AcceleratorKind, "accelerator")
        object.__setattr__(self, "accelerator", accelerator)
        _nonnegative_int(self.accelerator_memory_mib, "accelerator_memory_mib")
        if accelerator is AcceleratorKind.CPU and self.accelerator_memory_mib != 0:
            raise OrchestrationContractError(
                "CPU profiles may not claim accelerator memory"
            )

        options = tuple(self.options)
        if not all(isinstance(item, ProfileOption) for item in options):
            raise OrchestrationContractError("options must contain ProfileOption values")
        keys: set[tuple[str, str, str | None]] = set()
        boundary_by_name: dict[str, MutationBoundary] = {}
        for item in options:
            key = (item.name, item.boundary.value, item.phase)
            if key in keys:
                raise OrchestrationContractError(
                    f"duplicate option declaration for {item.name!r} at {item.phase!r}"
                )
            keys.add(key)
            previous = boundary_by_name.setdefault(item.name, item.boundary)
            if previous is not item.boundary:
                raise OrchestrationContractError(
                    f"option {item.name!r} crosses mutation boundaries "
                    f"({previous.value} vs {item.boundary.value})"
                )
        object.__setattr__(
            self,
            "options",
            tuple(
                sorted(
                    options,
                    key=lambda item: (
                        item.boundary.value,
                        "" if item.phase is None else item.phase,
                        item.name,
                    ),
                )
            ),
        )

        work_chunk_ids = tuple(self.work_chunk_ids)
        for index, chunk_id in enumerate(work_chunk_ids):
            _safe_id(chunk_id, f"work_chunk_ids[{index}]")
        if len(work_chunk_ids) != len(set(work_chunk_ids)):
            raise OrchestrationContractError("work_chunk_ids must be unique")
        object.__setattr__(self, "work_chunk_ids", tuple(sorted(work_chunk_ids)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "family": self.family,
            "process_identity": self.process_identity.as_dict(),
            "options": [item.as_dict() for item in self.options],
            "cpu_slots": self.cpu_slots,
            "expected_memory_mib": self.expected_memory_mib,
            "accelerator": self.accelerator.value,
            "accelerator_memory_mib": self.accelerator_memory_mib,
            "work_chunk_ids": list(self.work_chunk_ids),
            "qualification": self.qualification.as_dict(),
            "authority": {
                "resource_profile": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EngineResourceProfile":
        raw = _mapping(raw, "engine resource profile")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported resource profile schema_version: {raw.get('schema_version')!r}"
            )
        _reject_unknown(
            raw,
            {
                "schema_version",
                "profile_id",
                "family",
                "process_identity",
                "options",
                "cpu_slots",
                "expected_memory_mib",
                "accelerator",
                "accelerator_memory_mib",
                "work_chunk_ids",
                "qualification",
                "authority",
            },
            "engine resource profile",
        )
        authority = raw.get("authority")
        if authority != {
            "resource_profile": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError("resource profile authority marker is invalid")
        options_raw = raw.get("options", [])
        if not isinstance(options_raw, list):
            raise OrchestrationContractError("resource profile options must be an array")
        chunks_raw = raw.get("work_chunk_ids", [])
        if not isinstance(chunks_raw, list):
            raise OrchestrationContractError("work_chunk_ids must be an array")
        return cls(
            profile_id=raw.get("profile_id"),
            family=raw.get("family"),
            process_identity=ProcessIdentity.from_dict(raw.get("process_identity", {})),
            options=tuple(ProfileOption.from_dict(item) for item in options_raw),
            cpu_slots=raw.get("cpu_slots"),
            expected_memory_mib=raw.get("expected_memory_mib"),
            accelerator=raw.get("accelerator"),
            accelerator_memory_mib=raw.get("accelerator_memory_mib"),
            work_chunk_ids=tuple(chunks_raw),
            qualification=QualificationIdentity.from_dict(raw.get("qualification", {})),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


@dataclass(frozen=True)
class CompositionBinding:
    instance: str
    role: CompositionRole
    family: str
    profile_id: str
    cpu_slots: int
    concurrency_group: str

    def __post_init__(self) -> None:
        _instance_id(self.instance, "composition instance")
        role = _enum(self.role, CompositionRole, "composition role")
        object.__setattr__(self, "role", role)
        _family(self.family)
        _safe_id(self.profile_id, "composition profile_id")
        _positive_int(self.cpu_slots, "composition binding cpu_slots")
        _safe_id(self.concurrency_group, "concurrency_group")

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "role": self.role.value,
            "family": self.family,
            "profile_id": self.profile_id,
            "cpu_slots": self.cpu_slots,
            "concurrency_group": self.concurrency_group,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CompositionBinding":
        raw = _mapping(raw, "composition binding")
        _reject_unknown(
            raw,
            {
                "instance",
                "role",
                "family",
                "profile_id",
                "cpu_slots",
                "concurrency_group",
            },
            "composition binding",
        )
        return cls(
            instance=raw.get("instance"),
            role=raw.get("role"),
            family=raw.get("family"),
            profile_id=raw.get("profile_id"),
            cpu_slots=raw.get("cpu_slots"),
            concurrency_group=raw.get("concurrency_group"),
        )


@dataclass(frozen=True)
class CompositionProfile:
    """A host-level layout of engine profiles.

    CPU capacity is checked per concurrency group so later profiles can express
    mutually exclusive schedules without pretending every resident process runs
    simultaneously. Memory remains resident and is therefore checked across all
    bindings.
    """

    composition_id: str
    declared_cpu_slots: int
    expected_memory_mib: int
    enforcement_required: EnforcementMode
    bindings: tuple[CompositionBinding, ...]
    qualification: QualificationIdentity

    def __post_init__(self) -> None:
        _safe_id(self.composition_id, "composition_id")
        _positive_int(self.declared_cpu_slots, "declared_cpu_slots")
        _positive_int(self.expected_memory_mib, "composition expected_memory_mib")
        enforcement = _enum(
            self.enforcement_required, EnforcementMode, "enforcement_required"
        )
        object.__setattr__(self, "enforcement_required", enforcement)
        if not isinstance(self.qualification, QualificationIdentity):
            raise OrchestrationContractError("qualification must be QualificationIdentity")

        bindings = tuple(self.bindings)
        if not bindings or not all(isinstance(item, CompositionBinding) for item in bindings):
            raise OrchestrationContractError(
                "composition bindings must contain at least one CompositionBinding"
            )
        instances = [item.instance for item in bindings]
        if len(instances) != len(set(instances)):
            raise OrchestrationContractError("composition instance names must be unique")
        anchors = [item for item in bindings if item.role is CompositionRole.ANCHOR]
        if len(anchors) != 1:
            raise OrchestrationContractError("composition must contain exactly one anchor")

        by_group: dict[str, int] = {}
        for item in bindings:
            by_group[item.concurrency_group] = (
                by_group.get(item.concurrency_group, 0) + item.cpu_slots
            )
        for group, slots in by_group.items():
            if slots > self.declared_cpu_slots:
                raise OrchestrationContractError(
                    f"concurrency group {group!r} claims {slots} CPU slots "
                    f"but composition declares {self.declared_cpu_slots}"
                )

        object.__setattr__(
            self,
            "bindings",
            tuple(sorted(bindings, key=lambda item: item.instance)),
        )

    def validate_against(
        self, profiles: Mapping[str, EngineResourceProfile]
    ) -> None:
        """Cross-check referenced profile identity, family, slots and memory."""

        resident_memory = 0
        for binding in self.bindings:
            profile = profiles.get(binding.profile_id)
            if profile is None:
                raise OrchestrationContractError(
                    f"composition references unknown profile {binding.profile_id!r}"
                )
            if not isinstance(profile, EngineResourceProfile):
                raise OrchestrationContractError(
                    f"profile map entry {binding.profile_id!r} is not EngineResourceProfile"
                )
            if profile.family != binding.family:
                raise OrchestrationContractError(
                    f"binding {binding.instance!r} family {binding.family!r} "
                    f"does not match profile family {profile.family!r}"
                )
            if profile.cpu_slots != binding.cpu_slots:
                raise OrchestrationContractError(
                    f"binding {binding.instance!r} CPU slots {binding.cpu_slots} "
                    f"do not match profile requirement {profile.cpu_slots}"
                )
            if profile.cpu_slots > self.declared_cpu_slots:
                raise OrchestrationContractError(
                    f"profile {profile.profile_id!r} claims more CPU slots than composition"
                )
            resident_memory += profile.expected_memory_mib

        if resident_memory > self.expected_memory_mib:
            raise OrchestrationContractError(
                f"resident profile memory {resident_memory} MiB exceeds composition "
                f"declaration {self.expected_memory_mib} MiB"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "composition_id": self.composition_id,
            "declared_cpu_slots": self.declared_cpu_slots,
            "expected_memory_mib": self.expected_memory_mib,
            "enforcement_required": self.enforcement_required.value,
            "bindings": [item.as_dict() for item in self.bindings],
            "qualification": self.qualification.as_dict(),
            "authority": {
                "resource_profile": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CompositionProfile":
        raw = _mapping(raw, "composition profile")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported composition schema_version: {raw.get('schema_version')!r}"
            )
        _reject_unknown(
            raw,
            {
                "schema_version",
                "composition_id",
                "declared_cpu_slots",
                "expected_memory_mib",
                "enforcement_required",
                "bindings",
                "qualification",
                "authority",
            },
            "composition profile",
        )
        authority = raw.get("authority")
        if authority != {
            "resource_profile": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError("composition authority marker is invalid")
        bindings_raw = raw.get("bindings", [])
        if not isinstance(bindings_raw, list):
            raise OrchestrationContractError("composition bindings must be an array")
        return cls(
            composition_id=raw.get("composition_id"),
            declared_cpu_slots=raw.get("declared_cpu_slots"),
            expected_memory_mib=raw.get("expected_memory_mib"),
            enforcement_required=raw.get("enforcement_required"),
            bindings=tuple(CompositionBinding.from_dict(item) for item in bindings_raw),
            qualification=QualificationIdentity.from_dict(raw.get("qualification", {})),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())
