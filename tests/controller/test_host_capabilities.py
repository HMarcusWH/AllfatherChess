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
from controller.host_capabilities import HostCapabilities, build_host_capabilities
from controller.resource_profiles import OrchestrationContractError


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cpuinfo(
    cpus: tuple[int, ...],
    *,
    vendor: str,
    family: int,
    model: int,
    stepping: int,
    name: str,
    flags: str = "fpu sse sse2 avx avx2",
) -> str:
    blocks = []
    for cpu in cpus:
        blocks.append(
            "\n".join(
                [
                    f"processor: {cpu}",
                    f"vendor_id: {vendor}",
                    f"cpu family: {family}",
                    f"model: {model}",
                    f"stepping: {stepping}",
                    f"model name: {name}",
                    f"flags: {flags}",
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def synthetic_provider(
    root: Path,
    *,
    affinity=(0, 1, 2, 3),
    vendor="AuthenticAMD",
    family=25,
    model=1,
    stepping=1,
    model_name="AMD EPYC Test",
    cgroup_membership="/parent/child",
) -> LinuxHostProvider:
    proc = root / "proc"
    sysroot = root / "sys"
    cgroup = root / "cgroup"
    write(proc / "self" / "cgroup", f"0::{cgroup_membership}\n")
    write(proc / "meminfo", "MemTotal: 8388608 kB\n")
    write(
        proc / "cpuinfo",
        cpuinfo(
            tuple(range(8)),
            vendor=vendor,
            family=family,
            model=model,
            stepping=stepping,
            name=model_name,
        ),
    )

    relative = cgroup_membership.strip("/")
    leaf = cgroup / relative
    parent = leaf.parent
    levels = [
        (leaf, "max 100000", "max"),
        (parent, "150000 100000", str(6 * 1024**3)),
        (cgroup, "max 100000", "max"),
    ]
    seen: set[Path] = set()
    for path, cpu, mem in levels:
        if path in seen:
            continue
        seen.add(path)
        write(path / "cpu.max", cpu + "\n")
        write(path / "memory.max", mem + "\n")
    write(leaf / "cpuset.cpus.effective", "0-3\n")

    topology = {
        0: (0, 0, "0-1"),
        1: (0, 0, "0-1"),
        2: (0, 1, "2-3"),
        3: (0, 1, "2-3"),
        4: (0, 2, "4-5"),
        5: (0, 2, "4-5"),
        6: (0, 3, "6-7"),
        7: (0, 3, "6-7"),
    }
    for cpu, (package, core, siblings) in topology.items():
        base = (
            sysroot
            / "devices"
            / "system"
            / "cpu"
            / f"cpu{cpu}"
            / "topology"
        )
        write(base / "physical_package_id", f"{package}\n")
        write(base / "core_id", f"{core}\n")
        write(base / "thread_siblings_list", siblings + "\n")
    write(
        sysroot / "devices" / "system" / "node" / "node0" / "cpulist",
        "0-3\n",
    )
    write(
        sysroot / "devices" / "system" / "node" / "node1" / "cpulist",
        "4-7\n",
    )
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
        self.assertTrue(capabilities.qualification_domain_complete)
        self.assertEqual(capabilities.allowed_cpus, (0, 1, 2, 3))
        self.assertEqual(capabilities.cpu_quota_status, "limited")
        self.assertEqual(capabilities.cpu_quota_equivalents, 1.5)
        self.assertEqual(capabilities.cgroup_memory_status, "limited")
        self.assertEqual(
            capabilities.cgroup_memory_limit_bytes, 6 * 1024**3
        )
        self.assertEqual(
            capabilities.effective_memory_limit_bytes, 6 * 1024**3
        )
        self.assertTrue(capabilities.topology_complete)
        self.assertEqual(capabilities.physical_core_count, 2)
        self.assertEqual(capabilities.smt_width, 2)
        self.assertTrue(capabilities.cpu_identity_complete)
        self.assertEqual(capabilities.cpu_vendor_id, "AuthenticAMD")
        self.assertTrue(capabilities.numa_complete)
        self.assertEqual(len(capabilities.numa_nodes), 1)
        self.assertIsNotNone(capabilities.qualification_domain_id)
        self.assertIsNotNone(capabilities.qualification_domain_digest)
        self.assertEqual(len(capabilities.qualification_domain_digest), 64)
        self.assertTrue(capabilities.capability_id.startswith("host-cap/"))

    def test_round_trip_and_digest_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = build_host_capabilities(
                synthetic_provider(Path(tmp)).observe_capabilities()
            )
        restored = HostCapabilities.from_dict(item.as_dict())
        self.assertEqual(restored, item)
        self.assertEqual(restored.digest, item.digest)
        self.assertEqual(
            restored.qualification_domain_id,
            item.qualification_domain_id,
        )
        self.assertEqual(
            restored.qualification_domain_digest,
            item.qualification_domain_digest,
        )

    def test_capacity_change_changes_exact_digest(self):
        with (
            tempfile.TemporaryDirectory() as first_tmp,
            tempfile.TemporaryDirectory() as second_tmp,
        ):
            first = build_host_capabilities(
                synthetic_provider(
                    Path(first_tmp), affinity=(0, 1, 2, 3)
                ).observe_capabilities()
            )
            second = build_host_capabilities(
                synthetic_provider(
                    Path(second_tmp), affinity=(0, 1)
                ).observe_capabilities()
            )
        self.assertNotEqual(first.digest, second.digest)

    def test_cpu_microarchitecture_changes_qualification_domain(self):
        with (
            tempfile.TemporaryDirectory() as amd_tmp,
            tempfile.TemporaryDirectory() as intel_tmp,
        ):
            amd = build_host_capabilities(
                synthetic_provider(
                    Path(amd_tmp),
                    vendor="AuthenticAMD",
                    family=25,
                    model=1,
                    stepping=1,
                    model_name="AMD EPYC Test",
                ).observe_capabilities()
            )
            intel = build_host_capabilities(
                synthetic_provider(
                    Path(intel_tmp),
                    vendor="GenuineIntel",
                    family=6,
                    model=106,
                    stepping=6,
                    model_name="Intel Xeon Test",
                ).observe_capabilities()
            )
        self.assertEqual(len(amd.allowed_cpus), len(intel.allowed_cpus))
        self.assertNotEqual(
            amd.qualification_domain_id,
            intel.qualification_domain_id,
        )

    def test_heterogeneous_effective_cpu_flags_block_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root)
            text = (root / "proc" / "cpuinfo").read_text(encoding="utf-8")
            # Remove AVX2 from exactly one effective CPU block.
            blocks = text.strip().split("\n\n")
            blocks[1] = blocks[1].replace(
                "flags: fpu sse sse2 avx avx2",
                "flags: fpu sse sse2 avx",
            )
            (root / "proc" / "cpuinfo").write_text(
                "\n\n".join(blocks) + "\n",
                encoding="utf-8",
            )
            item = build_host_capabilities(
                provider.observe_capabilities()
            )
        self.assertFalse(item.cpu_identity_complete)
        self.assertFalse(item.qualification_domain_complete)
        self.assertIsNone(item.qualification_domain_id)
        self.assertTrue(
            any(
                "heterogeneous-feature-flags" in fault
                for fault in item.faults
            )
        )

    def test_heterogeneous_microcode_blocks_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root)
            text = (root / "proc" / "cpuinfo").read_text(encoding="utf-8")
            blocks = text.strip().split("\n\n")
            blocks[0] += "\nmicrocode: 0x1"
            blocks[1] += "\nmicrocode: 0x2"
            blocks[2] += "\nmicrocode: 0x1"
            blocks[3] += "\nmicrocode: 0x1"
            for index in range(4, len(blocks)):
                blocks[index] += "\nmicrocode: 0x1"
            (root / "proc" / "cpuinfo").write_text(
                "\n\n".join(blocks) + "\n",
                encoding="utf-8",
            )
            item = build_host_capabilities(
                provider.observe_capabilities()
            )
        self.assertFalse(item.cpu_identity_complete)
        self.assertFalse(item.qualification_domain_complete)
        self.assertIsNone(item.qualification_domain_id)
        self.assertTrue(
            any(
                "heterogeneous-microcode" in fault
                for fault in item.faults
            )
        )

    def test_cgroup_path_name_changes_exact_id_but_not_domain(self):
        with (
            tempfile.TemporaryDirectory() as first_tmp,
            tempfile.TemporaryDirectory() as second_tmp,
        ):
            first = build_host_capabilities(
                synthetic_provider(
                    Path(first_tmp),
                    cgroup_membership="/alpha/child",
                ).observe_capabilities()
            )
            second = build_host_capabilities(
                synthetic_provider(
                    Path(second_tmp),
                    cgroup_membership="/beta/child",
                ).observe_capabilities()
            )
        self.assertNotEqual(first.capability_id, second.capability_id)
        self.assertEqual(
            first.qualification_domain_id,
            second.qualification_domain_id,
        )

    def test_missing_topology_forms_distinct_incomplete_topology_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root)
            for cpu in range(4):
                (
                    root
                    / "sys"
                    / "devices"
                    / "system"
                    / "cpu"
                    / f"cpu{cpu}"
                    / "topology"
                    / "core_id"
                ).unlink()
            item = build_host_capabilities(
                provider.observe_capabilities()
            )
        self.assertTrue(item.capacity_complete)
        self.assertFalse(item.topology_complete)
        self.assertIsNone(item.physical_core_count)
        self.assertIsNone(item.smt_width)
        self.assertTrue(item.qualification_domain_complete)
        self.assertIsNotNone(item.qualification_domain_id)
        material = item.qualification_domain_material
        self.assertIsNotNone(material)
        self.assertFalse(material["topology"]["complete"])

    def test_missing_cpu_identity_blocks_domain_but_not_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root)
            (root / "proc" / "cpuinfo").unlink()
            item = build_host_capabilities(
                provider.observe_capabilities()
            )
        self.assertTrue(item.capacity_complete)
        self.assertFalse(item.cpu_identity_complete)
        self.assertFalse(item.qualification_domain_complete)
        self.assertIsNone(item.qualification_domain_id)

    def test_missing_numa_is_explicit_and_domain_remains_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root)
            node_root = root / "sys" / "devices" / "system" / "node"
            for node in node_root.iterdir():
                (node / "cpulist").unlink()
            item = build_host_capabilities(
                provider.observe_capabilities()
            )
        self.assertTrue(item.capacity_complete)
        self.assertTrue(item.cpu_identity_complete)
        self.assertFalse(item.numa_complete)
        self.assertTrue(item.qualification_domain_complete)
        material = item.qualification_domain_material
        self.assertIsNotNone(material)
        self.assertFalse(material["numa"]["complete"])

    def test_empty_affinity_cpuset_intersection_fails_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = synthetic_provider(root, affinity=(4, 5))
            item = build_host_capabilities(
                provider.observe_capabilities()
            )
        self.assertFalse(item.capacity_complete)
        self.assertIsNone(item.allowed_cpus)
        self.assertEqual(item.host_class, "unknown")

    def test_capacity_complete_cannot_be_forged(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = build_host_capabilities(
                synthetic_provider(Path(tmp)).observe_capabilities()
            )
        raw = item.as_dict()
        forged = copy.deepcopy(raw)
        forged["allowed_cpus"] = None
        forged["capacity_complete"] = True
        with self.assertRaises(OrchestrationContractError):
            HostCapabilities.from_dict(forged)

    def test_domain_complete_cannot_be_forged(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = build_host_capabilities(
                synthetic_provider(Path(tmp)).observe_capabilities()
            )
        raw = item.as_dict()
        forged = copy.deepcopy(raw)
        forged["cpu_identity_complete"] = False
        forged["qualification_domain_complete"] = True
        with self.assertRaises(OrchestrationContractError):
            HostCapabilities.from_dict(forged)

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
