#!/usr/bin/env python3
"""Contract tests for M14-J J8 MoveResourcePlan."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_position_command
from controller.adaptive_time import AdaptiveTimeSettings, build_move_resource_plan
from controller.budget import ResourceEnvelope
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities
from controller.move_resource_plan import MoveResourcePlan
from controller.online_time import OnlineTimeSettings, make_time_plan
from controller.resource_profile_catalog import load_resource_profile_catalog
from controller.resource_profiles import OrchestrationContractError


CATALOG = load_resource_profile_catalog(
    ROOT / "qualification/resource-profile-catalog-v1.json"
)


def host(cpus: int = 4) -> HostCapabilities:
    allowed = tuple(range(cpus))
    memory = 8 * 1024**3
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="j8-test-host",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=cpus,
        affinity_cpus=allowed,
        cgroup_cpuset_effective=allowed,
        allowed_cpus=allowed,
        cpu_vendor_id=None,
        cpu_family=None,
        cpu_model=None,
        cpu_stepping=None,
        cpu_model_name=None,
        cpu_microcode=None,
        cpu_flags_intersection=(),
        cpu_feature_digest=None,
        cpu_identity_complete=False,
        cpu_quota_status="unlimited",
        cpu_quota_equivalents=None,
        cpu_quota_observations=(),
        physical_core_count=None,
        smt_width=None,
        topology_complete=False,
        numa_nodes=(),
        numa_complete=False,
        physical_memory_bytes=memory,
        cgroup_memory_status="unlimited",
        cgroup_memory_limit_bytes=None,
        effective_memory_limit_bytes=memory,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=True,
        qualification_domain_complete=False,
        faults=(),
    )


def baseline():
    return make_time_plan(
        command="go movetime 1200",
        position=parse_position_command("position startpos"),
        generation=3,
        settings=OnlineTimeSettings(
            max_move_ms=4000,
            stop_grace_ms=300,
            output_margin_ms=50,
            prepare_budget_ms=100,
            cpu_parallelism=4,
        ),
        envelope=ResourceEnvelope(
            wall_ms=4000,
            cpu_ms=12000,
            verification_reserve_fraction=0.3,
            controller_overhead_reserve_ms=250,
        ),
        received_monotonic=10.0,
        controller_cpu_started_ns=100,
    )


def plan() -> MoveResourcePlan:
    return build_move_resource_plan(
        baseline=baseline(),
        settings=AdaptiveTimeSettings(),
        host=host(),
        composition=CATALOG.default_composition,
        catalog_id=CATALOG.catalog_id,
        catalog_digest=CATALOG.digest,
    )


class MoveResourcePlanTests(unittest.TestCase):
    def test_round_trip_and_identity(self):
        first = plan()
        restored = MoveResourcePlan.from_dict(first.as_dict())
        self.assertEqual(restored, first)
        self.assertEqual(restored.plan_id, first.plan_id)
        self.assertTrue(first.plan_id.startswith("move-plan/"))
        self.assertTrue(first.host_capacity_claim)
        self.assertFalse(first.composition_qualification_established)
        self.assertFalse(first.generic_host_portability_established)
        raw = first.as_dict()
        self.assertFalse(raw["authority"]["resource_authorization"])
        self.assertFalse(raw["authority"]["outward_move"])
        self.assertFalse(raw["claim_boundary"]["work_grant"])
        self.assertFalse(raw["claim_boundary"]["runtime_profile_selection"])

    def test_claim_bearing_change_changes_plan_id(self):
        first = plan()
        raw = first.as_dict()
        changed = copy.deepcopy(raw)
        changed["resource_envelope"]["cpu_ms"] -= 1
        changed.pop("plan_id")
        changed_item = MoveResourcePlan(
            policy_id=changed["policy_id"],
            generation=changed["generation"],
            position_id=changed["position_id"],
            baseline_time_plan_id=changed["baseline_time_plan_id"],
            game_environment=first.game_environment,
            host_capabilities=first.host_capabilities,
            catalog_id=changed["catalog_id"],
            catalog_digest=changed["catalog_digest"],
            composition=first.composition,
            resource_envelope=ResourceEnvelope(
                wall_ms=changed["resource_envelope"]["wall_ms"],
                cpu_ms=changed["resource_envelope"]["cpu_ms"],
                gpu_ms=changed["resource_envelope"]["gpu_ms"],
                verification_reserve_fraction=changed["resource_envelope"]["verification_reserve_fraction"],
                refinement_reserve_fraction=changed["resource_envelope"]["refinement_reserve_fraction"],
                controller_overhead_reserve_ms=changed["resource_envelope"]["controller_overhead_reserve_ms"],
            ),
            soft_budget_ms=changed["soft_budget_ms"],
            hard_ceiling_ms=changed["hard_ceiling_ms"],
            prepare_budget_ms=changed["prepare_budget_ms"],
            output_margin_ms=changed["output_margin_ms"],
            effective_parallelism=changed["effective_parallelism"],
            allocator_policy_id=changed["allocator_policy_id"],
            disposition=changed["disposition"],
            fallback_policy=changed["fallback_policy"],
            fallback_profile=changed["fallback_profile"],
            fallback_reason=changed["fallback_reason"],
            host_capacity_claim=changed["host_capacity_claim"],
        )
        self.assertNotEqual(first.plan_id, changed_item.plan_id)

    def test_authority_and_claim_escalation_are_rejected(self):
        raw = plan().as_dict()
        for section, key in (
            ("authority", "resource_authorization"),
            ("authority", "outward_move"),
            ("claim_boundary", "work_grant"),
            ("claim_boundary", "runtime_profile_selection"),
        ):
            tampered = copy.deepcopy(raw)
            tampered[section][key] = True
            with self.subTest(section=section, key=key), self.assertRaises(
                OrchestrationContractError
            ):
                MoveResourcePlan.from_dict(tampered)

    def test_tampered_plan_id_is_rejected(self):
        raw = plan().as_dict()
        raw["plan_id"] = "move-plan/" + "f" * 64
        with self.assertRaises(OrchestrationContractError):
            MoveResourcePlan.from_dict(raw)

    def test_fallback_requires_reason_and_no_host_claim(self):
        base = baseline()
        item = build_move_resource_plan(
            baseline=base,
            settings=AdaptiveTimeSettings(),
            host=None,
            composition=CATALOG.default_composition,
            catalog_id=CATALOG.catalog_id,
            catalog_digest=CATALOG.digest,
        )
        self.assertEqual(item.disposition, "FALLBACK")
        self.assertEqual(item.fallback_reason, "HOST_DISCOVERY_UNAVAILABLE")
        self.assertFalse(item.host_capacity_claim)
        self.assertEqual(item.resource_envelope, base.envelope)


if __name__ == "__main__":
    unittest.main()
