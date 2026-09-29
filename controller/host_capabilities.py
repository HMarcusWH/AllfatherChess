"""Immutable M14-J host-capability contracts and discovery.

J2 observes capacity only. These facts carry no profile-selection, resource
allocation, process-placement, or outward-move authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from adapters.resource.linux_host import LinuxHostFacts, LinuxHostProvider
from controller.decision import canonical_digest
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _finite_nonnegative,
    _mapping,
    _nonnegative_int,
    _positive_int,
    _reject_unknown,
    _safe_id,
)


HOST_CAPABILITIES_VERSION = "host-capabilities-v1"
_LIMIT_STATES = ("limited", "unlimited", "unknown")


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, label)


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, label)


def _optional_positive_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    number = _finite_nonnegative(value, label)
    if number <= 0:
        raise OrchestrationContractError(f"{label} must be positive")
    return number


def _cpu_tuple(value: Any, label: str) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise OrchestrationContractError(f"{label} must be an array or null")
    cpus = tuple(value)
    if (
        any(isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0 for cpu in cpus)
        or len(cpus) != len(set(cpus))
        or tuple(sorted(cpus)) != cpus
    ):
        raise OrchestrationContractError(
            f"{label} must contain sorted unique non-negative CPU ids"
        )
    if not cpus:
        raise OrchestrationContractError(f"{label} may not be empty")
    return cpus


def _fault_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise OrchestrationContractError(f"{label} must be an array")
    faults = tuple(value)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in faults):
        raise OrchestrationContractError(f"{label} must contain non-empty strings")
    return faults


@dataclass(frozen=True)
class CpuQuotaObservation:
    cgroup_path: str
    quota_us: int | None
    period_us: int
    equivalent_cpus: float | None

    def __post_init__(self) -> None:
        _safe_id(self.cgroup_path, "cpu quota cgroup_path")
        _positive_int(self.period_us, "cpu quota period_us")
        if self.quota_us is None:
            if self.equivalent_cpus is not None:
                raise OrchestrationContractError(
                    "unlimited cpu quota may not carry equivalent_cpus"
                )
        else:
            _positive_int(self.quota_us, "cpu quota quota_us")
            equivalent = _optional_positive_float(
                self.equivalent_cpus, "cpu quota equivalent_cpus"
            )
            expected = self.quota_us / self.period_us
            if equivalent is None or not math.isclose(
                equivalent, expected, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise OrchestrationContractError(
                    "cpu quota equivalent_cpus does not match quota/period"
                )
            object.__setattr__(self, "equivalent_cpus", equivalent)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cgroup_path": self.cgroup_path,
            "quota_us": self.quota_us,
            "period_us": self.period_us,
            "equivalent_cpus": self.equivalent_cpus,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CpuQuotaObservation":
        raw = _mapping(raw, "cpu quota observation")
        _reject_unknown(
            raw,
            {"cgroup_path", "quota_us", "period_us", "equivalent_cpus"},
            "cpu quota observation",
        )
        return cls(
            cgroup_path=raw.get("cgroup_path"),
            quota_us=raw.get("quota_us"),
            period_us=raw.get("period_us"),
            equivalent_cpus=raw.get("equivalent_cpus"),
        )


@dataclass(frozen=True)
class MemoryLimitObservation:
    cgroup_path: str
    status: str
    limit_bytes: int | None

    def __post_init__(self) -> None:
        _safe_id(self.cgroup_path, "memory limit cgroup_path")
        if self.status not in ("limited", "unlimited"):
            raise OrchestrationContractError(
                "memory limit observation status must be limited/unlimited"
            )
        if self.status == "limited":
            if self.limit_bytes is None:
                raise OrchestrationContractError(
                    "limited memory observation requires limit_bytes"
                )
            _nonnegative_int(self.limit_bytes, "memory limit bytes")
        elif self.limit_bytes is not None:
            raise OrchestrationContractError(
                "unlimited memory observation may not carry limit_bytes"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "cgroup_path": self.cgroup_path,
            "status": self.status,
            "limit_bytes": self.limit_bytes,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MemoryLimitObservation":
        raw = _mapping(raw, "memory limit observation")
        _reject_unknown(
            raw, {"cgroup_path", "status", "limit_bytes"}, "memory limit observation"
        )
        return cls(
            cgroup_path=raw.get("cgroup_path"),
            status=raw.get("status"),
            limit_bytes=raw.get("limit_bytes"),
        )


@dataclass(frozen=True)
class HostCapabilities:
    version: str
    provider_id: str
    platform: str
    architecture: str
    os_visible_logical_cpus: int | None
    affinity_cpus: tuple[int, ...] | None
    cgroup_cpuset_effective: tuple[int, ...] | None
    allowed_cpus: tuple[int, ...] | None
    cpu_quota_status: str
    cpu_quota_equivalents: float | None
    cpu_quota_observations: tuple[CpuQuotaObservation, ...]
    physical_core_count: int | None
    smt_width: int | None
    topology_complete: bool
    physical_memory_bytes: int | None
    cgroup_memory_status: str
    cgroup_memory_limit_bytes: int | None
    effective_memory_limit_bytes: int | None
    memory_limit_observations: tuple[MemoryLimitObservation, ...]
    accelerator_detection_complete: bool
    accelerators: tuple[str, ...]
    capacity_complete: bool
    faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version != HOST_CAPABILITIES_VERSION:
            raise OrchestrationContractError(
                f"unsupported host capabilities version: {self.version!r}"
            )
        _safe_id(self.provider_id, "host provider_id")
        _safe_id(self.platform, "host platform")
        _safe_id(self.architecture, "host architecture")
        object.__setattr__(
            self,
            "os_visible_logical_cpus",
            _optional_positive_int(
                self.os_visible_logical_cpus, "os_visible_logical_cpus"
            ),
        )
        for name in ("affinity_cpus", "cgroup_cpuset_effective", "allowed_cpus"):
            object.__setattr__(self, name, _cpu_tuple(getattr(self, name), name))

        if self.cpu_quota_status not in _LIMIT_STATES:
            raise OrchestrationContractError("invalid cpu_quota_status")
        cpu_equiv = _optional_positive_float(
            self.cpu_quota_equivalents, "cpu_quota_equivalents"
        )
        if self.cpu_quota_status == "limited" and cpu_equiv is None:
            raise OrchestrationContractError(
                "limited cpu quota requires cpu_quota_equivalents"
            )
        if self.cpu_quota_status != "limited" and cpu_equiv is not None:
            raise OrchestrationContractError(
                "non-limited cpu quota may not carry cpu_quota_equivalents"
            )
        object.__setattr__(self, "cpu_quota_equivalents", cpu_equiv)

        quotas = tuple(self.cpu_quota_observations)
        if not all(isinstance(item, CpuQuotaObservation) for item in quotas):
            raise OrchestrationContractError(
                "cpu_quota_observations must contain CpuQuotaObservation values"
            )
        object.__setattr__(self, "cpu_quota_observations", quotas)

        object.__setattr__(
            self,
            "physical_core_count",
            _optional_positive_int(self.physical_core_count, "physical_core_count"),
        )
        object.__setattr__(
            self,
            "smt_width",
            _optional_positive_int(self.smt_width, "smt_width"),
        )
        if not isinstance(self.topology_complete, bool):
            raise OrchestrationContractError("topology_complete must be boolean")
        if self.topology_complete and (
            self.physical_core_count is None or self.smt_width is None
        ):
            raise OrchestrationContractError(
                "complete topology requires physical_core_count and smt_width"
            )

        physical_memory = _optional_positive_int(
            self.physical_memory_bytes, "physical_memory_bytes"
        )
        object.__setattr__(self, "physical_memory_bytes", physical_memory)

        if self.cgroup_memory_status not in _LIMIT_STATES:
            raise OrchestrationContractError("invalid cgroup_memory_status")
        cgroup_memory = _optional_nonnegative_int(
            self.cgroup_memory_limit_bytes, "cgroup_memory_limit_bytes"
        )
        if self.cgroup_memory_status == "limited" and cgroup_memory is None:
            raise OrchestrationContractError(
                "limited cgroup memory requires cgroup_memory_limit_bytes"
            )
        if self.cgroup_memory_status != "limited" and cgroup_memory is not None:
            raise OrchestrationContractError(
                "non-limited cgroup memory may not carry a limit"
            )
        object.__setattr__(self, "cgroup_memory_limit_bytes", cgroup_memory)
        effective_memory = _optional_nonnegative_int(
            self.effective_memory_limit_bytes, "effective_memory_limit_bytes"
        )
        object.__setattr__(self, "effective_memory_limit_bytes", effective_memory)

        memory_observations = tuple(self.memory_limit_observations)
        if not all(
            isinstance(item, MemoryLimitObservation) for item in memory_observations
        ):
            raise OrchestrationContractError(
                "memory_limit_observations must contain MemoryLimitObservation values"
            )
        object.__setattr__(
            self, "memory_limit_observations", memory_observations
        )

        if not isinstance(self.accelerator_detection_complete, bool):
            raise OrchestrationContractError(
                "accelerator_detection_complete must be boolean"
            )
        accelerators = tuple(self.accelerators)
        for index, value in enumerate(accelerators):
            _safe_id(value, f"accelerators[{index}]")
        if len(accelerators) != len(set(accelerators)):
            raise OrchestrationContractError("accelerators must be unique")
        object.__setattr__(self, "accelerators", tuple(sorted(accelerators)))

        if not isinstance(self.capacity_complete, bool):
            raise OrchestrationContractError("capacity_complete must be boolean")
        object.__setattr__(self, "faults", _fault_tuple(self.faults, "host faults"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "version": self.version,
            "provider_id": self.provider_id,
            "platform": self.platform,
            "architecture": self.architecture,
            "os_visible_logical_cpus": self.os_visible_logical_cpus,
            "affinity_cpus": None if self.affinity_cpus is None else list(self.affinity_cpus),
            "cgroup_cpuset_effective": (
                None
                if self.cgroup_cpuset_effective is None
                else list(self.cgroup_cpuset_effective)
            ),
            "allowed_cpus": None if self.allowed_cpus is None else list(self.allowed_cpus),
            "cpu_quota_status": self.cpu_quota_status,
            "cpu_quota_equivalents": self.cpu_quota_equivalents,
            "cpu_quota_observations": [
                item.as_dict() for item in self.cpu_quota_observations
            ],
            "physical_core_count": self.physical_core_count,
            "smt_width": self.smt_width,
            "topology_complete": self.topology_complete,
            "physical_memory_bytes": self.physical_memory_bytes,
            "cgroup_memory_status": self.cgroup_memory_status,
            "cgroup_memory_limit_bytes": self.cgroup_memory_limit_bytes,
            "effective_memory_limit_bytes": self.effective_memory_limit_bytes,
            "memory_limit_observations": [
                item.as_dict() for item in self.memory_limit_observations
            ],
            "accelerator_detection_complete": self.accelerator_detection_complete,
            "accelerators": list(self.accelerators),
            "capacity_complete": self.capacity_complete,
            "faults": list(self.faults),
            "authority": {
                "resource_context": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "HostCapabilities":
        raw = _mapping(raw, "host capabilities")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported host capabilities schema_version: "
                f"{raw.get('schema_version')!r}"
            )
        allowed = {
            "schema_version",
            "version",
            "provider_id",
            "platform",
            "architecture",
            "os_visible_logical_cpus",
            "affinity_cpus",
            "cgroup_cpuset_effective",
            "allowed_cpus",
            "cpu_quota_status",
            "cpu_quota_equivalents",
            "cpu_quota_observations",
            "physical_core_count",
            "smt_width",
            "topology_complete",
            "physical_memory_bytes",
            "cgroup_memory_status",
            "cgroup_memory_limit_bytes",
            "effective_memory_limit_bytes",
            "memory_limit_observations",
            "accelerator_detection_complete",
            "accelerators",
            "capacity_complete",
            "faults",
            "authority",
        }
        _reject_unknown(raw, allowed, "host capabilities")
        if raw.get("authority") != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError("host capabilities authority marker is invalid")
        quotas = raw.get("cpu_quota_observations", [])
        mems = raw.get("memory_limit_observations", [])
        if not isinstance(quotas, list) or not isinstance(mems, list):
            raise OrchestrationContractError("host observation lists must be arrays")
        accelerators = raw.get("accelerators", [])
        faults = raw.get("faults", [])
        if not isinstance(accelerators, list) or not isinstance(faults, list):
            raise OrchestrationContractError("accelerators/faults must be arrays")
        return cls(
            version=raw.get("version"),
            provider_id=raw.get("provider_id"),
            platform=raw.get("platform"),
            architecture=raw.get("architecture"),
            os_visible_logical_cpus=raw.get("os_visible_logical_cpus"),
            affinity_cpus=None
            if raw.get("affinity_cpus") is None
            else tuple(raw["affinity_cpus"]),
            cgroup_cpuset_effective=None
            if raw.get("cgroup_cpuset_effective") is None
            else tuple(raw["cgroup_cpuset_effective"]),
            allowed_cpus=None
            if raw.get("allowed_cpus") is None
            else tuple(raw["allowed_cpus"]),
            cpu_quota_status=raw.get("cpu_quota_status"),
            cpu_quota_equivalents=raw.get("cpu_quota_equivalents"),
            cpu_quota_observations=tuple(
                CpuQuotaObservation.from_dict(item) for item in quotas
            ),
            physical_core_count=raw.get("physical_core_count"),
            smt_width=raw.get("smt_width"),
            topology_complete=raw.get("topology_complete"),
            physical_memory_bytes=raw.get("physical_memory_bytes"),
            cgroup_memory_status=raw.get("cgroup_memory_status"),
            cgroup_memory_limit_bytes=raw.get("cgroup_memory_limit_bytes"),
            effective_memory_limit_bytes=raw.get("effective_memory_limit_bytes"),
            memory_limit_observations=tuple(
                MemoryLimitObservation.from_dict(item) for item in mems
            ),
            accelerator_detection_complete=raw.get(
                "accelerator_detection_complete"
            ),
            accelerators=tuple(accelerators),
            capacity_complete=raw.get("capacity_complete"),
            faults=tuple(faults),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    @property
    def capability_id(self) -> str:
        return f"host-cap/{self.digest[:16]}"

    @property
    def host_class(self) -> str:
        if not self.capacity_complete or self.allowed_cpus is None:
            return "unknown"
        return f"{self.platform}/{self.architecture}/cpu-{len(self.allowed_cpus)}"


def build_host_capabilities(facts: LinuxHostFacts) -> HostCapabilities:
    if not isinstance(facts, LinuxHostFacts):
        raise OrchestrationContractError("facts must be LinuxHostFacts")
    faults = list(facts.faults)

    allowed: tuple[int, ...] | None
    if facts.affinity_cpus is not None and facts.cgroup_cpuset_effective is not None:
        allowed = tuple(
            sorted(set(facts.affinity_cpus).intersection(facts.cgroup_cpuset_effective))
        )
        if not allowed:
            faults.append("allowed-cpus:contradiction:affinity-cpuset-empty")
            allowed = None
    else:
        allowed = (
            facts.affinity_cpus
            if facts.affinity_cpus is not None
            else facts.cgroup_cpuset_effective
        )

    quota_observations = tuple(
        CpuQuotaObservation(
            cgroup_path=item.cgroup_path,
            quota_us=item.quota_us,
            period_us=item.period_us,
            equivalent_cpus=item.equivalent_cpus,
        )
        for item in facts.cpu_max_chain
    )
    if facts.cpu_max_complete:
        finite = [
            item.equivalent_cpus
            for item in quota_observations
            if item.equivalent_cpus is not None
        ]
        if finite:
            cpu_quota_status = "limited"
            cpu_quota_equivalents = min(finite)
        else:
            cpu_quota_status = "unlimited"
            cpu_quota_equivalents = None
    else:
        cpu_quota_status = "unknown"
        cpu_quota_equivalents = None

    topology_complete = bool(
        facts.topology_complete
        and allowed is not None
        and {item.cpu for item in facts.topology} == set(allowed)
    )
    physical_core_count: int | None = None
    smt_width: int | None = None
    if topology_complete:
        core_keys = [(item.package_id, item.core_id) for item in facts.topology]
        physical_core_count = len(set(core_keys))
        per_core: dict[tuple[int, int], int] = {}
        for key in core_keys:
            per_core[key] = per_core.get(key, 0) + 1
        smt_width = max(per_core.values())

    memory_observations = tuple(
        MemoryLimitObservation(
            cgroup_path=item.cgroup_path,
            status="unlimited" if item.limit_bytes is None else "limited",
            limit_bytes=item.limit_bytes,
        )
        for item in facts.memory_max_chain
    )
    if facts.memory_max_complete:
        finite_memory = [
            item.limit_bytes
            for item in memory_observations
            if item.limit_bytes is not None
        ]
        if finite_memory:
            cgroup_memory_status = "limited"
            cgroup_memory_limit = min(finite_memory)
        else:
            cgroup_memory_status = "unlimited"
            cgroup_memory_limit = None
    else:
        cgroup_memory_status = "unknown"
        cgroup_memory_limit = None

    hard_memory_candidates = []
    if facts.physical_memory_bytes is not None:
        hard_memory_candidates.append(facts.physical_memory_bytes)
    if cgroup_memory_limit is not None:
        hard_memory_candidates.append(cgroup_memory_limit)
    effective_memory = min(hard_memory_candidates) if hard_memory_candidates else None

    platform_name = facts.platform or "unknown"
    architecture = facts.architecture or "unknown"
    capacity_complete = bool(
        platform_name != "unknown"
        and architecture != "unknown"
        and allowed
        and cpu_quota_status != "unknown"
        and cgroup_memory_status != "unknown"
        and effective_memory is not None
    )

    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id=facts.provider_id,
        platform=platform_name,
        architecture=architecture,
        os_visible_logical_cpus=facts.os_visible_logical_cpus,
        affinity_cpus=facts.affinity_cpus,
        cgroup_cpuset_effective=facts.cgroup_cpuset_effective,
        allowed_cpus=allowed,
        cpu_quota_status=cpu_quota_status,
        cpu_quota_equivalents=cpu_quota_equivalents,
        cpu_quota_observations=quota_observations,
        physical_core_count=physical_core_count,
        smt_width=smt_width,
        topology_complete=topology_complete,
        physical_memory_bytes=facts.physical_memory_bytes,
        cgroup_memory_status=cgroup_memory_status,
        cgroup_memory_limit_bytes=cgroup_memory_limit,
        effective_memory_limit_bytes=effective_memory,
        memory_limit_observations=memory_observations,
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=capacity_complete,
        faults=tuple(faults),
    )


def discover_host_capabilities(
    provider: LinuxHostProvider | None = None,
) -> HostCapabilities:
    provider = provider or LinuxHostProvider()
    return build_host_capabilities(provider.observe_capabilities())
