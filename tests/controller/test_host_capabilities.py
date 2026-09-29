#!/usr/bin/env python3
"""Contract tests for J2 HostCapabilities."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_host import LinuxHostProvider
from controller.host_capabilities import (
    HostCapabilities,
    build_host_capabilities,
)
from controller.resource_profiles import OrchestrationContractError


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def synthetic_provider(root: Path, *, affinity=(0, 1, 2, 3)) -> LinuxHostProvider:
    proc = root / "proc"
    sysroot = root / "sys"
    cgroup = root / "cgroup"
    write(proc / "self" / "cgroup", "0::/parent/child\n")
    write(proc / "meminfo", "MemTotal: 8388608 kB\n")
    levels = [
        (cgroup / "parent" / "child", "max 100000", "max"),
        (cgroup / "parent", "150000 100000", str(6 * 1024**3)),
        (cgroup, "max 100000", "max"),
    ]
    for path, cpu, mem in levels:
        write(path / "cpu.max", cpu + "\n")
        write(path / "memory.max", mem + "\n")
    write(cgroup / "parent" / "child" / "cpuset.cpus.effective", "0-3\n")
    topology = {0: (0, 0, "0-1"), 1: (0, 0, "0-1"), 2: (0, 1, "2-3"), 3: (0, 1, "2-3")}
    for cpu, (package, core, siblings) in topology.items():
        base = sysroot / "devices" / "system" / "cpu" / f"cpu{cpu}" / "topology"
        write(base / "physical_package_id", f"{package}\n")
        write(base / "core_id", f"{core}\n")
        write(base / "thread_siblings_list", siblings + "\n")
    return LinuxHostProvider(
        proc_root=proc,
        sys_root=sysroot,
        cgroup_root=cgroup,
        affinity_reader=lambda: affinity,
        cpu_count_reader=lambda: 8,
        platform_reader=lambda: ("linux", "x86_64"),
    )


class HostCapabilitiesTests(unittest.TestCase):
    def test_effective_capacity_uses_tightest_ancestor_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            capabilities = build_host_capabilities(
                synthetic_provider(Path(tmp)).observe_capabilities()
            )
        self.assertTrue(capabilities.capacity_complete)
        self.assertEqual(capabilities.allowed_cpus, (0, 1, 2, 3))
        self.assertEqual(capabilities.cpu_quota_status, "limited")
        self.assertEqual(capabilities.cpu_quota_equivalents, 1.5)
        self.assertEqual(capabilities.cgroup_memory_status, "limited")
        self.assertEqual(capabilities.cgroup_memory_limit_bytes, 6 * 1024**3)
        self.assertEqual(capabilities.effective_memory_limit_bytes, 6 * 1024**3)
        self.assertTrue(capabilities.topology_complete)
        self.assertEqual(capabilities.physical_core_count, 2)
        self.assertEqual(capabilities.smt_width, 2)
        self.assertEqual(capabilities.host_class, "linux/x86_64/cpu-4")
        self.assertTrue(capabilities.capability_id.startswith("host-cap/"))

    def test_round_trip_and_digest_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = build_host_capabilities(
                synthetic_provider(Path(tmp)).observe_capabilities()
            )
        restored = HostCapabilities.from_dict(item.as_dict())
        self.assertEqual(restored, item)
        self.assertEqual(restored.digest, item.digest)

    def test_capacity_change_changes_digest(self):
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = build_host_capabilities(
                synthetic_provider(Path(first_tmp), affinity=(0, 1, 2, 3)).observe_capabilities()
            )
            second = build_host_capabilities(
                synthetic_provider(Path(second_tmp), affinity=(0, 1)).observe_capabilities()
            )
        self.assertNotEqual(first.digest, second.digest)

    def test_missing_topology_does_not_invent_physical_cores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root)
            for cpu in range(4):
                path = (
                    root
                    / "sys"
                    / "devices"
                    / "system"
                    / "cpu"
                    / f"cpu{cpu}"
                    / "topology"
                    / "core_id"
                )
                path.unlink()
            item = build_host_capabilities(provider.observe_capabilities())
        self.assertTrue(item.capacity_complete)
        self.assertFalse(item.topology_complete)
        self.assertIsNone(item.physical_core_count)
        self.assertIsNone(item.smt_width)

    def test_empty_affinity_cpuset_intersection_fails_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root, affinity=(4, 5))
            item = build_host_capabilities(provider.observe_capabilities())
        self.assertFalse(item.capacity_complete)
        self.assertIsNone(item.allowed_cpus)
        self.assertEqual(item.host_class, "unknown")

    def test_unknown_serialized_field_or_authority_escalation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = build_host_capabilities(
                synthetic_provider(Path(tmp)).observe_capabilities()
            )
        raw = item.as_dict()
        hidden = copy.deepcopy(raw)
        hidden["authorized_move"] = "e2e4"
        with self.assertRaises(OrchestrationContractError):
            HostCapabilities.from_dict(hidden)
        elevated = copy.deepcopy(raw)
        elevated["authority"]["resource_authorization"] = True
        with self.assertRaises(OrchestrationContractError):
            HostCapabilities.from_dict(elevated)


if __name__ == "__main__":
    unittest.main()
