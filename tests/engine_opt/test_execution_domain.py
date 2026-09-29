#!/usr/bin/env python3
"""Tests for execution-domain aggregation."""

from __future__ import annotations

import copy
import sys
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
from controller.runtime_substrate import (
    RUNTIME_SUBSTRATE_VERSION,
    LinkedLibraryIdentity,
    PackageIdentity,
    RuntimeSubstrate,
)
from tools.engine_opt.domain import (
    ExecutionDomainError,
    require_same_execution_domain,
)


def host(*, vendor="AuthenticAMD", model=1) -> HostCapabilities:
    flags = ("avx", "avx2", "fpu", "sse", "sse2")
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="linux-host-v2",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=2,
        affinity_cpus=(0, 1),
        cgroup_cpuset_effective=(0, 1),
        allowed_cpus=(0, 1),
        cpu_vendor_id=vendor,
        cpu_family=25 if vendor == "AuthenticAMD" else 6,
        cpu_model=model,
        cpu_stepping=1,
        cpu_model_name=f"{vendor} test",
        cpu_flags_intersection=flags,
        cpu_feature_digest=canonical_digest(list(flags)),
        cpu_identity_complete=True,
        cpu_quota_status="unlimited",
        cpu_quota_equivalents=None,
        cpu_quota_observations=(),
        physical_core_count=2,
        smt_width=1,
        topology_complete=True,
        numa_nodes=(NumaNodeObservation(0, (0, 1)),),
        numa_complete=True,
        physical_memory_bytes=8 * 1024**3,
        cgroup_memory_status="unlimited",
        cgroup_memory_limit_bytes=None,
        effective_memory_limit_bytes=8 * 1024**3,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=True,
        qualification_domain_complete=True,
        faults=(),
    )


def runtime(*, lib_hash="a" * 64) -> RuntimeSubstrate:
    packages = tuple(
        PackageIdentity(name, "1")
        for name in (
            "libopenblas-dev",
            "libopenblas0-pthread",
            "libstdc++6",
            "libgcc-s1",
            "libc6",
        )
    )
    return RuntimeSubstrate(
        version=RUNTIME_SUBSTRATE_VERSION,
        os_id="ubuntu",
        os_version="24.04",
        kernel_release="6.17.0-test",
        libc_name="glibc",
        libc_version="2.39",
        packages=packages,
        linked_libraries=(LinkedLibraryIdentity("libopenblas.so.0", lib_hash),),
        complete=True,
        faults=(),
    )


def domain(*, host_obj=None, runtime_obj=None):
    h = host_obj or host()
    r = runtime_obj or runtime()
    return {
        "complete": True,
        "host_capabilities": h.as_dict(),
        "host_capability_id": h.capability_id,
        "host_qualification_domain_id": h.qualification_domain_id,
        "runtime_substrate": r.as_dict(),
        "runtime_substrate_id": r.runtime_substrate_id,
    }


class ExecutionDomainTests(unittest.TestCase):
    def test_same_domain_passes(self):
        result = require_same_execution_domain(
            {"matrix": domain(), "hybrid": domain(), "local1": domain()}
        )
        self.assertIsNotNone(result["host_qualification_domain_id"])
        self.assertIsNotNone(result["runtime_substrate_id"])

    def test_host_mismatch_fails(self):
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain(
                {
                    "matrix": domain(),
                    "hybrid": domain(
                        host_obj=host(vendor="GenuineIntel", model=106)
                    ),
                }
            )

    def test_runtime_mismatch_fails(self):
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain(
                {
                    "matrix": domain(),
                    "hybrid": domain(runtime_obj=runtime(lib_hash="b" * 64)),
                }
            )

    def test_tampered_ids_fail_independent_reconstruction(self):
        bad = domain()
        bad["host_qualification_domain_id"] = "host-domain/fabricated"
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"matrix": bad})

        bad = domain()
        bad["runtime_substrate_id"] = "runtime-substrate/fabricated"
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"matrix": bad})

    def test_incomplete_or_missing_domain_fails(self):
        bad = domain()
        bad["complete"] = False
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"matrix": bad})
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"matrix": None})


if __name__ == "__main__":
    unittest.main()
