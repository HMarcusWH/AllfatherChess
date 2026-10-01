#!/usr/bin/env python3
"""M14-J J10 allocation contract tests."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_position_command
from controller.adaptive_time import AdaptiveTimeSettings, build_move_resource_plan
from controller.budget import ResourceEnvelope
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities
from controller.online_time import OnlineTimeSettings, make_time_plan
from controller.resource_allocator import (
    BUY_BUNDLE,
    FALLBACK,
    STOP_BUYING,
    AllocationPolicy,
    AllocationBundle,
    DeterministicAdaptiveAllocator,
    ResourceAllocatorError,
    STAGED_BUNDLE_ID,
    load_allocation_policy,
)
from controller.resource_profile_catalog import load_resource_profile_catalog
from controller.runtime import RuntimeError as ControllerRuntimeError, load_runtime_config
from controller.regimes import RegimeStatus, SearchRegime
from controller.staged_decision_calibration import StagedValueEstimate


CATALOG = load_resource_profile_catalog(
    ROOT / "qualification/resource-profile-catalog-v1.json"
)


def host() -> HostCapabilities:
    allowed = (0, 1, 2, 3)
    memory = 8 * 1024**3
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="j10-test-host",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=4,
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


def move_plan(*, adaptive: bool = True):
    baseline = make_time_plan(
        command="go movetime 3500",
        position=parse_position_command("position startpos"),
        generation=7,
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
    return build_move_resource_plan(
        baseline=baseline,
        settings=AdaptiveTimeSettings(
            allocator_policy_id="adaptive-resource-v1"
        ),
        host=host() if adaptive else None,
        composition=CATALOG.default_composition,
        catalog_id=CATALOG.catalog_id,
        catalog_digest=CATALOG.digest,
    )


@dataclass
class FakeDomain:
    bucket: str = "domain-bucket"
    in_domain: bool = True
    reason: str | None = None


class FakeClassification:
    def __init__(self, *, in_domain: bool = True, ood: bool = False):
        self.domain = FakeDomain(in_domain=in_domain)
        self._ood = ood

    def status_for(self, regime):
        if regime is SearchRegime.OUT_OF_DOMAIN:
            return (
                RegimeStatus.ACTIVE if self._ood else RegimeStatus.INACTIVE
            )
        return RegimeStatus.INACTIVE


class FakeStagedModel:
    model_id = "staged-model-test"

    def __init__(self, *, heldout: bool = True):
        self.evaluation = {
            "holdout": {
                "reliability": (
                    [
                        {
                            "bucket": "bucket-a",
                            "count": 4,
                            "in_domain_rows": 4,
                        }
                    ]
                    if heldout
                    else []
                )
            }
        }


def promoted_policy() -> AllocationPolicy:
    return AllocationPolicy(
        catalog_id="adaptive-resource-allocation-v1",
        policy_id="adaptive_resource_v1",
        work_scheduler_catalog_id="work-grant-scheduler-v1",
        work_scheduler_catalog_digest="a" * 64,
        bundle=AllocationBundle(
            bundle_id=STAGED_BUNDLE_ID,
            allocation_round=2,
            chunk_ids=(
                "compat/stockfish/staged-verify/n32",
                "compat/reckless/staged-verify/n32",
                "compat/lc0/staged-verify/n32",
            ),
        ),
        skip_max_change_probability=0.10,
        max_allocation_rounds=3,
        stop_promotion=True,
        promotion_reason="test promotion",
        minimum_independent_groups=32,
        calibration_independent_groups=32,
        calibration_corpus_id="j10-calibration-corpus-v1",
        staged_model_path="qualification/staged.json",
        regime_model_path="qualification/regime.json",
    )


class ResourceAllocatorTests(unittest.TestCase):
    def test_source_controlled_policy_is_buy_only(self):
        policy = load_allocation_policy(
            ROOT / "qualification/adaptive-resource-allocation-v1.json"
        )
        self.assertFalse(policy.stop_promotion)
        self.assertEqual(policy.calibration_independent_groups, 16)
        self.assertEqual(policy.minimum_independent_groups, 32)
        self.assertEqual(
            policy.bundle.bundle_id,
            STAGED_BUNDLE_ID,
        )

        allocator = DeterministicAdaptiveAllocator(
            policy=policy,
            staged_model=None,
            regime_model_id=None,
        )
        decision = allocator.decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=None,
            classification=None,
        )
        self.assertEqual(decision.action, BUY_BUNDLE)
        self.assertEqual(decision.selected_bundle_id, STAGED_BUNDLE_ID)
        self.assertEqual(decision.allocation_policy_digest, policy.digest)
        self.assertFalse(
            decision.as_dict()["authority"]["resource_authorization"]
        )
        self.assertFalse(decision.as_dict()["authority"]["outward_move"])

    def test_fallback_parent_never_creates_buy_authority(self):
        policy = load_allocation_policy(
            ROOT / "qualification/adaptive-resource-allocation-v1.json"
        )
        allocator = DeterministicAdaptiveAllocator(
            policy=policy,
            staged_model=None,
            regime_model_id=None,
        )
        decision = allocator.decide(
            move_plan=move_plan(adaptive=False),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=None,
            classification=None,
        )
        self.assertEqual(decision.action, FALLBACK)
        self.assertIsNone(decision.selected_bundle_id)

    def test_stop_requires_all_calibrated_gates(self):
        estimate = StagedValueEstimate(
            change_probability=0.05,
            support=10,
            position_group_support=5,
            in_domain=True,
            bucket="bucket-a",
            reason=None,
        )
        allocator = DeterministicAdaptiveAllocator(
            policy=promoted_policy(),
            staged_model=FakeStagedModel(heldout=True),
            regime_model_id="regime-model-test",
        )
        decision = allocator.decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=estimate,
            classification=FakeClassification(),
        )
        self.assertEqual(decision.action, STOP_BUYING)
        self.assertIsNone(decision.selected_bundle_id)
        self.assertTrue(all(gate.passed for gate in decision.gates))

        no_holdout = DeterministicAdaptiveAllocator(
            policy=promoted_policy(),
            staged_model=FakeStagedModel(heldout=False),
            regime_model_id="regime-model-test",
        ).decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=estimate,
            classification=FakeClassification(),
        )
        self.assertEqual(no_holdout.action, BUY_BUNDLE)

        ood = allocator.decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=estimate,
            classification=FakeClassification(ood=True),
        )
        self.assertEqual(ood.action, BUY_BUNDLE)

    def test_high_change_risk_buys_compute(self):
        allocator = DeterministicAdaptiveAllocator(
            policy=promoted_policy(),
            staged_model=FakeStagedModel(),
            regime_model_id="regime-model-test",
        )
        estimate = StagedValueEstimate(
            change_probability=0.40,
            support=10,
            position_group_support=5,
            in_domain=True,
            bucket="bucket-a",
            reason=None,
        )
        decision = allocator.decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=estimate,
            classification=FakeClassification(),
        )
        self.assertEqual(decision.action, BUY_BUNDLE)

    def test_promotion_below_independence_floor_is_rejected(self):
        with self.assertRaises(ResourceAllocatorError):
            AllocationPolicy(
                catalog_id="adaptive-resource-allocation-v1",
                policy_id="adaptive_resource_v1",
                work_scheduler_catalog_id="work-grant-scheduler-v1",
                work_scheduler_catalog_digest="a" * 64,
                bundle=AllocationBundle(
                    bundle_id=STAGED_BUNDLE_ID,
                    allocation_round=2,
                    chunk_ids=(
                        "compat/stockfish/staged-verify/n32",
                        "compat/reckless/staged-verify/n32",
                        "compat/lc0/staged-verify/n32",
                    ),
                ),
                skip_max_change_probability=0.10,
                max_allocation_rounds=3,
                stop_promotion=True,
                promotion_reason="bad promotion",
                minimum_independent_groups=32,
                calibration_independent_groups=16,
                calibration_corpus_id="j10-calibration-corpus-v1",
                staged_model_path="qualification/staged.json",
                regime_model_path="qualification/regime.json",
            )

    def test_policy_digest_changes_allocation_identity(self):
        policy = load_allocation_policy(
            ROOT / "qualification/adaptive-resource-allocation-v1.json"
        )
        changed = replace(
            policy,
            skip_max_change_probability=0.09,
        )
        first = DeterministicAdaptiveAllocator(
            policy=policy,
            staged_model=None,
            regime_model_id=None,
        ).decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=None,
            classification=None,
        )
        second = DeterministicAdaptiveAllocator(
            policy=changed,
            staged_model=None,
            regime_model_id=None,
        ).decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=None,
            classification=None,
        )
        self.assertNotEqual(
            first.allocation_policy_digest,
            second.allocation_policy_digest,
        )
        self.assertNotEqual(first.allocation_id, second.allocation_id)

    def test_j8_only_profile_rejects_adaptive_allocator_identity(self):
        raw = json.loads(
            (ROOT / "config/allfather.m14-j-j8.validation.json").read_text(
                encoding="utf-8"
            )
        )
        raw["orchestration"]["allocator_policy_id"] = "adaptive-resource-v1"
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=".j10-invalid-",
            dir=ROOT / "config",
            delete=False,
            encoding="utf-8",
        )
        path = Path(handle.name)
        try:
            json.dump(raw, handle)
            handle.close()
            with self.assertRaises(ControllerRuntimeError):
                load_runtime_config(path)
        finally:
            try:
                handle.close()
            except Exception:
                pass
            path.unlink(missing_ok=True)

    def test_decision_identity_changes_with_claim_bearing_fact(self):
        policy = load_allocation_policy(
            ROOT / "qualification/adaptive-resource-allocation-v1.json"
        )
        allocator = DeterministicAdaptiveAllocator(
            policy=policy,
            staged_model=None,
            regime_model_id=None,
        )
        first = allocator.decide(
            move_plan=move_plan(),
            feature_digest="b" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=None,
            classification=None,
        )
        second = allocator.decide(
            move_plan=move_plan(),
            feature_digest="d" * 64,
            budget_snapshot_digest="c" * 64,
            estimate=None,
            classification=None,
        )
        self.assertNotEqual(first.allocation_id, second.allocation_id)


if __name__ == "__main__":
    unittest.main()
