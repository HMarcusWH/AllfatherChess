#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.host_capabilities import (
    HOST_CAPABILITIES_VERSION,
    HostCapabilities,
    NumaNodeObservation,
    canonical_digest,
)
from tools.local_game.common import QualificationError, ROOT, save, sha
from tools.meta1.common import RUN_DISPOSITION, campaign_disposition, policy
from tools.meta1.integrity import _verify_producer_summary, qualify
from tools.meta1.preflight import (
    validate_postflight_payload,
    validate_preflight_payload,
)


def complete_host(microcode: str = "1") -> HostCapabilities:
    flags = ("sse2",)
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="meta1-test-host",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=2,
        affinity_cpus=(0, 1),
        cgroup_cpuset_effective=(0, 1),
        allowed_cpus=(0, 1),
        cpu_vendor_id="GenuineIntel",
        cpu_family=6,
        cpu_model=1,
        cpu_stepping=1,
        cpu_model_name="META1 Test CPU",
        cpu_microcode=microcode,
        cpu_flags_intersection=flags,
        cpu_feature_digest=canonical_digest(list(flags)),
        cpu_identity_complete=True,
        cpu_quota_status="unlimited",
        cpu_quota_equivalents=None,
        cpu_quota_observations=(),
        physical_core_count=2,
        smt_width=1,
        topology_complete=True,
        numa_nodes=(NumaNodeObservation(node_id=0, cpus=(0, 1)),),
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


def positive_fixture():
    host = complete_host()
    source = {"commit": "a" * 40, "tree": "b" * 40}
    p = policy()
    bundle = {"source_commit": source["commit"], "marker": "bundle"}
    j12 = {
        "mechanism_valid": True,
        "authority_qualified": True,
        "qualification_disposition": "QUALIFIED_ORCHESTRATED_AUTHORITY",
        "synthetic_capacity_observation": False,
        "execution_domain": {
            "binding_scope": "exact_host_observation",
            "host_capabilities": host.as_dict(),
        },
    }
    payload = {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "source": source,
        "qualification_disposition": RUN_DISPOSITION,
        "policy_sha256": sha(ROOT / "qualification/meta-1-v1.json"),
        "runtime_config_sha256": sha(ROOT / p["source_runtime"]),
        "candidate_bundle": bundle,
        "host_capabilities": host.as_dict(),
        "host_capabilities_digest": host.digest,
        "qualification_domain_digest": host.qualification_domain_digest,
    }
    return host, source, p, bundle, j12, payload


class Meta1MutationTests(unittest.TestCase):
    def test_synthetic_authority_cannot_be_promoted(self):
        row = {
            "mechanism_valid": True,
            "authority_qualified": True,
            "qualification_disposition": "QUALIFIED_ORCHESTRATED_AUTHORITY",
            "synthetic_capacity_observation": True,
        }
        with self.assertRaises(QualificationError):
            campaign_disposition(row)

    def test_preflight_rejects_policy_bundle_disposition_and_host_digest_tampering(self):
        _, source, p, bundle, j12, payload = positive_fixture()
        validate_preflight_payload(
            payload,
            source=source,
            p=p,
            j12=j12,
            candidate_bundle=bundle,
        )
        mutations = [
            ("policy_sha256", "0" * 64),
            ("candidate_bundle", {"marker": "other"}),
            ("qualification_disposition", "NOT_QUALIFIED_HOST_CAPACITY"),
            ("host_capabilities_digest", "0" * 64),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                bad = copy.deepcopy(payload)
                bad[key] = value
                with self.assertRaises(QualificationError):
                    validate_preflight_payload(
                        bad,
                        source=source,
                        p=p,
                        j12=j12,
                        candidate_bundle=bundle,
                    )

    def test_postflight_host_domain_drift_is_rejected(self):
        host, source, _, _, _, payload = positive_fixture()
        changed = complete_host(microcode="2")
        post = {
            "schema_version": 1,
            "profile_id": "meta-1-v1",
            "source": source,
            "host_capabilities": changed.as_dict(),
            "host_capabilities_digest": changed.digest,
            "qualification_domain_digest": changed.qualification_domain_digest,
            "qualification_domain_stable": False,
        }
        self.assertNotEqual(
            host.qualification_domain_digest,
            changed.qualification_domain_digest,
        )
        with self.assertRaises(QualificationError):
            validate_postflight_payload(
                post,
                source=source,
                preflight=payload,
                require_stable=True,
            )

    def test_producer_summary_cannot_relabel_campaign(self):
        manifest = {
            "source": {"commit": "a" * 40, "tree": "b" * 40},
            "qualification_disposition": "NOT_QUALIFIED_HOST_CAPACITY",
            "status": "gated",
            "planned_blocks": [{} for _ in range(50)],
            "jobs": [],
            "failures": [],
        }
        forged = {
            "schema_version": 1,
            "profile_id": "meta-1-v1",
            "source": manifest["source"],
            "qualification_disposition": RUN_DISPOSITION,
            "status": "completed",
            "planned_blocks": 50,
            "blocks_executed": 50,
            "failures": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "producer.json"
            save(path, forged)
            with self.assertRaises(QualificationError):
                _verify_producer_summary(manifest, path)

    def test_nonrun_path_still_rejects_common_evidence_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "tools.meta1.integrity.verify_common_evidence",
                side_effect=QualificationError("forged non-run evidence"),
            ):
                report = qualify(Path(tmp))
        self.assertFalse(report["passed"])
        self.assertEqual(report["qualification_disposition"], "INVALID_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
