"""Immutable M14-J host-capability contracts and discovery.

J2 observes capacity only. These facts carry no profile-selection, resource
allocation, process-placement, or outward-move authority.

Two identities are deliberately distinct:
- capability_id hashes the exact observation, including concrete CPU ids and
  cgroup observation paths.
- qualification_domain_id hashes only stable compatibility facts suitable for
  binding profile evidence across equivalent hosts.
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


HOST_CAPABILITIES_VERSION = "host-capabilities-v2"
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
        any(
            isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0
            for cpu in cpus
        )
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
    if any(
        not isinstance(item, str) or not item or "\x00" in item
        for item in faults
    ):
        raise OrchestrationContractError(
            f"{label} must contain non-empty strings"
        )
    return faults


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise OrchestrationContractError(f"{label} must be an array")
    values = tuple(value)
    if (
        any(not isinstance(item, str) or not item for item in values)
        or tuple(sorted(values)) != values
        or len(values) != len(set(values))
    ):
        raise OrchestrationContractError(
            f"{label} must contain sorted unique non-empty strings"
        )
    return values


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
            raw,
            {"cgroup_path", "status", "limit_bytes"},
            "memory limit observation",
        )
        return cls(
            cgroup_path=raw.get("cgroup_path"),
            status=raw.get("status"),
            limit_bytes=raw.get("limit_bytes"),
        )


@dataclass(frozen=True)
class NumaNodeObservation:
    node_id: int
    cpus: tuple[int, ...]

    def __post_init__(self) -> None:
        _nonnegative_int(self.node_id, "numa node_id")
        cpus = _cpu_tuple(self.cpus, "numa cpus")
        if cpus is None:
            raise OrchestrationContractError("numa cpus may not be null")
        object.__setattr__(self, "cpus", cpus)

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "cpus": list(self.cpus)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NumaNodeObservation":
        raw = _mapping(raw, "numa node observation")
        _reject_unknown(raw, {"node_id", "cpus"}, "numa node observation")
        cpus = raw.get("cpus")
        if not isinstance(cpus, list):
            raise OrchestrationContractError("numa cpus must be an array")
        return cls(node_id=raw.get("node_id"), cpus=tuple(cpus))


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
    cpu_vendor_id: str | None
    cpu_family: int | None
    cpu_model: int | None
    cpu_stepping: int | None
    cpu_model_name: str | None
    cpu_microcode: str | None
    cpu_flags_intersection: tuple[str, ...]
    cpu_feature_digest: str | None
    cpu_identity_complete: bool
    cpu_quota_status: str
    cpu_quota_equivalents: float | None
    cpu_quota_observations: tuple[CpuQuotaObservation, ...]
    physical_core_count: int | None
    smt_width: int | None
    topology_complete: bool
    numa_nodes: tuple[NumaNodeObservation, ...]
    numa_complete: bool
    physical_memory_bytes: int | None
    cgroup_memory_status: str
    cgroup_memory_limit_bytes: int | None
    effective_memory_limit_bytes: int | None
    memory_limit_observations: tuple[MemoryLimitObservation, ...]
    accelerator_detection_complete: bool
    accelerators: tuple[str, ...]
    capacity_complete: bool
    qualification_domain_complete: bool
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
        for name in (
            "affinity_cpus",
            "cgroup_cpuset_effective",
            "allowed_cpus",
        ):
            object.__setattr__(
                self, name, _cpu_tuple(getattr(self, name), name)
            )

        for name in ("cpu_vendor_id", "cpu_model_name", "cpu_microcode"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value or "\x00" in value
            ):
                raise OrchestrationContractError(
                    f"{name} must be non-empty NUL-free string or null"
                )
        for name in ("cpu_family", "cpu_model", "cpu_stepping"):
            object.__setattr__(
                self,
                name,
                _optional_nonnegative_int(getattr(self, name), name),
            )
        flags = _string_tuple(
            self.cpu_flags_intersection, "cpu_flags_intersection"
        )
        object.__setattr__(self, "cpu_flags_intersection", flags)
        if self.cpu_feature_digest is not None:
            if (
                not isinstance(self.cpu_feature_digest, str)
                or len(self.cpu_feature_digest) != 64
                or any(ch not in "0123456789abcdef" for ch in self.cpu_feature_digest)
            ):
                raise OrchestrationContractError(
                    "cpu_feature_digest must be lowercase SHA-256 or null"
                )
            expected = canonical_digest(list(flags))
            if self.cpu_feature_digest != expected:
                raise OrchestrationContractError(
                    "cpu_feature_digest does not match feature intersection"
                )
        if not isinstance(self.cpu_identity_complete, bool):
            raise OrchestrationContractError(
                "cpu_identity_complete must be boolean"
            )
        identity_fields = (
            self.cpu_vendor_id,
            self.cpu_family,
            self.cpu_model,
            self.cpu_stepping,
            self.cpu_model_name,
            self.cpu_feature_digest,
        )
        if self.cpu_identity_complete and (
            any(value is None for value in identity_fields) or not flags
        ):
            raise OrchestrationContractError(
                "complete CPU identity requires all identity fields and flags"
            )

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
        if not all(
            isinstance(item, CpuQuotaObservation) for item in quotas
        ):
            raise OrchestrationContractError(
                "cpu_quota_observations must contain CpuQuotaObservation values"
            )
        object.__setattr__(self, "cpu_quota_observations", quotas)

        object.__setattr__(
            self,
            "physical_core_count",
            _optional_positive_int(
                self.physical_core_count, "physical_core_count"
            ),
        )
        object.__setattr__(
            self,
            "smt_width",
            _optional_positive_int(self.smt_width, "smt_width"),
        )
        if not isinstance(self.topology_complete, bool):
            raise OrchestrationContractError(
                "topology_complete must be boolean"
            )
        if self.topology_complete and (
            self.physical_core_count is None or self.smt_width is None
        ):
            raise OrchestrationContractError(
                "complete topology requires physical_core_count and smt_width"
            )

        nodes = tuple(self.numa_nodes)
        if not all(isinstance(item, NumaNodeObservation) for item in nodes):
            raise OrchestrationContractError(
                "numa_nodes must contain NumaNodeObservation values"
            )
        node_ids = [item.node_id for item in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise OrchestrationContractError("NUMA node ids must be unique")
        object.__setattr__(
            self, "numa_nodes", tuple(sorted(nodes, key=lambda item: item.node_id))
        )
        if not isinstance(self.numa_complete, bool):
            raise OrchestrationContractError("numa_complete must be boolean")
        if self.numa_complete:
            if self.allowed_cpus is None or not nodes:
                raise OrchestrationContractError(
                    "complete NUMA topology requires allowed CPUs and nodes"
                )
            flattened = [cpu for node in nodes for cpu in node.cpus]
            if len(flattened) != len(set(flattened)):
                raise OrchestrationContractError(
                    "NUMA node CPU assignments overlap"
                )
            if set(flattened) != set(self.allowed_cpus):
                raise OrchestrationContractError(
                    "NUMA nodes do not cover the effective CPU set"
                )

        physical_memory = _optional_positive_int(
            self.physical_memory_bytes, "physical_memory_bytes"
        )
        object.__setattr__(self, "physical_memory_bytes", physical_memory)

        if self.cgroup_memory_status not in _LIMIT_STATES:
            raise OrchestrationContractError(
                "invalid cgroup_memory_status"
            )
        cgroup_memory = _optional_nonnegative_int(
            self.cgroup_memory_limit_bytes, "cgroup_memory_limit_bytes"
        )
        if self.cgroup_memory_status == "limited" and cgroup_memory is None:
            raise OrchestrationContractError(
                "limited cgroup memory requires cgroup_memory_limit_bytes"
            )
        if (
            self.cgroup_memory_status != "limited"
            and cgroup_memory is not None
        ):
            raise OrchestrationContractError(
                "non-limited cgroup memory may not carry a limit"
            )
        object.__setattr__(
            self, "cgroup_memory_limit_bytes", cgroup_memory
        )
        effective_memory = _optional_nonnegative_int(
            self.effective_memory_limit_bytes, "effective_memory_limit_bytes"
        )
        object.__setattr__(
            self, "effective_memory_limit_bytes", effective_memory
        )

        memory_observations = tuple(self.memory_limit_observations)
        if not all(
            isinstance(item, MemoryLimitObservation)
            for item in memory_observations
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
            raise OrchestrationContractError(
                "accelerators must be unique"
            )
        object.__setattr__(
            self, "accelerators", tuple(sorted(accelerators))
        )

        if not isinstance(self.capacity_complete, bool):
            raise OrchestrationContractError(
                "capacity_complete must be boolean"
            )
        expected_capacity_complete = bool(
            self.platform != "unknown"
            and self.architecture != "unknown"
            and self.allowed_cpus is not None
            and self.cpu_quota_status != "unknown"
            and self.cgroup_memory_status != "unknown"
            and self.effective_memory_limit_bytes is not None
            and self.effective_memory_limit_bytes > 0
        )
        if self.capacity_complete != expected_capacity_complete:
            raise OrchestrationContractError(
                "capacity_complete does not match the declared capacity facts"
            )

        if not isinstance(self.qualification_domain_complete, bool):
            raise OrchestrationContractError(
                "qualification_domain_complete must be boolean"
            )
        expected_domain_complete = bool(
            self.capacity_complete and self.cpu_identity_complete
        )
        if self.qualification_domain_complete != expected_domain_complete:
            raise OrchestrationContractError(
                "qualification_domain_complete does not match host evidence"
            )
        object.__setattr__(
            self, "faults", _fault_tuple(self.faults, "host faults")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "version": self.version,
            "provider_id": self.provider_id,
            "platform": self.platform,
            "architecture": self.architecture,
            "os_visible_logical_cpus": self.os_visible_logical_cpus,
            "affinity_cpus": (
                None if self.affinity_cpus is None else list(self.affinity_cpus)
            ),
            "cgroup_cpuset_effective": (
                None
                if self.cgroup_cpuset_effective is None
                else list(self.cgroup_cpuset_effective)
            ),
            "allowed_cpus": (
                None if self.allowed_cpus is None else list(self.allowed_cpus)
            ),
            "cpu_vendor_id": self.cpu_vendor_id,
            "cpu_family": self.cpu_family,
            "cpu_model": self.cpu_model,
            "cpu_stepping": self.cpu_stepping,
            "cpu_model_name": self.cpu_model_name,
            "cpu_microcode": self.cpu_microcode,
            "cpu_flags_intersection": list(self.cpu_flags_intersection),
            "cpu_feature_digest": self.cpu_feature_digest,
            "cpu_identity_complete": self.cpu_identity_complete,
            "cpu_quota_status": self.cpu_quota_status,
            "cpu_quota_equivalents": self.cpu_quota_equivalents,
            "cpu_quota_observations": [
                item.as_dict() for item in self.cpu_quota_observations
            ],
            "physical_core_count": self.physical_core_count,
            "smt_width": self.smt_width,
            "topology_complete": self.topology_complete,
            "numa_nodes": [item.as_dict() for item in self.numa_nodes],
            "numa_complete": self.numa_complete,
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
            "qualification_domain_complete": self.qualification_domain_complete,
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
            "cpu_vendor_id",
            "cpu_family",
            "cpu_model",
            "cpu_stepping",
            "cpu_model_name",
            "cpu_microcode",
            "cpu_flags_intersection",
            "cpu_feature_digest",
            "cpu_identity_complete",
            "cpu_quota_status",
            "cpu_quota_equivalents",
            "cpu_quota_observations",
            "physical_core_count",
            "smt_width",
            "topology_complete",
            "numa_nodes",
            "numa_complete",
            "physical_memory_bytes",
            "cgroup_memory_status",
            "cgroup_memory_limit_bytes",
            "effective_memory_limit_bytes",
            "memory_limit_observations",
            "accelerator_detection_complete",
            "accelerators",
            "capacity_complete",
            "qualification_domain_complete",
            "faults",
            "authority",
        }
        _reject_unknown(raw, allowed, "host capabilities")
        if raw.get("authority") != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError(
                "host capabilities authority marker is invalid"
            )
        quotas = raw.get("cpu_quota_observations", [])
        mems = raw.get("memory_limit_observations", [])
        nodes = raw.get("numa_nodes", [])
        flags = raw.get("cpu_flags_intersection", [])
        if (
            not isinstance(quotas, list)
            or not isinstance(mems, list)
            or not isinstance(nodes, list)
            or not isinstance(flags, list)
        ):
            raise OrchestrationContractError(
                "host observation collections must be arrays"
            )
        accelerators = raw.get("accelerators", [])
        faults = raw.get("faults", [])
        if not isinstance(accelerators, list) or not isinstance(faults, list):
            raise OrchestrationContractError(
                "accelerators/faults must be arrays"
            )
        return cls(
            version=raw.get("version"),
            provider_id=raw.get("provider_id"),
            platform=raw.get("platform"),
            architecture=raw.get("architecture"),
            os_visible_logical_cpus=raw.get("os_visible_logical_cpus"),
            affinity_cpus=(
                None
                if raw.get("affinity_cpus") is None
                else tuple(raw["affinity_cpus"])
            ),
            cgroup_cpuset_effective=(
                None
                if raw.get("cgroup_cpuset_effective") is None
                else tuple(raw["cgroup_cpuset_effective"])
            ),
            allowed_cpus=(
                None
                if raw.get("allowed_cpus") is None
                else tuple(raw["allowed_cpus"])
            ),
            cpu_vendor_id=raw.get("cpu_vendor_id"),
            cpu_family=raw.get("cpu_family"),
            cpu_model=raw.get("cpu_model"),
            cpu_stepping=raw.get("cpu_stepping"),
            cpu_model_name=raw.get("cpu_model_name"),
            cpu_microcode=raw.get("cpu_microcode"),
            cpu_flags_intersection=tuple(flags),
            cpu_feature_digest=raw.get("cpu_feature_digest"),
            cpu_identity_complete=raw.get("cpu_identity_complete"),
            cpu_quota_status=raw.get("cpu_quota_status"),
            cpu_quota_equivalents=raw.get("cpu_quota_equivalents"),
            cpu_quota_observations=tuple(
                CpuQuotaObservation.from_dict(item) for item in quotas
            ),
            physical_core_count=raw.get("physical_core_count"),
            smt_width=raw.get("smt_width"),
            topology_complete=raw.get("topology_complete"),
            numa_nodes=tuple(
                NumaNodeObservation.from_dict(item) for item in nodes
            ),
            numa_complete=raw.get("numa_complete"),
            physical_memory_bytes=raw.get("physical_memory_bytes"),
            cgroup_memory_status=raw.get("cgroup_memory_status"),
            cgroup_memory_limit_bytes=raw.get(
                "cgroup_memory_limit_bytes"
            ),
            effective_memory_limit_bytes=raw.get(
                "effective_memory_limit_bytes"
            ),
            memory_limit_observations=tuple(
                MemoryLimitObservation.from_dict(item) for item in mems
            ),
            accelerator_detection_complete=raw.get(
                "accelerator_detection_complete"
            ),
            accelerators=tuple(accelerators),
            capacity_complete=raw.get("capacity_complete"),
            qualification_domain_complete=raw.get(
                "qualification_domain_complete"
            ),
            faults=tuple(faults),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    @property
    def capability_id(self) -> str:
        return f"host-cap/{self.digest[:16]}"

    @property
    def qualification_domain_material(self) -> dict[str, Any] | None:
        if not self.qualification_domain_complete:
            return None
        assert self.allowed_cpus is not None
        assert self.cpu_vendor_id is not None
        assert self.cpu_family is not None
        assert self.cpu_model is not None
        assert self.cpu_stepping is not None
        assert self.cpu_feature_digest is not None
        assert self.effective_memory_limit_bytes is not None

        memory_mib = self.effective_memory_limit_bytes // (1024 * 1024)
        memory_class_mib = (
            memory_mib
            if memory_mib < 256
            else (memory_mib // 256) * 256
        )
        numa_counts = (
            sorted(len(node.cpus) for node in self.numa_nodes)
            if self.numa_complete
            else []
        )
        return {
            "schema_version": 1,
            "platform": self.platform,
            "architecture": self.architecture,
            "cpu": {
                "vendor_id": self.cpu_vendor_id,
                "family": self.cpu_family,
                "model": self.cpu_model,
                "stepping": self.cpu_stepping,
                "microcode": self.cpu_microcode,
                "feature_digest": self.cpu_feature_digest,
                "allowed_logical_cpus": len(self.allowed_cpus),
            },
            "topology": {
                "complete": self.topology_complete,
                "physical_cores": (
                    self.physical_core_count if self.topology_complete else None
                ),
                "smt_width": self.smt_width if self.topology_complete else None,
            },
            "numa": {
                "complete": self.numa_complete,
                "node_count": (
                    len(self.numa_nodes) if self.numa_complete else None
                ),
                "logical_cpu_counts": numa_counts,
            },
            "limits": {
                "cpu_quota_status": self.cpu_quota_status,
                "cpu_quota_equivalents": self.cpu_quota_equivalents,
                "memory_class_mib": memory_class_mib,
            },
        }

    @property
    def qualification_domain_id(self) -> str | None:
        material = self.qualification_domain_material
        if material is None:
            return None
        return f"host-domain/{canonical_digest(material)[:20]}"

    @property
    def host_class(self) -> str:
        if not self.qualification_domain_complete or self.allowed_cpus is None:
            return "unknown"
        assert self.cpu_vendor_id is not None
        assert self.cpu_family is not None
        assert self.cpu_model is not None
        assert self.cpu_stepping is not None
        return (
            f"{self.platform}/{self.architecture}/"
            f"{self.cpu_vendor_id}-f{self.cpu_family}-m{self.cpu_model}-"
            f"s{self.cpu_stepping}/cpu-{len(self.allowed_cpus)}"
        )


def build_host_capabilities(facts: LinuxHostFacts) -> HostCapabilities:
    if not isinstance(facts, LinuxHostFacts):
        raise OrchestrationContractError("facts must be LinuxHostFacts")
    faults = list(facts.faults)

    allowed: tuple[int, ...] | None
    if (
        facts.affinity_cpus is not None
        and facts.cgroup_cpuset_effective is not None
    ):
        allowed = tuple(
            sorted(
                set(facts.affinity_cpus).intersection(
                    facts.cgroup_cpuset_effective
                )
            )
        )
        if not allowed:
            faults.append(
                "allowed-cpus:contradiction:affinity-cpuset-empty"
            )
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
        core_keys = [
            (item.package_id, item.core_id) for item in facts.topology
        ]
        physical_core_count = len(set(core_keys))
        per_core: dict[tuple[int, int], int] = {}
        for key in core_keys:
            per_core[key] = per_core.get(key, 0) + 1
        smt_width = max(per_core.values())

    cpu_vendor_id: str | None = None
    cpu_family: int | None = None
    cpu_model: int | None = None
    cpu_stepping: int | None = None
    cpu_flags: tuple[str, ...] = ()
    cpu_feature_digest: str | None = None
    cpu_identity_complete = bool(
        facts.cpu_identity_complete
        and allowed is not None
        and {item.cpu for item in facts.cpu_identity} == set(allowed)
    )
    if cpu_identity_complete:
        signatures = {
            (
                item.vendor_id,
                item.family,
                item.model,
                item.stepping,
            )
            for item in facts.cpu_identity
        }
        if len(signatures) != 1:
            cpu_identity_complete = False
            faults.append("cpuinfo:heterogeneous-effective-cpus")
        else:
            cpu_vendor_id, cpu_family, cpu_model, cpu_stepping = next(
                iter(signatures)
            )
            names = sorted({item.model_name for item in facts.cpu_identity})
            cpu_model_name = " | ".join(names)
            microcodes = sorted(
                {
                    item.microcode
                    for item in facts.cpu_identity
                    if item.microcode is not None
                }
            )
            cpu_microcode = (
                microcodes[0] if len(microcodes) == 1 else None
            )
            if len(microcodes) > 1:
                faults.append("cpuinfo:heterogeneous-microcode")
            feature_sets = [set(item.flags) for item in facts.cpu_identity]
            intersection = set.intersection(*feature_sets)
            cpu_flags = tuple(sorted(intersection))
            if not cpu_flags:
                cpu_identity_complete = False
                faults.append("cpuinfo:empty-feature-intersection")
            else:
                cpu_feature_digest = canonical_digest(list(cpu_flags))

    numa_nodes = tuple(
        NumaNodeObservation(item.node_id, item.cpus)
        for item in facts.numa_nodes
    )
    numa_complete = bool(
        facts.numa_complete
        and allowed is not None
        and {cpu for node in numa_nodes for cpu in node.cpus} == set(allowed)
    )

    memory_observations = tuple(
        MemoryLimitObservation(
            cgroup_path=item.cgroup_path,
            status=(
                "unlimited" if item.limit_bytes is None else "limited"
            ),
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
    effective_memory = (
        min(hard_memory_candidates) if hard_memory_candidates else None
    )

    platform_name = facts.platform or "unknown"
    architecture = facts.architecture or "unknown"
    capacity_complete = bool(
        platform_name != "unknown"
        and architecture != "unknown"
        and allowed
        and cpu_quota_status != "unknown"
        and cgroup_memory_status != "unknown"
        and effective_memory is not None
        and effective_memory > 0
    )
    qualification_domain_complete = bool(
        capacity_complete and cpu_identity_complete
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
        cpu_vendor_id=cpu_vendor_id,
        cpu_family=cpu_family,
        cpu_model=cpu_model,
        cpu_stepping=cpu_stepping,
        cpu_model_name=cpu_model_name,
        cpu_microcode=cpu_microcode,
        cpu_flags_intersection=cpu_flags,
        cpu_feature_digest=cpu_feature_digest,
        cpu_identity_complete=cpu_identity_complete,
        cpu_quota_status=cpu_quota_status,
        cpu_quota_equivalents=cpu_quota_equivalents,
        cpu_quota_observations=quota_observations,
        physical_core_count=physical_core_count,
        smt_width=smt_width,
        topology_complete=topology_complete,
        numa_nodes=numa_nodes,
        numa_complete=numa_complete,
        physical_memory_bytes=facts.physical_memory_bytes,
        cgroup_memory_status=cgroup_memory_status,
        cgroup_memory_limit_bytes=cgroup_memory_limit,
        effective_memory_limit_bytes=effective_memory,
        memory_limit_observations=memory_observations,
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=capacity_complete,
        qualification_domain_complete=qualification_domain_complete,
        faults=tuple(faults),
    )


def discover_host_capabilities(
    provider: LinuxHostProvider | None = None,
) -> HostCapabilities:
    provider = provider or LinuxHostProvider()
    return build_host_capabilities(provider.observe_capabilities())
