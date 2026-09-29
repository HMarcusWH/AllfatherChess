"""Immutable transient host-pressure observations for M14-J.

Pressure is intentionally separate from HostCapabilities. A load spike must not
change the qualified capacity identity of the host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from adapters.resource.linux_host import LinuxHostProvider, PsiFact, PsiLineFact
from controller.decision import canonical_digest
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _finite_nonnegative,
    _mapping,
    _nonnegative_int,
    _reject_unknown,
    _safe_id,
)


HOST_PRESSURE_VERSION = "host-pressure-v1"


def _fault_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise OrchestrationContractError("pressure faults must be an array")
    faults = tuple(value)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in faults):
        raise OrchestrationContractError("pressure faults must be non-empty strings")
    return faults


@dataclass(frozen=True)
class PressureLine:
    kind: str
    avg10: float
    avg60: float
    avg300: float
    total_us: int

    def __post_init__(self) -> None:
        if self.kind not in ("some", "full"):
            raise OrchestrationContractError("pressure kind must be some/full")
        for name in ("avg10", "avg60", "avg300"):
            value = _finite_nonnegative(getattr(self, name), name)
            object.__setattr__(self, name, value)
        _nonnegative_int(self.total_us, "pressure total_us")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "avg10": self.avg10,
            "avg60": self.avg60,
            "avg300": self.avg300,
            "total_us": self.total_us,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PressureLine":
        raw = _mapping(raw, "pressure line")
        _reject_unknown(
            raw, {"kind", "avg10", "avg60", "avg300", "total_us"}, "pressure line"
        )
        return cls(
            kind=raw.get("kind"),
            avg10=raw.get("avg10"),
            avg60=raw.get("avg60"),
            avg300=raw.get("avg300"),
            total_us=raw.get("total_us"),
        )


@dataclass(frozen=True)
class PressureSample:
    source: str
    some: PressureLine
    full: PressureLine | None

    def __post_init__(self) -> None:
        _safe_id(self.source, "pressure source")
        if not isinstance(self.some, PressureLine) or self.some.kind != "some":
            raise OrchestrationContractError("pressure sample requires a 'some' line")
        if self.full is not None and (
            not isinstance(self.full, PressureLine) or self.full.kind != "full"
        ):
            raise OrchestrationContractError("pressure full line must have kind='full'")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "some": self.some.as_dict(),
            "full": None if self.full is None else self.full.as_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PressureSample":
        raw = _mapping(raw, "pressure sample")
        _reject_unknown(raw, {"source", "some", "full"}, "pressure sample")
        full_raw = raw.get("full")
        return cls(
            source=raw.get("source"),
            some=PressureLine.from_dict(raw.get("some", {})),
            full=None if full_raw is None else PressureLine.from_dict(full_raw),
        )


def _line_from_fact(line: PsiLineFact) -> PressureLine:
    return PressureLine(
        kind=line.kind,
        avg10=line.avg10,
        avg60=line.avg60,
        avg300=line.avg300,
        total_us=line.total_us,
    )


def _sample_from_fact(source: str, fact: PsiFact | None) -> PressureSample | None:
    if fact is None:
        return None
    return PressureSample(
        source=source,
        some=_line_from_fact(fact.some),
        full=None if fact.full is None else _line_from_fact(fact.full),
    )


@dataclass(frozen=True)
class HostPressure:
    version: str
    provider_id: str
    system_cpu: PressureSample | None
    system_memory: PressureSample | None
    cgroup_cpu: PressureSample | None
    cgroup_memory: PressureSample | None
    memory_current_bytes: int | None
    system_complete: bool
    cgroup_complete: bool
    faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version != HOST_PRESSURE_VERSION:
            raise OrchestrationContractError(
                f"unsupported host pressure version: {self.version!r}"
            )
        _safe_id(self.provider_id, "pressure provider_id")
        for name in ("system_cpu", "system_memory", "cgroup_cpu", "cgroup_memory"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, PressureSample):
                raise OrchestrationContractError(f"{name} must be PressureSample or null")
        if self.memory_current_bytes is not None:
            _nonnegative_int(self.memory_current_bytes, "memory_current_bytes")
        if not isinstance(self.system_complete, bool):
            raise OrchestrationContractError("system_complete must be boolean")
        if not isinstance(self.cgroup_complete, bool):
            raise OrchestrationContractError("cgroup_complete must be boolean")
        expected_system_complete = (
            self.system_cpu is not None and self.system_memory is not None
        )
        expected_cgroup_complete = (
            self.cgroup_cpu is not None
            and self.cgroup_memory is not None
            and self.memory_current_bytes is not None
        )
        if self.system_complete != expected_system_complete:
            raise OrchestrationContractError(
                "system_complete does not match pressure observations"
            )
        if self.cgroup_complete != expected_cgroup_complete:
            raise OrchestrationContractError(
                "cgroup_complete does not match pressure observations"
            )
        object.__setattr__(self, "faults", _fault_tuple(self.faults))

    def as_dict(self) -> dict[str, Any]:
        def item(value: PressureSample | None) -> dict[str, Any] | None:
            return None if value is None else value.as_dict()

        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "version": self.version,
            "provider_id": self.provider_id,
            "system_cpu": item(self.system_cpu),
            "system_memory": item(self.system_memory),
            "cgroup_cpu": item(self.cgroup_cpu),
            "cgroup_memory": item(self.cgroup_memory),
            "memory_current_bytes": self.memory_current_bytes,
            "system_complete": self.system_complete,
            "cgroup_complete": self.cgroup_complete,
            "faults": list(self.faults),
            "authority": {
                "resource_context": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "HostPressure":
        raw = _mapping(raw, "host pressure")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported host pressure schema_version: "
                f"{raw.get('schema_version')!r}"
            )
        allowed = {
            "schema_version",
            "version",
            "provider_id",
            "system_cpu",
            "system_memory",
            "cgroup_cpu",
            "cgroup_memory",
            "memory_current_bytes",
            "system_complete",
            "cgroup_complete",
            "faults",
            "authority",
        }
        _reject_unknown(raw, allowed, "host pressure")
        if raw.get("authority") != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError("host pressure authority marker is invalid")

        def sample(name: str) -> PressureSample | None:
            value = raw.get(name)
            return None if value is None else PressureSample.from_dict(value)

        faults = raw.get("faults", [])
        if not isinstance(faults, list):
            raise OrchestrationContractError("pressure faults must be an array")
        return cls(
            version=raw.get("version"),
            provider_id=raw.get("provider_id"),
            system_cpu=sample("system_cpu"),
            system_memory=sample("system_memory"),
            cgroup_cpu=sample("cgroup_cpu"),
            cgroup_memory=sample("cgroup_memory"),
            memory_current_bytes=raw.get("memory_current_bytes"),
            system_complete=raw.get("system_complete"),
            cgroup_complete=raw.get("cgroup_complete"),
            faults=tuple(faults),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


def discover_host_pressure(
    provider: LinuxHostProvider | None = None,
) -> HostPressure:
    provider = provider or LinuxHostProvider()
    facts = provider.observe_pressure()
    system_cpu = _sample_from_fact("system/cpu", facts.system_cpu)
    system_memory = _sample_from_fact("system/memory", facts.system_memory)
    cgroup_cpu = _sample_from_fact("cgroup/cpu", facts.cgroup_cpu)
    cgroup_memory = _sample_from_fact("cgroup/memory", facts.cgroup_memory)
    return HostPressure(
        version=HOST_PRESSURE_VERSION,
        provider_id=facts.provider_id,
        system_cpu=system_cpu,
        system_memory=system_memory,
        cgroup_cpu=cgroup_cpu,
        cgroup_memory=cgroup_memory,
        memory_current_bytes=facts.memory_current_bytes,
        system_complete=system_cpu is not None and system_memory is not None,
        cgroup_complete=(
            cgroup_cpu is not None
            and cgroup_memory is not None
            and facts.memory_current_bytes is not None
        ),
        faults=facts.faults,
    )
