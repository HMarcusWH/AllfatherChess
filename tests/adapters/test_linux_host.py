#!/usr/bin/env python3
"""Linux host-provider parser and synthetic-tree tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_host import LinuxHostProvider, LinuxHostProviderError


PSI = (
    "some avg10=1.00 avg60=2.00 avg300=3.00 total=42\n"
    "full avg10=0.10 avg60=0.20 avg300=0.30 total=7\n"
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LinuxHostParserTests(unittest.TestCase):
    def test_cpu_list_parser(self):
        self.assertEqual(LinuxHostProvider.parse_cpu_list("0-3"), (0, 1, 2, 3))
        self.assertEqual(
            LinuxHostProvider.parse_cpu_list("0,2,4-7"),
            (0, 2, 4, 5, 6, 7),
        )
        for bad in ("1-0", "0,,1", "a", "0-1,1", "-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(LinuxHostProviderError):
                    LinuxHostProvider.parse_cpu_list(bad)

    def test_cpu_max_and_memory_limit_parsers(self):
        self.assertEqual(
            LinuxHostProvider.parse_cpu_max("max 100000"),
            (None, 100000),
        )
        self.assertEqual(
            LinuxHostProvider.parse_cpu_max("150000 100000"),
            (150000, 100000),
        )
        self.assertIsNone(LinuxHostProvider.parse_memory_limit("max"))
        self.assertEqual(LinuxHostProvider.parse_memory_limit("8589934592"), 8589934592)

    def test_psi_parser(self):
        item = LinuxHostProvider.parse_psi(PSI)
        self.assertEqual(item.some.avg10, 1.0)
        self.assertEqual(item.full.total_us, 7)
        with self.assertRaises(LinuxHostProviderError):
            LinuxHostProvider.parse_psi("some avg10=nan avg60=0 avg300=0 total=0")

    def test_cgroup_parser_rejects_non_unified_or_ambiguous_membership(self):
        self.assertEqual(
            LinuxHostProvider.parse_self_cgroup("0::/a/b\n"),
            "/a/b",
        )
        with self.assertRaises(LinuxHostProviderError):
            LinuxHostProvider.parse_self_cgroup("1:cpu:/x\n")
        with self.assertRaises(LinuxHostProviderError):
            LinuxHostProvider.parse_self_cgroup("0::/a\n0::/b\n")


class SyntheticLinuxHostTests(unittest.TestCase):
    def provider(self, root: Path, *, affinity=(0, 1, 2, 3), cpus=8):
        proc = root / "proc"
        sysroot = root / "sys"
        cgroup = root / "cgroup"
        return LinuxHostProvider(
            proc_root=proc,
            sys_root=sysroot,
            cgroup_root=cgroup,
            affinity_reader=lambda: affinity,
            cpu_count_reader=lambda: cpus,
            platform_reader=lambda: ("linux", "x86_64"),
        )

    def populate(
        self,
        root: Path,
        *,
        child_cpu="max 100000",
        parent_cpu="150000 100000",
        root_cpu="max 100000",
        child_mem="max",
        parent_mem=str(6 * 1024**3),
        root_mem="max",
        cpuset="0-3",
    ):
        proc = root / "proc"
        sysroot = root / "sys"
        cgroup = root / "cgroup"
        write(proc / "self" / "cgroup", "0::/parent/child\n")
        write(proc / "meminfo", "MemTotal:       8388608 kB\n")
        write(proc / "pressure" / "cpu", PSI)
        write(proc / "pressure" / "memory", PSI)

        levels = [
            (cgroup / "parent" / "child", child_cpu, child_mem),
            (cgroup / "parent", parent_cpu, parent_mem),
            (cgroup, root_cpu, root_mem),
        ]
        for path, cpu, mem in levels:
            write(path / "cpu.max", cpu + "\n")
            write(path / "memory.max", mem + "\n")
        leaf = cgroup / "parent" / "child"
        write(leaf / "cpuset.cpus.effective", cpuset + "\n")
        write(leaf / "cpu.pressure", PSI)
        write(leaf / "memory.pressure", PSI)
        write(leaf / "memory.current", str(1024**3) + "\n")

        # 0/1 share core 0, 2/3 share core 1.
        topology = {0: (0, 0, "0-1"), 1: (0, 0, "0-1"), 2: (0, 1, "2-3"), 3: (0, 1, "2-3")}
        for cpu, (package, core, siblings) in topology.items():
            base = sysroot / "devices" / "system" / "cpu" / f"cpu{cpu}" / "topology"
            write(base / "physical_package_id", f"{package}\n")
            write(base / "core_id", f"{core}\n")
            write(base / "thread_siblings_list", siblings + "\n")

    def test_provider_walks_ancestor_limits_and_effective_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.populate(root)
            facts = self.provider(root).observe_capabilities()
            self.assertEqual(facts.affinity_cpus, (0, 1, 2, 3))
            self.assertEqual(facts.cgroup_cpuset_effective, (0, 1, 2, 3))
            self.assertTrue(facts.cpu_max_complete)
            self.assertTrue(facts.memory_max_complete)
            self.assertEqual(
                [item.cgroup_path for item in facts.cpu_max_chain],
                ["parent/child", "parent", "root"],
            )
            self.assertEqual(
                [item.equivalent_cpus for item in facts.cpu_max_chain],
                [None, 1.5, None],
            )
            self.assertEqual(
                [item.limit_bytes for item in facts.memory_max_chain],
                [None, 6 * 1024**3, None],
            )
            self.assertTrue(facts.topology_complete)
            self.assertEqual(len(facts.topology), 4)

    def test_pressure_preserves_system_and_cgroup_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.populate(root)
            pressure = self.provider(root).observe_pressure()
            self.assertEqual(pressure.system_cpu.some.total_us, 42)
            self.assertEqual(pressure.cgroup_memory.full.total_us, 7)
            self.assertEqual(pressure.memory_current_bytes, 1024**3)

    def test_cgroup_path_escape_is_rejected_even_through_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / "proc"
            cgroup = root / "cgroup"
            outside = root / "outside"
            outside.mkdir()
            cgroup.mkdir()
            (cgroup / "escape").symlink_to(outside, target_is_directory=True)
            write(proc / "self" / "cgroup", "0::/escape\n")
            provider = self.provider(root)
            facts = provider.observe_capabilities()
            self.assertFalse(facts.cpu_max_complete)
            self.assertTrue(any("cgroup-path" in fault for fault in facts.faults))

    def test_affinity_cpuset_contradiction_is_preserved_as_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.populate(root, cpuset="4-5")
            facts = self.provider(root, affinity=(0, 1)).observe_capabilities()
            self.assertTrue(
                any("affinity-cpuset-empty" in fault for fault in facts.faults)
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "sched_getaffinity"),
        "live host discovery is Linux-specific",
    )
    def test_live_provider_observes_nonempty_affinity_without_mutation(self):
        facts = LinuxHostProvider().observe_capabilities()
        self.assertIsNotNone(facts.affinity_cpus)
        self.assertGreater(len(facts.affinity_cpus), 0)


if __name__ == "__main__":
    unittest.main()
