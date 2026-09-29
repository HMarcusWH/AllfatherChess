"""Read-only Linux host-capacity and pressure observation.

This adapter owns Linux filesystem/kernel parsing only. It never selects an
Allfather resource profile, changes affinity, writes cgroups, or grants compute.
Controller modules turn these observations into orchestration contracts.
"""

from __future__ import annotations

import math
import os
import platform as _platform
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


class LinuxHostProviderError(RuntimeError):
    """Raised when a Linux host fact is malformed or unsafe to read."""


@dataclass(frozen=True)
class CpuMaxFact:
    cgroup_path: str
    quota_us: int | None
    period_us: int

    @property
    def equivalent_cpus(self) -> float | None:
        if self.quota_us is None:
            return None
        return self.quota_us / self.period_us


@dataclass(frozen=True)
class MemoryMaxFact:
    cgroup_path: str
    limit_bytes: int | None


@dataclass(frozen=True)
class CpuTopologyFact:
    cpu: int
    package_id: int
    core_id: int
    thread_siblings: tuple[int, ...]


@dataclass(frozen=True)
class CpuIdentityFact:
    cpu: int
    vendor_id: str
    family: int
    model: int
    stepping: int
    model_name: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class NumaNodeFact:
    node_id: int
    cpus: tuple[int, ...]


@dataclass(frozen=True)
class PsiLineFact:
    kind: str
    avg10: float
    avg60: float
    avg300: float
    total_us: int


@dataclass(frozen=True)
class PsiFact:
    some: PsiLineFact
    full: PsiLineFact | None


@dataclass(frozen=True)
class LinuxHostFacts:
    provider_id: str
    platform: str | None
    architecture: str | None
    os_visible_logical_cpus: int | None
    affinity_cpus: tuple[int, ...] | None
    cgroup_path: str | None
    cgroup_cpuset_effective: tuple[int, ...] | None
    cpu_max_chain: tuple[CpuMaxFact, ...]
    cpu_max_complete: bool
    memory_max_chain: tuple[MemoryMaxFact, ...]
    memory_max_complete: bool
    physical_memory_bytes: int | None
    topology: tuple[CpuTopologyFact, ...]
    topology_complete: bool
    cpu_identity: tuple[CpuIdentityFact, ...]
    cpu_identity_complete: bool
    numa_nodes: tuple[NumaNodeFact, ...]
    numa_complete: bool
    faults: tuple[str, ...]


@dataclass(frozen=True)
class LinuxPressureFacts:
    provider_id: str
    system_cpu: PsiFact | None
    system_memory: PsiFact | None
    cgroup_cpu: PsiFact | None
    cgroup_memory: PsiFact | None
    memory_current_bytes: int | None
    faults: tuple[str, ...]


class LinuxHostProvider:
    """Observe effective Linux host facts without third-party dependencies."""

    provider_id = "linux-host-v2"
    _NODE_RE = re.compile(r"^node([0-9]+)$")

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        sys_root: Path = Path("/sys"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        affinity_reader: Callable[[], object] | None = None,
        cpu_count_reader: Callable[[], object] | None = None,
        platform_reader: Callable[[], tuple[object, object]] | None = None,
    ) -> None:
        self.proc_root = Path(proc_root)
        self.sys_root = Path(sys_root)
        self.cgroup_root = Path(cgroup_root)
        self._affinity_reader = affinity_reader or self._default_affinity
        self._cpu_count_reader = cpu_count_reader or os.cpu_count
        self._platform_reader = platform_reader or self._default_platform

    @staticmethod
    def _default_affinity() -> object:
        if not hasattr(os, "sched_getaffinity"):
            raise OSError("sched_getaffinity unavailable")
        return os.sched_getaffinity(0)

    @staticmethod
    def _default_platform() -> tuple[object, object]:
        return (_platform.system().lower(), _platform.machine().lower())

    @staticmethod
    def parse_cpu_list(text: str) -> tuple[int, ...]:
        raw = text.strip()
        if raw == "":
            return ()
        cpus: set[int] = set()
        for token in raw.split(","):
            token = token.strip()
            if not token:
                raise LinuxHostProviderError("empty CPU-list token")
            if "-" in token:
                if token.count("-") != 1:
                    raise LinuxHostProviderError(f"malformed CPU range: {token!r}")
                left, right = token.split("-", 1)
                if not left.isdigit() or not right.isdigit():
                    raise LinuxHostProviderError(f"non-numeric CPU range: {token!r}")
                start, end = int(left), int(right)
                if start > end:
                    raise LinuxHostProviderError(f"descending CPU range: {token!r}")
                values = range(start, end + 1)
            else:
                if not token.isdigit():
                    raise LinuxHostProviderError(f"non-numeric CPU id: {token!r}")
                values = (int(token),)
            for cpu in values:
                if cpu in cpus:
                    raise LinuxHostProviderError(f"duplicate CPU id: {cpu}")
                cpus.add(cpu)
        return tuple(sorted(cpus))

    @staticmethod
    def parse_cpu_max(text: str) -> tuple[int | None, int]:
        parts = text.strip().split()
        if len(parts) != 2:
            raise LinuxHostProviderError("cpu.max must contain quota and period")
        quota_raw, period_raw = parts
        if not period_raw.isdigit() or int(period_raw) <= 0:
            raise LinuxHostProviderError("cpu.max period must be a positive integer")
        period = int(period_raw)
        if quota_raw == "max":
            return None, period
        if not quota_raw.isdigit() or int(quota_raw) <= 0:
            raise LinuxHostProviderError(
                "cpu.max quota must be 'max' or positive integer"
            )
        return int(quota_raw), period

    @staticmethod
    def parse_memory_limit(text: str) -> int | None:
        raw = text.strip()
        if raw == "max":
            return None
        if not raw.isdigit():
            raise LinuxHostProviderError("memory limit must be 'max' or integer bytes")
        return int(raw)

    @staticmethod
    def parse_self_cgroup(text: str) -> str:
        matches: list[str] = []
        for line in text.splitlines():
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) != 3:
                raise LinuxHostProviderError("malformed /proc/self/cgroup line")
            hierarchy, controllers, path = parts
            if hierarchy == "0" and controllers == "":
                matches.append(path)
        if len(matches) != 1:
            raise LinuxHostProviderError(
                "expected exactly one unified cgroup-v2 membership"
            )
        path = matches[0]
        if not path.startswith("/"):
            raise LinuxHostProviderError("cgroup-v2 membership path must be absolute")
        pure = PurePosixPath(path)
        if any(part in (".", "..") for part in pure.parts):
            raise LinuxHostProviderError("unsafe cgroup-v2 membership path")
        return path

    @staticmethod
    def parse_meminfo(text: str) -> int:
        for line in text.splitlines():
            if not line.startswith("MemTotal:"):
                continue
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                raise LinuxHostProviderError("malformed MemTotal")
            value = int(parts[1])
            unit = parts[2].lower() if len(parts) > 2 else "b"
            scale = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}.get(unit)
            if scale is None:
                raise LinuxHostProviderError(
                    f"unsupported MemTotal unit: {unit!r}"
                )
            return value * scale
        raise LinuxHostProviderError("MemTotal missing from meminfo")

    @staticmethod
    def parse_cpuinfo(text: str) -> tuple[CpuIdentityFact, ...]:
        rows: list[CpuIdentityFact] = []
        seen: set[int] = set()
        blocks = [block for block in text.split("\n\n") if block.strip()]
        if not blocks:
            raise LinuxHostProviderError("/proc/cpuinfo contains no processor blocks")
        for block in blocks:
            fields: dict[str, str] = {}
            for line in block.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                fields[key.strip().lower()] = value.strip()
            if "processor" not in fields:
                continue
            if not fields["processor"].isdigit():
                raise LinuxHostProviderError("cpuinfo processor id is not numeric")
            cpu = int(fields["processor"])
            if cpu in seen:
                raise LinuxHostProviderError(f"duplicate cpuinfo processor id: {cpu}")
            seen.add(cpu)
            required = {
                "vendor_id": "vendor_id",
                "cpu family": "family",
                "model": "model",
                "stepping": "stepping",
                "model name": "model_name",
            }
            missing = [source for source in required if not fields.get(source)]
            if missing:
                raise LinuxHostProviderError(
                    f"cpuinfo processor {cpu} missing fields: {missing}"
                )
            numeric: dict[str, int] = {}
            for source in ("cpu family", "model", "stepping"):
                raw = fields[source]
                if not raw.isdigit():
                    raise LinuxHostProviderError(
                        f"cpuinfo processor {cpu} has non-numeric {source}"
                    )
                numeric[source] = int(raw)
            flags_raw = fields.get("flags") or fields.get("features") or ""
            flags = tuple(sorted(set(flags_raw.split())))
            if not flags:
                raise LinuxHostProviderError(
                    f"cpuinfo processor {cpu} has no feature flags"
                )
            rows.append(
                CpuIdentityFact(
                    cpu=cpu,
                    vendor_id=fields["vendor_id"],
                    family=numeric["cpu family"],
                    model=numeric["model"],
                    stepping=numeric["stepping"],
                    model_name=fields["model name"],
                    flags=flags,
                )
            )
        if not rows:
            raise LinuxHostProviderError("/proc/cpuinfo has no usable processor records")
        return tuple(sorted(rows, key=lambda item: item.cpu))

    @staticmethod
    def parse_psi(text: str) -> PsiFact:
        parsed: dict[str, PsiLineFact] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            fields = line.split()
            kind = fields[0]
            if kind not in ("some", "full") or kind in parsed:
                raise LinuxHostProviderError(f"invalid PSI line kind: {kind!r}")
            values: dict[str, str] = {}
            for item in fields[1:]:
                if "=" not in item:
                    raise LinuxHostProviderError("malformed PSI field")
                key, value = item.split("=", 1)
                if key in values:
                    raise LinuxHostProviderError(f"duplicate PSI field: {key}")
                values[key] = value
            required = {"avg10", "avg60", "avg300", "total"}
            if set(values) != required:
                raise LinuxHostProviderError(
                    f"PSI fields must be exactly {sorted(required)}"
                )
            avgs = []
            for key in ("avg10", "avg60", "avg300"):
                try:
                    number = float(values[key])
                except ValueError as exc:
                    raise LinuxHostProviderError(f"invalid PSI {key}") from exc
                if not math.isfinite(number) or number < 0:
                    raise LinuxHostProviderError(
                        f"PSI {key} must be finite/non-negative"
                    )
                avgs.append(0.0 if number == 0.0 else number)
            if not values["total"].isdigit():
                raise LinuxHostProviderError("PSI total must be non-negative integer")
            parsed[kind] = PsiLineFact(
                kind=kind,
                avg10=avgs[0],
                avg60=avgs[1],
                avg300=avgs[2],
                total_us=int(values["total"]),
            )
        if "some" not in parsed:
            raise LinuxHostProviderError("PSI 'some' line is required")
        return PsiFact(some=parsed["some"], full=parsed.get("full"))

    @staticmethod
    def _stable_fault(label: str, exc: BaseException) -> str:
        if isinstance(exc, OSError):
            return f"{label}:OSError:{exc.errno}"
        return f"{label}:{type(exc).__name__}:{exc}"

    def _read_text(self, path: Path, label: str) -> tuple[str | None, str | None]:
        try:
            return path.read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, self._stable_fault(label, exc)

    def _resolve_cgroup_path(self, membership: str) -> Path:
        root = self.cgroup_root.resolve()
        relative = membership.lstrip("/")
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise LinuxHostProviderError("cgroup path escapes configured cgroup root")
        return candidate

    def _cgroup_chain(self, membership: str) -> tuple[tuple[str, Path], ...]:
        root = self.cgroup_root.resolve()
        leaf = self._resolve_cgroup_path(membership)
        chain: list[tuple[str, Path]] = []
        current = leaf
        while True:
            relative = current.relative_to(root)
            label = "root" if not relative.parts else relative.as_posix()
            chain.append((label, current))
            if current == root:
                break
            current = current.parent
            if current != root and root not in current.parents:
                raise LinuxHostProviderError("cgroup ancestor escaped configured root")
        return tuple(chain)

    def _membership(self) -> tuple[str | None, str | None]:
        text, fault = self._read_text(
            self.proc_root / "self" / "cgroup", "proc-self-cgroup"
        )
        if text is None:
            return None, fault
        try:
            return self.parse_self_cgroup(text), None
        except Exception as exc:
            return None, self._stable_fault("proc-self-cgroup", exc)

    def _observe_cpu_identity(
        self, candidate_cpus: tuple[int, ...] | None, faults: list[str]
    ) -> tuple[tuple[CpuIdentityFact, ...], bool]:
        if candidate_cpus is None:
            return (), False
        text, fault = self._read_text(self.proc_root / "cpuinfo", "cpuinfo")
        if text is None:
            if fault:
                faults.append(fault)
            return (), False
        try:
            all_rows = self.parse_cpuinfo(text)
        except Exception as exc:
            faults.append(self._stable_fault("cpuinfo", exc))
            return (), False
        by_cpu = {row.cpu: row for row in all_rows}
        rows = tuple(by_cpu[cpu] for cpu in candidate_cpus if cpu in by_cpu)
        complete = len(rows) == len(candidate_cpus)
        if not complete:
            faults.append("cpuinfo:incomplete-for-effective-cpuset")
        return rows, complete

    def _observe_numa(
        self, candidate_cpus: tuple[int, ...] | None, faults: list[str]
    ) -> tuple[tuple[NumaNodeFact, ...], bool]:
        if candidate_cpus is None:
            return (), False
        node_root = self.sys_root / "devices" / "system" / "node"
        try:
            entries = sorted(node_root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            faults.append(self._stable_fault("numa", exc))
            return (), False

        candidate = set(candidate_cpus)
        nodes: list[NumaNodeFact] = []
        seen: set[int] = set()
        complete = True
        for entry in entries:
            match = self._NODE_RE.fullmatch(entry.name)
            if match is None or not entry.is_dir():
                continue
            text, fault = self._read_text(entry / "cpulist", f"numa:{entry.name}")
            if text is None:
                complete = False
                if fault:
                    faults.append(fault)
                continue
            try:
                cpus = tuple(
                    cpu for cpu in self.parse_cpu_list(text) if cpu in candidate
                )
            except Exception as exc:
                complete = False
                faults.append(self._stable_fault(f"numa:{entry.name}", exc))
                continue
            if not cpus:
                continue
            overlap = seen.intersection(cpus)
            if overlap:
                complete = False
                faults.append(
                    f"numa:{entry.name}:overlapping-effective-cpus:{sorted(overlap)}"
                )
            seen.update(cpus)
            nodes.append(NumaNodeFact(int(match.group(1)), tuple(sorted(cpus))))
        if seen != candidate:
            complete = False
            faults.append("numa:effective-cpuset-not-fully-mapped")
        if not nodes:
            complete = False
        return tuple(sorted(nodes, key=lambda item: item.node_id)), complete

    def observe_capabilities(self) -> LinuxHostFacts:
        faults: list[str] = []

        platform_name: str | None = None
        architecture: str | None = None
        try:
            platform_raw, arch_raw = self._platform_reader()
            if not isinstance(platform_raw, str) or not platform_raw:
                raise LinuxHostProviderError("platform reader returned invalid platform")
            if not isinstance(arch_raw, str) or not arch_raw:
                raise LinuxHostProviderError(
                    "platform reader returned invalid architecture"
                )
            platform_name = platform_raw.lower()
            architecture = arch_raw.lower()
        except Exception as exc:
            faults.append(self._stable_fault("platform", exc))

        visible: int | None = None
        try:
            raw_count = self._cpu_count_reader()
            if (
                isinstance(raw_count, bool)
                or not isinstance(raw_count, int)
                or raw_count <= 0
            ):
                raise LinuxHostProviderError(
                    "os cpu count must be positive integer"
                )
            visible = raw_count
        except Exception as exc:
            faults.append(self._stable_fault("os-cpu-count", exc))

        affinity: tuple[int, ...] | None = None
        try:
            raw_affinity = self._affinity_reader()
            if raw_affinity is None:
                raise LinuxHostProviderError("affinity unavailable")
            values = tuple(raw_affinity)
            if any(
                isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0
                for cpu in values
            ):
                raise LinuxHostProviderError("affinity contains invalid CPU id")
            if len(values) != len(set(values)) or not values:
                raise LinuxHostProviderError(
                    "affinity must be non-empty and unique"
                )
            affinity = tuple(sorted(values))
        except Exception as exc:
            faults.append(self._stable_fault("affinity", exc))

        membership, membership_fault = self._membership()
        if membership_fault:
            faults.append(membership_fault)

        cpuset: tuple[int, ...] | None = None
        cpu_chain: list[CpuMaxFact] = []
        mem_chain: list[MemoryMaxFact] = []
        cpu_complete = membership is not None
        mem_complete = membership is not None

        chain: tuple[tuple[str, Path], ...] = ()
        if membership is not None:
            try:
                chain = self._cgroup_chain(membership)
            except Exception as exc:
                faults.append(self._stable_fault("cgroup-path", exc))
                cpu_complete = False
                mem_complete = False

        if chain:
            leaf_label, leaf = chain[0]
            cpuset_text, cpuset_fault = self._read_text(
                leaf / "cpuset.cpus.effective",
                f"cpuset:{leaf_label}",
            )
            if cpuset_text is not None:
                try:
                    parsed = self.parse_cpu_list(cpuset_text)
                    if not parsed:
                        raise LinuxHostProviderError("effective cpuset is empty")
                    cpuset = parsed
                except Exception as exc:
                    faults.append(self._stable_fault("cpuset-effective", exc))
            elif cpuset_fault:
                faults.append(cpuset_fault)

            for label, path in chain:
                cpu_text, cpu_fault = self._read_text(
                    path / "cpu.max", f"cpu.max:{label}"
                )
                if cpu_text is None:
                    cpu_complete = False
                    if cpu_fault:
                        faults.append(cpu_fault)
                else:
                    try:
                        quota, period = self.parse_cpu_max(cpu_text)
                        cpu_chain.append(CpuMaxFact(label, quota, period))
                    except Exception as exc:
                        cpu_complete = False
                        faults.append(
                            self._stable_fault(f"cpu.max:{label}", exc)
                        )

                mem_text, mem_fault = self._read_text(
                    path / "memory.max", f"memory.max:{label}"
                )
                if mem_text is None:
                    mem_complete = False
                    if mem_fault:
                        faults.append(mem_fault)
                else:
                    try:
                        mem_chain.append(
                            MemoryMaxFact(
                                label, self.parse_memory_limit(mem_text)
                            )
                        )
                    except Exception as exc:
                        mem_complete = False
                        faults.append(
                            self._stable_fault(f"memory.max:{label}", exc)
                        )

        physical_memory: int | None = None
        meminfo, meminfo_fault = self._read_text(
            self.proc_root / "meminfo", "meminfo"
        )
        if meminfo is None:
            if meminfo_fault:
                faults.append(meminfo_fault)
        else:
            try:
                physical_memory = self.parse_meminfo(meminfo)
            except Exception as exc:
                faults.append(self._stable_fault("meminfo", exc))

        candidate_cpus: tuple[int, ...] | None
        if affinity is not None and cpuset is not None:
            intersection = tuple(sorted(set(affinity).intersection(cpuset)))
            if not intersection:
                faults.append(
                    "allowed-cpus:contradiction:affinity-cpuset-empty"
                )
                candidate_cpus = None
            else:
                candidate_cpus = intersection
        else:
            candidate_cpus = (
                affinity if affinity is not None else cpuset
            )

        topology: list[CpuTopologyFact] = []
        topology_complete = candidate_cpus is not None
        if candidate_cpus is not None:
            for cpu in candidate_cpus:
                root = (
                    self.sys_root
                    / "devices"
                    / "system"
                    / "cpu"
                    / f"cpu{cpu}"
                    / "topology"
                )
                values: dict[str, str] = {}
                for key in (
                    "physical_package_id",
                    "core_id",
                    "thread_siblings_list",
                ):
                    text, fault = self._read_text(
                        root / key, f"topology:{cpu}:{key}"
                    )
                    if text is None:
                        topology_complete = False
                        if fault:
                            faults.append(fault)
                    else:
                        values[key] = text
                if len(values) != 3:
                    continue
                try:
                    package = int(values["physical_package_id"].strip())
                    core = int(values["core_id"].strip())
                    if package < 0 or core < 0:
                        raise LinuxHostProviderError("negative topology id")
                    siblings = self.parse_cpu_list(
                        values["thread_siblings_list"]
                    )
                    if cpu not in siblings:
                        raise LinuxHostProviderError(
                            "CPU absent from thread sibling set"
                        )
                    topology.append(
                        CpuTopologyFact(cpu, package, core, siblings)
                    )
                except Exception as exc:
                    topology_complete = False
                    faults.append(
                        self._stable_fault(f"topology:{cpu}", exc)
                    )
        else:
            topology_complete = False

        cpu_identity, cpu_identity_complete = self._observe_cpu_identity(
            candidate_cpus, faults
        )
        numa_nodes, numa_complete = self._observe_numa(
            candidate_cpus, faults
        )

        return LinuxHostFacts(
            provider_id=self.provider_id,
            platform=platform_name,
            architecture=architecture,
            os_visible_logical_cpus=visible,
            affinity_cpus=affinity,
            cgroup_path=membership,
            cgroup_cpuset_effective=cpuset,
            cpu_max_chain=tuple(cpu_chain),
            cpu_max_complete=cpu_complete and bool(chain),
            memory_max_chain=tuple(mem_chain),
            memory_max_complete=mem_complete and bool(chain),
            physical_memory_bytes=physical_memory,
            topology=tuple(sorted(topology, key=lambda item: item.cpu)),
            topology_complete=(
                topology_complete
                and candidate_cpus is not None
                and len(topology) == len(candidate_cpus)
            ),
            cpu_identity=cpu_identity,
            cpu_identity_complete=cpu_identity_complete,
            numa_nodes=numa_nodes,
            numa_complete=numa_complete,
            faults=tuple(faults),
        )

    def observe_pressure(self) -> LinuxPressureFacts:
        faults: list[str] = []

        def read_psi(path: Path, label: str) -> PsiFact | None:
            text, fault = self._read_text(path, label)
            if text is None:
                if fault:
                    faults.append(fault)
                return None
            try:
                return self.parse_psi(text)
            except Exception as exc:
                faults.append(self._stable_fault(label, exc))
                return None

        system_cpu = read_psi(
            self.proc_root / "pressure" / "cpu", "psi:system:cpu"
        )
        system_memory = read_psi(
            self.proc_root / "pressure" / "memory", "psi:system:memory"
        )

        membership, membership_fault = self._membership()
        if membership_fault:
            faults.append(membership_fault)

        cgroup_cpu: PsiFact | None = None
        cgroup_memory: PsiFact | None = None
        memory_current: int | None = None
        if membership is not None:
            try:
                leaf = self._resolve_cgroup_path(membership)
                cgroup_cpu = read_psi(
                    leaf / "cpu.pressure", "psi:cgroup:cpu"
                )
                cgroup_memory = read_psi(
                    leaf / "memory.pressure", "psi:cgroup:memory"
                )
                current_text, current_fault = self._read_text(
                    leaf / "memory.current", "memory.current"
                )
                if current_text is None:
                    if current_fault:
                        faults.append(current_fault)
                else:
                    raw = current_text.strip()
                    if not raw.isdigit():
                        raise LinuxHostProviderError(
                            "memory.current must be non-negative integer"
                        )
                    memory_current = int(raw)
            except Exception as exc:
                faults.append(
                    self._stable_fault("cgroup-pressure", exc)
                )

        return LinuxPressureFacts(
            provider_id=self.provider_id,
            system_cpu=system_cpu,
            system_memory=system_memory,
            cgroup_cpu=cgroup_cpu,
            cgroup_memory=cgroup_memory,
            memory_current_bytes=memory_current,
            faults=tuple(faults),
        )
