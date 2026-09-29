#!/usr/bin/env python3
"""Contract tests for dynamic host pressure identity."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_host import LinuxHostProvider
from controller.host_capabilities import build_host_capabilities
from controller.host_pressure import HostPressure, discover_host_pressure
from controller.resource_profiles import OrchestrationContractError


PSI_LOW = "some avg10=0.00 avg60=0.10 avg300=0.20 total=10\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
PSI_HIGH = "some avg10=5.00 avg60=4.00 avg300=3.00 total=100\nfull avg10=1.00 avg60=1.00 avg300=1.00 total=20\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def provider(root: Path) -> LinuxHostProvider:
    proc = root / "proc"
    sysroot = root / "sys"
    cgroup = root / "cgroup"
    write(proc / "self" / "cgroup", "0::/g\n")
    write(proc / "meminfo", "MemTotal: 4194304 kB\n")
    write(proc / "pressure" / "cpu", PSI_LOW)
    write(proc / "pressure" / "memory", PSI_LOW)
    leaf = cgroup / "g"
    for path in (leaf, cgroup):
        write(path / "cpu.max", "max 100000\n")
        write(path / "memory.max", "max\n")
    write(leaf / "cpuset.cpus.effective", "0-1\n")
    write(leaf / "cpu.pressure", PSI_LOW)
    write(leaf / "memory.pressure", PSI_LOW)
    write(leaf / "memory.current", "123456\n")
    for cpu in (0, 1):
        base = sysroot / "devices" / "system" / "cpu" / f"cpu{cpu}" / "topology"
        write(base / "physical_package_id", "0\n")
        write(base / "core_id", f"{cpu}\n")
        write(base / "thread_siblings_list", f"{cpu}\n")
    return LinuxHostProvider(
        proc_root=proc,
        sys_root=sysroot,
        cgroup_root=cgroup,
        affinity_reader=lambda: (0, 1),
        cpu_count_reader=lambda: 2,
        platform_reader=lambda: ("linux", "x86_64"),
    )


class HostPressureTests(unittest.TestCase):
    def test_pressure_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = discover_host_pressure(provider(Path(tmp)))
        restored = HostPressure.from_dict(item.as_dict())
        self.assertEqual(restored, item)
        self.assertEqual(restored.digest, item.digest)
        self.assertTrue(item.system_complete)
        self.assertTrue(item.cgroup_complete)

    def test_pressure_change_does_not_change_capability_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = provider(root)
            capability_before = build_host_capabilities(p.observe_capabilities())
            pressure_before = discover_host_pressure(p)
            write(root / "proc" / "pressure" / "cpu", PSI_HIGH)
            write(root / "cgroup" / "g" / "cpu.pressure", PSI_HIGH)
            capability_after = build_host_capabilities(p.observe_capabilities())
            pressure_after = discover_host_pressure(p)
        self.assertEqual(capability_before.digest, capability_after.digest)
        self.assertNotEqual(pressure_before.digest, pressure_after.digest)

    def test_missing_pressure_is_explicit_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = provider(root)
            (root / "proc" / "pressure" / "cpu").unlink()
            item = discover_host_pressure(p)
        self.assertFalse(item.system_complete)
        self.assertIsNone(item.system_cpu)
        self.assertTrue(any("psi:system:cpu" in fault for fault in item.faults))

    def test_pressure_has_no_resource_or_move_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = discover_host_pressure(provider(Path(tmp)))
        raw = item.as_dict()
        self.assertFalse(raw["authority"]["resource_authorization"])
        self.assertFalse(raw["authority"]["outward_move"])
        tampered = copy.deepcopy(raw)
        tampered["authority"]["outward_move"] = True
        with self.assertRaises(OrchestrationContractError):
            HostPressure.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()
