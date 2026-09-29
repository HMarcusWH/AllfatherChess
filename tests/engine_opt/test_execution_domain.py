#!/usr/bin/env python3
"""Destructive contracts for ENGINE-OPT execution-domain evidence."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import canonical_digest
from controller.host_capabilities import (
    HOST_CAPABILITIES_VERSION,
    HostCapabilities,
    NumaNodeObservation,
)
from controller.runtime_substrate import RuntimeSubstrate
from tools.engine_opt.domain import (
    ExecutionDomainError,
    execution_domain_from_probe,
    load_execution_domain,
    require_same_execution_domain,
    validate_execution_domain,
)


SOURCE = "1" * 40


def host(*, complete: bool = True, vendor: str = "AuthenticAMD") -> HostCapabilities:
    flags = ("avx", "avx2", "fpu", "sse", "sse2")
    kwargs = dict(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="linux-host-v2",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=4,
        affinity_cpus=(0, 1, 2, 3),
        cgroup_cpuset_effective=(0, 1, 2, 3),
        allowed_cpus=(0, 1, 2, 3),
        cpu_vendor_id=vendor,
        cpu_family=25 if vendor == "AuthenticAMD" else 6,
        cpu_model=1 if vendor == "AuthenticAMD" else 106,
        cpu_stepping=1,
        cpu_model_name=f"{vendor} test",
        cpu_microcode="0x1",
        cpu_flags_intersection=flags,
        cpu_feature_digest=canonical_digest(list(flags)),
        cpu_identity_complete=True,
        physical_core_count=2,
        smt_width=2,
        topology_complete=True,
        numa_nodes=(NumaNodeObservation(0, (0, 1, 2, 3)),),
        numa_complete=True,
        physical_memory_bytes=8 * 1024**3,
        cgroup_memory_limit_bytes=None,
        effective_memory_limit_bytes=8 * 1024**3,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        faults=(),
    )
    if complete:
        kwargs.update(
            cpu_quota_status="unlimited",
            cpu_quota_equivalents=None,
            cpu_quota_observations=(),
            cgroup_memory_status="unlimited",
            capacity_complete=True,
            qualification_domain_complete=True,
        )
    else:
        kwargs.update(
            cpu_quota_status="unknown",
            cpu_quota_equivalents=None,
            cpu_quota_observations=(),
            cgroup_memory_status="unknown",
            capacity_complete=False,
            qualification_domain_complete=False,
            faults=("cpu.max:root:OSError:2", "memory.max:root:OSError:2"),
        )
    return HostCapabilities(**kwargs)


def runtime(*, kernel: str = "6.17.0-test") -> RuntimeSubstrate:
    return RuntimeSubstrate.from_observation(
        os_id="ubuntu",
        os_version_id="24.04",
        kernel_release=kernel,
        architecture="x86_64",
        libc_name="glibc",
        libc_version="2.39",
        python_version="3.12.3",
        runner_image_os="ubuntu24",
        runner_image_version="20260920.314.1",
        openblas_package="libopenblas-dev=0.3.26",
        clock_ticks_per_second=100,
    )


def probe(*, host_obj: HostCapabilities | None = None, runtime_obj: RuntimeSubstrate | None = None) -> dict:
    h = host_obj or host()
    r = runtime_obj or runtime()
    return {
        "host_capabilities": h.as_dict(),
        "host_capability_id": h.capability_id,
        "host_qualification_domain_id": h.qualification_domain_id,
        "host_qualification_domain_digest": h.qualification_domain_digest,
        "runtime_substrate": r.as_dict(),
        "runtime_substrate_id": r.substrate_id,
        "runtime_substrate_digest": r.digest,
        "commit_sha": SOURCE,
    }


class ExecutionDomainTests(unittest.TestCase):
    def test_complete_host_uses_reusable_scope(self):
        domain = execution_domain_from_probe(probe())
        self.assertEqual(domain["binding_scope"], "reusable_host_domain")
        self.assertTrue(domain["generic_host_portability_eligible"])
        self.assertIsNotNone(domain["host_qualification_domain_id"])
        validate_execution_domain(domain, expected_source_commit=SOURCE)

    def test_incomplete_reusable_domain_is_valid_exact_host_evidence(self):
        domain = execution_domain_from_probe(probe(host_obj=host(complete=False)))
        self.assertEqual(domain["binding_scope"], "exact_host_observation")
        self.assertFalse(domain["generic_host_portability_eligible"])
        self.assertIsNone(domain["host_qualification_domain_id"])
        validate_execution_domain(domain, expected_source_commit=SOURCE)

    def test_same_exact_domain_passes(self):
        domain = execution_domain_from_probe(probe(host_obj=host(complete=False)))
        result = require_same_execution_domain(
            {"matrix": domain, "g3": copy.deepcopy(domain), "local1": copy.deepcopy(domain)},
            expected_source_commit=SOURCE,
        )
        self.assertEqual(result["binding_scope"], "exact_host_observation")
        self.assertEqual(len(result["members"]), 3)

    def test_host_mismatch_fails(self):
        left = execution_domain_from_probe(probe())
        right = execution_domain_from_probe(probe(host_obj=host(vendor="GenuineIntel")))
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"left": left, "right": right}, expected_source_commit=SOURCE)

    def test_runtime_mismatch_fails(self):
        left = execution_domain_from_probe(probe())
        right = execution_domain_from_probe(probe(runtime_obj=runtime(kernel="6.17.1-test")))
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"left": left, "right": right}, expected_source_commit=SOURCE)

    def test_tampered_ids_and_seals_fail(self):
        domain = execution_domain_from_probe(probe())
        bad = copy.deepcopy(domain)
        bad["execution_domain_id"] = "exec-domain/fabricated"
        with self.assertRaises(ExecutionDomainError):
            validate_execution_domain(bad, expected_source_commit=SOURCE)

        bad = copy.deepcopy(domain)
        bad["host_capability_id"] = "host-cap/fabricated"
        core = dict(bad)
        core.pop("content_sha256")
        bad["content_sha256"] = canonical_digest(core)
        with self.assertRaises(ExecutionDomainError):
            validate_execution_domain(bad, expected_source_commit=SOURCE)

    def test_unknown_field_fails_closed(self):
        domain = execution_domain_from_probe(probe())
        bad = copy.deepcopy(domain)
        bad["hidden_authority"] = True
        core = dict(bad)
        core.pop("content_sha256")
        bad["content_sha256"] = canonical_digest(core)
        with self.assertRaises(ExecutionDomainError):
            validate_execution_domain(bad, expected_source_commit=SOURCE)

    def test_source_mismatch_fails(self):
        domain = execution_domain_from_probe(probe())
        with self.assertRaises(ExecutionDomainError):
            validate_execution_domain(domain, expected_source_commit="2" * 40)

    def test_incomplete_domain_cannot_fabricate_reusable_identity(self):
        domain = execution_domain_from_probe(probe(host_obj=host(complete=False)))
        bad = copy.deepcopy(domain)
        bad["binding_scope"] = "reusable_host_domain"
        bad["host_qualification_domain_id"] = "host-domain/fabricated"
        core = dict(bad)
        core.pop("content_sha256")
        bad["content_sha256"] = canonical_digest(core)
        with self.assertRaises(ExecutionDomainError):
            validate_execution_domain(bad, expected_source_commit=SOURCE)

    def test_file_round_trip(self):
        domain = execution_domain_from_probe(probe())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "domain.json"
            path.write_text(json.dumps(domain), encoding="utf-8")
            loaded = load_execution_domain(path, expected_source_commit=SOURCE)
        self.assertEqual(loaded, domain)


if __name__ == "__main__":
    unittest.main()
