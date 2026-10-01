#!/usr/bin/env python3
"""J10 adaptive router / transactional bundle tests."""

from __future__ import annotations

import time
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_position_command
from controller.adaptive_resource_router import AdaptiveResourceRouter
from controller.adaptive_time import AdaptiveTimeSettings, build_move_resource_plan
from controller.budget import ResourceEnvelope
from controller.decision import canonical_digest
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities
from controller.online_time import ClockSearch, OnlineTimeSettings, make_time_plan
from controller.resource_allocator import (
    AllocationDecision,
    DeterministicAdaptiveAllocator,
    STOP_BUYING,
    STAGED_BUNDLE_ID,
    load_allocation_policy,
)
from controller.resource_profile_catalog import load_resource_profile_catalog
from controller.routing import RoutingPolicy
from controller.work_scheduler import WorkSchedulerSettings, build_work_scheduler


RESOURCE_CATALOG = load_resource_profile_catalog(
    ROOT / "qualification/resource-profile-catalog-v1.json"
)


def host() -> HostCapabilities:
    allowed = (0, 1, 2, 3)
    memory = 8 * 1024**3
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="j10-router-host",
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


def parent():
    started = time.monotonic()
    base = make_time_plan(
        command="go movetime 3500",
        position=parse_position_command("position startpos"),
        generation=9,
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
        received_monotonic=started,
        controller_cpu_started_ns=100,
    )
    plan = build_move_resource_plan(
        baseline=base,
        settings=AdaptiveTimeSettings(
            allocator_policy_id="adaptive-resource-v1"
        ),
        host=host(),
        composition=RESOURCE_CATALOG.default_composition,
        catalog_id=RESOURCE_CATALOG.catalog_id,
        catalog_digest=RESOURCE_CATALOG.digest,
    )
    return base, plan


class FakeContext:
    def __init__(self, base, plan):
        self.run_id = "j10-router-run"
        self.generation = base.generation
        self.position = parse_position_command("position startpos")
        self.external_go_command = base.external_go_command
        self.started_monotonic = base.received_monotonic
        self.clock = ClockSearch(base)
        self.move_resource_plan = plan

    def elapsed_ms(self):
        return max(
            0.0,
            (time.monotonic() - self.started_monotonic) * 1000.0,
        )

    def anchor_threads(self):
        return 1


def make_router():
    scheduler = build_work_scheduler(
        settings=WorkSchedulerSettings(),
        root=ROOT,
    )
    allocation_policy = load_allocation_policy(
        ROOT / "qualification/adaptive-resource-allocation-v1.json"
    )
    allocator = DeterministicAdaptiveAllocator(
        policy=allocation_policy,
        staged_model=None,
        regime_model_id=None,
    )
    return AdaptiveResourceRouter(
        envelope=ResourceEnvelope(
            wall_ms=4000,
            cpu_ms=12000,
            verification_reserve_fraction=0.3,
            controller_overhead_reserve_ms=250,
        ),
        policy=RoutingPolicy.from_config(
            {
                "policy": "conservative_v1",
                "anchor_cpu_ms_estimate": 0,
                "stage_cpu_ms_estimate": 100,
                "checkpoint_interval_ms": 10,
                "max_stages_per_owner": 1,
            }
        ),
        verify_enabled=True,
        refine_enabled=False,
        work_scheduler=scheduler,
        allocator=allocator,
        staged_model=None,
        regime_model=None,
    )


class AdaptiveResourceRouterTests(unittest.TestCase):
    def _buy_decision(self, router, context):
        decision = router.allocator.decide(
            move_plan=context.move_resource_plan,
            feature_digest="a" * 64,
            budget_snapshot_digest=canonical_digest(
                router.ledger.snapshot()
            ),
            estimate=None,
            classification=None,
        )
        router._allocation_decisions[context.run_id] = decision
        return decision

    def test_round2_grants_bind_allocation_decision(self):
        base, plan = parent()
        context = FakeContext(base, plan)
        router = make_router()
        router.on_run_start(context)
        decision = self._buy_decision(router, context)

        grants = []
        for owner, instance in (
            ("stockfish", "stockfish-shadow"),
            ("reckless", "reckless-shadow"),
            ("lc0", "lc0-shadow"),
        ):
            profile = RESOURCE_CATALOG.profile_for_instance(instance)
            options = RESOURCE_CATALOG.startup_options(profile.profile_id)
            options.update(
                RESOURCE_CATALOG.phase_options(profile.profile_id).get(
                    "STAGED_VERIFY",
                    {},
                )
            )
            grant = router.propose_work_grant(
                context,
                owner=owner,
                instance=instance,
                phase="STAGED_VERIFY",
                allocation_round=2,
                effective_options_digest=canonical_digest(
                    dict(sorted(options.items()))
                ),
                target_id="staged_extension",
            )
            self.assertIsNotNone(grant)
            self.assertEqual(
                grant.allocator_decision_digest,
                decision.digest,
            )
            grants.append(
                (
                    grant,
                    f"search-{owner}",
                )
            )

        admissions = router.authorize_work_grant_bundle(
            context,
            items=tuple(grants),
        )
        self.assertIsNotNone(admissions)
        self.assertEqual(len(admissions), 3)
        # Anchor + 3 staged grants.
        self.assertEqual(
            router.ledger.snapshot()["open_reservations"],
            4,
        )
        for admission in admissions:
            router.release_work_grant(
                admission,
                reason="test cleanup",
            )
        self.assertEqual(
            router.ledger.snapshot()["open_reservations"],
            1,
        )

    def test_partial_selected_bundle_is_rejected_before_reservation(self):
        base, plan = parent()
        context = FakeContext(base, plan)
        router = make_router()
        router.on_run_start(context)
        self._buy_decision(router, context)

        owner, instance = "stockfish", "stockfish-shadow"
        profile = RESOURCE_CATALOG.profile_for_instance(instance)
        options = RESOURCE_CATALOG.startup_options(profile.profile_id)
        options.update(
            RESOURCE_CATALOG.phase_options(profile.profile_id).get(
                "STAGED_VERIFY",
                {},
            )
        )
        grant = router.propose_work_grant(
            context,
            owner=owner,
            instance=instance,
            phase="STAGED_VERIFY",
            allocation_round=2,
            effective_options_digest=canonical_digest(
                dict(sorted(options.items()))
            ),
        )
        self.assertIsNotNone(grant)
        admissions = router.authorize_work_grant_bundle(
            context,
            items=((grant, "search-stockfish"),),
        )
        self.assertIsNone(admissions)
        self.assertEqual(
            router.ledger.snapshot()["open_reservations"],
            1,
        )

    def test_bundle_denial_rolls_back_prior_reservations(self):
        base, plan = parent()
        context = FakeContext(base, plan)
        router = make_router()
        router.on_run_start(context)
        self._buy_decision(router, context)

        items = []
        for owner, instance in (
            ("stockfish", "stockfish-shadow"),
            ("reckless", "reckless-shadow"),
            ("lc0", "lc0-shadow"),
        ):
            profile = RESOURCE_CATALOG.profile_for_instance(instance)
            options = RESOURCE_CATALOG.startup_options(profile.profile_id)
            options.update(
                RESOURCE_CATALOG.phase_options(profile.profile_id).get(
                    "STAGED_VERIFY",
                    {},
                )
            )
            grant = router.propose_work_grant(
                context,
                owner=owner,
                instance=instance,
                phase="STAGED_VERIFY",
                allocation_round=2,
                effective_options_digest=canonical_digest(
                    dict(sorted(options.items()))
                ),
            )
            self.assertIsNotNone(grant)
            items.append((grant, f"search-{owner}"))

        bad_grant, bad_search = items[-1]
        items[-1] = (
            replace(
                bad_grant,
                allocator_decision_digest="f" * 64,
            ),
            bad_search,
        )
        admissions = router.authorize_work_grant_bundle(
            context,
            items=tuple(items),
        )
        self.assertIsNone(admissions)
        self.assertEqual(
            router.ledger.snapshot()["open_reservations"],
            1,
        )
        self.assertEqual(router._work_grant_reservations, {})
        self.assertFalse(
            any(key[0] == 2 for key in router._work_grant_keys)
        )

    def test_bundle_exception_rolls_back_prior_reservations(self):
        base, plan = parent()
        context = FakeContext(base, plan)
        router = make_router()
        router.on_run_start(context)
        self._buy_decision(router, context)

        items = []
        for owner, instance in (
            ("stockfish", "stockfish-shadow"),
            ("reckless", "reckless-shadow"),
            ("lc0", "lc0-shadow"),
        ):
            profile = RESOURCE_CATALOG.profile_for_instance(instance)
            options = RESOURCE_CATALOG.startup_options(profile.profile_id)
            options.update(
                RESOURCE_CATALOG.phase_options(profile.profile_id).get(
                    "STAGED_VERIFY",
                    {},
                )
            )
            grant = router.propose_work_grant(
                context,
                owner=owner,
                instance=instance,
                phase="STAGED_VERIFY",
                allocation_round=2,
                effective_options_digest=canonical_digest(
                    dict(sorted(options.items()))
                ),
            )
            self.assertIsNotNone(grant)
            items.append((grant, f"search-{owner}"))

        # Keep the selected owner/chunk set valid so J10 precheck passes, but
        # use an instance absent from the bound composition. J9 validation then
        # raises after earlier members were already reserved.
        bad_grant, bad_search = items[-1]
        items[-1] = (
            replace(bad_grant, instance="missing-shadow"),
            bad_search,
        )
        with self.assertRaises(Exception):
            router.authorize_work_grant_bundle(
                context,
                items=tuple(items),
            )
        self.assertEqual(
            router.ledger.snapshot()["open_reservations"],
            1,
        )
        self.assertEqual(router._work_grant_reservations, {})
        self.assertFalse(
            any(key[0] == 2 for key in router._work_grant_keys)
        )

    def test_stop_decision_cannot_produce_round2_grant(self):
        base, plan = parent()
        context = FakeContext(base, plan)
        router = make_router()
        router.on_run_start(context)
        buy = self._buy_decision(router, context)
        stop = AllocationDecision(
            policy_id=buy.policy_id,
            allocation_policy_digest=buy.allocation_policy_digest,
            generation=buy.generation,
            position_id=buy.position_id,
            move_resource_plan_id=buy.move_resource_plan_id,
            allocation_round=2,
            candidate_bundle_ids=(STAGED_BUNDLE_ID,),
            selected_bundle_id=None,
            action=STOP_BUYING,
            feature_digest=buy.feature_digest,
            budget_snapshot_digest=buy.budget_snapshot_digest,
            staged_model_id="test-model",
            regime_model_id="test-regime",
            change_probability=0.01,
            support=100,
            position_group_support=50,
            heldout_bucket_observed=True,
            regime_bucket="test-bucket",
            regime_in_domain=True,
            gates=(),
            reason="test stop",
        )
        router._allocation_decisions[context.run_id] = stop

        grant = router.propose_work_grant(
            context,
            owner="stockfish",
            instance="stockfish-shadow",
            phase="STAGED_VERIFY",
            allocation_round=2,
            effective_options_digest="a" * 64,
        )
        self.assertIsNone(grant)

    def test_round0_remains_j9_compat_decision(self):
        base, plan = parent()
        context = FakeContext(base, plan)
        router = make_router()
        router.on_run_start(context)
        decision = self._buy_decision(router, context)
        profile = RESOURCE_CATALOG.profile_for_instance("stockfish-shadow")
        options = RESOURCE_CATALOG.startup_options(profile.profile_id)
        options.update(
            RESOURCE_CATALOG.phase_options(profile.profile_id).get(
                "EXPLORE",
                {},
            )
        )
        grant = router.propose_work_grant(
            context,
            owner="stockfish",
            instance="stockfish-shadow",
            phase="EXPLORE",
            allocation_round=0,
            effective_options_digest=canonical_digest(
                dict(sorted(options.items()))
            ),
        )
        self.assertIsNotNone(grant)
        self.assertNotEqual(
            grant.allocator_decision_digest,
            decision.digest,
        )


if __name__ == "__main__":
    unittest.main()
