#!/usr/bin/env python3
"""Contracts and destructive controls for M14-J J5 resource placement."""

from __future__ import annotations

import errno
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_affinity import LinuxAffinityProvider
from controller.decision import canonical_digest
from controller.host_capabilities import (
    HOST_CAPABILITIES_VERSION,
    HostCapabilities,
    NumaNodeObservation,
)
from controller.resource_control import (
    FALLBACK_CPU_QUOTA,
    FALLBACK_HOST_CPUSET_CHANGED,
    FALLBACK_INCOMPLETE_HOST,
    FALLBACK_INSUFFICIENT_CPU,
    FALLBACK_INSUFFICIENT_MEMORY,
    FALLBACK_SCHEDULED_REUSE_UNSUPPORTED,
    FALLBACK_UNSUPPORTED_ENFORCEMENT,
    READY,
    ResourceControlError,
    ResourceController,
    ResourceFallbackRequired,
    assess_resource_placement,
    build_affinity_layout,
)
from controller.resource_profiles import (
    AcceleratorKind,
    CompositionBinding,
    CompositionProfile,
    CompositionRole,
    EnforcementMode,
    EngineResourceProfile,
    ProcessIdentity,
    QualificationIdentity,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
DOMAIN_DIGEST = "d" * 64
DOMAIN_ID = f"exec-domain/{DOMAIN_DIGEST[:20]}"
COMMIT = "1" * 40


def qualification() -> QualificationIdentity:
    return QualificationIdentity(
        source_commit=COMMIT,
        evidence_sha256=SHA_C,
        evidence_id="j5-test",
        execution_domain_id=DOMAIN_ID,
        execution_domain_digest=DOMAIN_DIGEST,
        binding_scope="exact_host_observation",
    )


def profile(profile_id: str, family: str, slots: int = 1) -> EngineResourceProfile:
    return EngineResourceProfile(
        profile_id=profile_id,
        family=family,
        process_identity=ProcessIdentity(
            binary_sha256=SHA_A,
            artifacts=(),
            backend=None,
            args=(),
            environment=(),
        ),
        options=(),
        cpu_slots=slots,
        expected_memory_mib=64,
        accelerator=AcceleratorKind.CPU,
        accelerator_memory_mib=0,
        work_chunk_ids=(),
        qualification=qualification(),
    )


def profiles() -> dict[str, EngineResourceProfile]:
    return {
        "stockfish/anchor": profile("stockfish/anchor", "stockfish"),
        "stockfish/shadow": profile("stockfish/shadow", "stockfish"),
        "reckless/shadow": profile("reckless/shadow", "reckless"),
        "lc0/shadow": profile("lc0/shadow", "lc0"),
    }


def composition(
    *,
    enforcement: EnforcementMode = EnforcementMode.AFFINITY,
    declared_slots: int = 4,
    memory_mib: int = 256,
    scheduled_reuse: bool = False,
) -> CompositionProfile:
    if scheduled_reuse:
        groups = {
            "stockfish-anchor": "a",
            "stockfish-shadow": "a",
            "reckless-shadow": "b",
            "lc0-shadow": "b",
        }
    else:
        groups = {name: "move" for name in (
            "stockfish-anchor",
            "stockfish-shadow",
            "reckless-shadow",
            "lc0-shadow",
        )}
    rows = (
        CompositionBinding(
            instance="stockfish-anchor",
            role=CompositionRole.ANCHOR,
            family="stockfish",
            profile_id="stockfish/anchor",
            cpu_slots=1,
            concurrency_group=groups["stockfish-anchor"],
        ),
        CompositionBinding(
            instance="stockfish-shadow",
            role=CompositionRole.SPECIALIST,
            family="stockfish",
            profile_id="stockfish/shadow",
            cpu_slots=1,
            concurrency_group=groups["stockfish-shadow"],
        ),
        CompositionBinding(
            instance="reckless-shadow",
            role=CompositionRole.SPECIALIST,
            family="reckless",
            profile_id="reckless/shadow",
            cpu_slots=1,
            concurrency_group=groups["reckless-shadow"],
        ),
        CompositionBinding(
            instance="lc0-shadow",
            role=CompositionRole.SPECIALIST,
            family="lc0",
            profile_id="lc0/shadow",
            cpu_slots=1,
            concurrency_group=groups["lc0-shadow"],
        ),
    )
    return CompositionProfile(
        composition_id="composition/j5-test",
        declared_cpu_slots=declared_slots,
        expected_memory_mib=memory_mib,
        enforcement_required=enforcement,
        bindings=rows,
        qualification=qualification(),
    )


def host(
    *,
    cpus: tuple[int, ...] = (0, 1, 2, 3),
    capacity_complete: bool = True,
    quota_status: str = "unlimited",
    quota_equiv: float | None = None,
    memory_bytes: int = 8 * 1024**3,
) -> HostCapabilities:
    flags = ("avx", "avx2", "fpu", "sse", "sse2")
    cpu_identity_complete = True
    qualification_complete = capacity_complete and cpu_identity_complete
    physical = max(1, len(cpus))
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="linux-host-v2",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=len(cpus),
        affinity_cpus=cpus,
        cgroup_cpuset_effective=cpus,
        allowed_cpus=cpus,
        cpu_vendor_id="AuthenticAMD",
        cpu_family=25,
        cpu_model=1,
        cpu_stepping=1,
        cpu_model_name="AMD test",
        cpu_microcode="0x1",
        cpu_flags_intersection=flags,
        cpu_feature_digest=canonical_digest(list(flags)),
        cpu_identity_complete=cpu_identity_complete,
        cpu_quota_status=quota_status,
        cpu_quota_equivalents=quota_equiv,
        cpu_quota_observations=(),
        physical_core_count=physical,
        smt_width=1,
        topology_complete=True,
        numa_nodes=(NumaNodeObservation(0, cpus),),
        numa_complete=True,
        physical_memory_bytes=memory_bytes,
        cgroup_memory_status="unlimited" if capacity_complete else "unknown",
        cgroup_memory_limit_bytes=None,
        effective_memory_limit_bytes=memory_bytes,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=capacity_complete,
        qualification_domain_complete=qualification_complete,
        faults=() if capacity_complete else ("cpu.max:root:OSError:2",),
    )


def stat_line(pid: int, start: int) -> str:
    fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 4
    return f"{pid} (worker) " + " ".join(fields) + "\n"


def write_task(
    root: Path,
    pid: int,
    tid: int,
    *,
    process_start: int,
    task_start: int,
    children: str = "",
) -> None:
    proc = root / str(pid)
    task = proc / "task" / str(tid)
    task.mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text(stat_line(pid, process_start), encoding="utf-8")
    (task / "stat").write_text(stat_line(tid, task_start), encoding="utf-8")
    (task / "children").write_text(children, encoding="utf-8")


class FakeAffinity:
    def __init__(self, mapping: dict[int, set[int]], host_cpus=(0,1,2,3)):
        self.mapping = {key:set(value) for key,value in mapping.items()}
        self.host_cpus = set(host_cpus)
        self.set_calls: list[tuple[int, tuple[int, ...]]] = []

    def get(self, tid: int):
        if tid == 0:
            return set(self.host_cpus)
        if tid not in self.mapping:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        return set(self.mapping[tid])

    def set(self, tid: int, cpus: set[int]):
        if tid not in self.mapping:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        self.mapping[tid] = set(cpus)
        self.set_calls.append((tid, tuple(sorted(cpus))))


class ResourceControlTests(unittest.TestCase):
    def test_affinity_layout_is_deterministic_anchor_first_and_disjoint(self):
        first = build_affinity_layout(host(), composition(), profiles())
        second = build_affinity_layout(host(), composition(), profiles())
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(first.digest, second.digest)
        by_instance = {row.instance: row.cpu_set for row in first.bindings}
        self.assertEqual(by_instance["stockfish-anchor"], (0,))
        self.assertEqual(by_instance["lc0-shadow"], (1,))
        self.assertEqual(by_instance["reckless-shadow"], (2,))
        self.assertEqual(by_instance["stockfish-shadow"], (3,))
        flattened = [cpu for row in first.bindings for cpu in row.cpu_set]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertFalse(first.as_dict()["authority"]["outward_move"])

    def test_capacity_fallbacks_are_explicit(self):
        cases = [
            (
                host(cpus=(0,1,2)),
                composition(),
                FALLBACK_INSUFFICIENT_CPU,
            ),
            (
                host(capacity_complete=False, quota_status="unknown"),
                composition(),
                FALLBACK_INCOMPLETE_HOST,
            ),
            (
                host(quota_status="limited", quota_equiv=3.5),
                composition(),
                FALLBACK_CPU_QUOTA,
            ),
            (
                host(memory_bytes=128 * 1024**2),
                composition(memory_mib=256),
                FALLBACK_INSUFFICIENT_MEMORY,
            ),
            (
                host(),
                composition(enforcement=EnforcementMode.CGROUP_V2),
                FALLBACK_UNSUPPORTED_ENFORCEMENT,
            ),
            (
                host(),
                composition(declared_slots=2, scheduled_reuse=True),
                FALLBACK_SCHEDULED_REUSE_UNSUPPORTED,
            ),
        ]
        for h, comp, disposition in cases:
            with self.subTest(disposition=disposition):
                result = assess_resource_placement(h, comp, profiles())
                self.assertFalse(result.ready)
                self.assertTrue(result.fallback_required)
                self.assertEqual(result.disposition, disposition)
                self.assertFalse(result.as_dict()["authority"]["outward_move"])

    def test_observed_mode_never_writes_affinity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            controller=ResourceController(
                host=host(capacity_complete=False, quota_status="unknown"),
                composition=composition(enforcement=EnforcementMode.OBSERVED),
                profiles=profiles(),
                provider=provider,
            )
            self.assertEqual(controller.assessment.disposition, READY)
            evidence=controller.place_instance("stockfish-anchor",100)
        self.assertEqual(fake.set_calls, [])
        self.assertFalse(evidence.enforced)
        self.assertFalse(evidence.isolated)
        self.assertTrue(evidence.success)

    def test_affinity_mode_pins_and_verifies_task_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            controller=ResourceController(
                host=host(),
                composition=composition(),
                profiles=profiles(),
                provider=provider,
            )
            evidence=controller.place_instance("stockfish-anchor",100)
            self.assertEqual(evidence.requested_cpu_set,(0,))
            self.assertEqual(fake.mapping[100],{0})
            verified=controller.verify_instance("stockfish-anchor",100)
        self.assertTrue(verified.success)
        self.assertTrue(verified.enforced)
        self.assertFalse(verified.isolated)

    def test_host_cpuset_change_requires_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            controller=ResourceController(
                host=host(),
                composition=composition(),
                profiles=profiles(),
                provider=provider,
            )
            fake.host_cpus={0,1,2}
            with self.assertRaises(ResourceFallbackRequired) as caught:
                controller.place_instance("stockfish-anchor",100)
        self.assertEqual(
            caught.exception.assessment.disposition,
            FALLBACK_HOST_CPUSET_CHANGED,
        )

    def test_effective_affinity_mismatch_fails_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            controller=ResourceController(
                host=host(),
                composition=composition(),
                profiles=profiles(),
                provider=provider,
            )
            controller.place_instance("stockfish-anchor",100)
            fake.mapping[100]={0,1}
            with self.assertRaises(ResourceControlError):
                controller.verify_instance("stockfish-anchor",100)
            evidence=controller.resource_state("stockfish-anchor")
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertFalse(evidence.success)
        self.assertTrue(evidence.faults)

    def test_pid_reuse_identity_invalidates_previous_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            controller=ResourceController(
                host=host(),
                composition=composition(),
                profiles=profiles(),
                provider=provider,
            )
            controller.place_instance("stockfish-anchor",100)
            (root/"100"/"stat").write_text(stat_line(100,99),encoding="utf-8")
            (root/"100"/"task"/"100"/"stat").write_text(
                stat_line(100,99),encoding="utf-8"
            )
            with self.assertRaises(ResourceControlError):
                controller.verify_instance("stockfish-anchor",100)

    def test_evidence_contains_no_resource_or_move_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0}})
            provider=LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            controller=ResourceController(
                host=host(capacity_complete=False, quota_status="unknown"),
                composition=composition(enforcement=EnforcementMode.OBSERVED),
                profiles=profiles(),
                provider=provider,
            )
            raw=controller.place_instance("stockfish-anchor",100).as_dict()
        self.assertFalse(raw["authority"]["resource_authorization"])
        self.assertFalse(raw["authority"]["outward_move"])


if __name__ == "__main__":
    unittest.main()
