#!/usr/bin/env python3
"""M14-J J9 compatibility WorkGrant scheduler contracts."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_position_command
from controller.adaptive_time import AdaptiveTimeSettings, build_move_resource_plan
from controller.budget import BudgetExceeded, BudgetLedger, ResourceEnvelope
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities
from controller.online_time import OnlineTimeSettings, make_time_plan
from controller.resource_profile_catalog import load_resource_profile_catalog
from controller.work_scheduler import (
    WorkSchedulerDenied,
    WorkSchedulerSettings,
    build_work_scheduler,
    load_work_scheduler_catalog,
)


CATALOG_PATH = ROOT / "qualification/resource-profile-catalog-v1.json"
SCHEDULER_PATH = ROOT / "qualification/work-grant-scheduler-v1.json"
CATALOG = load_resource_profile_catalog(CATALOG_PATH)
SCHEDULER_CATALOG, _ = load_work_scheduler_catalog(SCHEDULER_PATH)
SCHEDULER = build_work_scheduler(
    settings=WorkSchedulerSettings(),
    root=ROOT,
)


def host(cpus: int = 4) -> HostCapabilities:
    allowed = tuple(range(cpus))
    memory = 8 * 1024**3
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="j9-test-host",
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


def parent_plan(*, observed_host=True):
    base = make_time_plan(
        command="go movetime 4000",
        position=parse_position_command("position startpos"),
        generation=4,
        settings=OnlineTimeSettings(
            max_move_ms=4000,
            network_reserve_ms=100,
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
        baseline=base,
        settings=AdaptiveTimeSettings(),
        host=host() if observed_host else None,
        composition=CATALOG.default_composition,
        catalog_id=CATALOG.catalog_id,
        catalog_digest=CATALOG.digest,
    )


class WorkSchedulerCatalogTests(unittest.TestCase):
    def test_frozen_catalog_is_exact_three_by_three_compatibility_grid(self):
        self.assertEqual(SCHEDULER_CATALOG.catalog_id, "work-grant-scheduler-v1")
        self.assertEqual(SCHEDULER_CATALOG.round_count, 3)
        self.assertEqual(SCHEDULER_CATALOG.max_grants_per_round, 3)
        self.assertFalse(SCHEDULER_CATALOG.cost_bound_claim)
        self.assertEqual(len(SCHEDULER_CATALOG.chunks), 9)
        self.assertEqual(len(SCHEDULER_CATALOG.licenses), 3)

        expected = {
            (0, family, "EXPLORE", 16)
            for family in ("stockfish", "reckless", "lc0")
        } | {
            (1, family, "VERIFY", 16)
            for family in ("stockfish", "reckless", "lc0")
        } | {
            (2, family, "STAGED_VERIFY", 32)
            for family in ("stockfish", "reckless", "lc0")
        }
        actual = {
            (
                row.allocation_round,
                row.chunk.family,
                row.chunk.phase,
                row.chunk.native_limit.value,
            )
            for row in SCHEDULER_CATALOG.chunks
        }
        self.assertEqual(actual, expected)
        for row in SCHEDULER_CATALOG.chunks:
            self.assertEqual(
                row.chunk.native_limit.semantics,
                f"{row.chunk.family}.uci_nodes",
            )
            self.assertEqual(row.chunk.reserved_gpu_ms, 0.0)
            self.assertEqual(row.chunk.wall_bound_ms, 4000.0)

    def test_j3_profiles_remain_frozen_with_no_direct_work_chunks(self):
        self.assertFalse(CATALOG.selection_enabled)
        self.assertEqual(CATALOG.fallback_profile, "engine-opt-v2")
        for profile_id in (
            "stockfish/anchor-engine-opt-v2",
            "stockfish/specialist-engine-opt-v2",
            "reckless/specialist-engine-opt-v2",
            "lc0/specialist-engine-opt-v2",
        ):
            self.assertEqual(CATALOG.profile(profile_id).work_chunk_ids, ())

    def test_scheduler_catalog_is_bound_to_current_j3_digest(self):
        self.assertEqual(
            SCHEDULER_CATALOG.resource_catalog_id,
            CATALOG.catalog_id,
        )
        self.assertEqual(
            SCHEDULER_CATALOG.resource_catalog_digest,
            CATALOG.digest,
        )
        self.assertEqual(
            dict(SCHEDULER_CATALOG.source_policy),
            CATALOG.legacy_policy,
        )


class WorkSchedulerGrantTests(unittest.TestCase):
    def _grant(self, *, owner, instance, phase, allocation_round):
        return SCHEDULER.create_grant(
            move_plan=parent_plan(),
            owner=owner,
            instance=instance,
            phase=phase,
            allocation_round=allocation_round,
            effective_options_digest="a" * 64,
            elapsed_ms=100.0,
        )

    def test_compatibility_grants_bind_parent_profile_license_and_round(self):
        cases = (
            ("stockfish", "stockfish-shadow", "EXPLORE", 0, 16),
            ("reckless", "reckless-shadow", "VERIFY", 1, 16),
            ("lc0", "lc0-shadow", "STAGED_VERIFY", 2, 32),
        )
        for owner, instance, phase, round_, nodes in cases:
            with self.subTest(owner=owner, phase=phase):
                item = self._grant(
                    owner=owner,
                    instance=instance,
                    phase=phase,
                    allocation_round=round_,
                )
                self.assertEqual(item.owner, owner)
                self.assertEqual(item.phase, phase)
                self.assertEqual(item.allocation_round, round_)
                self.assertEqual(item.native_limit.value, nodes)
                self.assertEqual(
                    item.move_resource_plan_id,
                    parent_plan().plan_id,
                )
                self.assertFalse(item.as_dict()["authority"]["outward_move"])
                self.assertEqual(len(item.work_chunk_license_digest), 64)

    def test_fallback_parent_cannot_generate_j9_work_grant(self):
        with self.assertRaises(WorkSchedulerDenied):
            SCHEDULER.create_grant(
                move_plan=parent_plan(observed_host=False),
                owner="stockfish",
                instance="stockfish-shadow",
                phase="EXPLORE",
                allocation_round=0,
                effective_options_digest="a" * 64,
                elapsed_ms=0.0,
            )

    def test_parent_catalog_and_composition_provenance_are_frozen(self):
        plan = parent_plan()
        with self.assertRaises(WorkSchedulerDenied):
            SCHEDULER.create_grant(
                move_plan=replace(plan, catalog_digest="f" * 64),
                owner="stockfish",
                instance="stockfish-shadow",
                phase="EXPLORE",
                allocation_round=0,
                effective_options_digest="a" * 64,
                elapsed_ms=100.0,
            )

        altered_composition = replace(
            plan.composition,
            expected_memory_mib=plan.composition.expected_memory_mib + 1,
        )
        with self.assertRaises(WorkSchedulerDenied):
            SCHEDULER.create_grant(
                move_plan=replace(plan, composition=altered_composition),
                owner="stockfish",
                instance="stockfish-shadow",
                phase="EXPLORE",
                allocation_round=0,
                effective_options_digest="a" * 64,
                elapsed_ms=100.0,
            )

    def test_wrong_round_phase_or_instance_is_rejected(self):
        plan = parent_plan()
        for kwargs in (
            dict(
                owner="stockfish",
                instance="stockfish-shadow",
                phase="VERIFY",
                allocation_round=0,
            ),
            dict(
                owner="lc0",
                instance="stockfish-shadow",
                phase="EXPLORE",
                allocation_round=0,
            ),
            dict(
                owner="stockfish",
                instance="stockfish-shadow",
                phase="EXPLORE",
                allocation_round=3,
            ),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(
                WorkSchedulerDenied
            ):
                SCHEDULER.create_grant(
                    move_plan=plan,
                    effective_options_digest="a" * 64,
                    elapsed_ms=100.0,
                    **kwargs,
                )

    def test_admission_validator_recomputes_frozen_grant_identity(self):
        plan = parent_plan()
        grant = SCHEDULER.create_grant(
            move_plan=plan,
            owner="stockfish",
            instance="stockfish-shadow",
            phase="EXPLORE",
            allocation_round=0,
            effective_options_digest="a" * 64,
            elapsed_ms=100.0,
        )
        SCHEDULER.validate_grant(move_plan=plan, grant=grant)

        for forged in (
            replace(grant, reserved_cpu_ms=1.0),
            replace(grant, work_chunk_license_digest="f" * 64),
            replace(grant, allocator_decision_digest="e" * 64),
        ):
            with self.subTest(grant=forged), self.assertRaises(
                WorkSchedulerDenied
            ):
                SCHEDULER.validate_grant(
                    move_plan=plan,
                    grant=forged,
                )

    def test_external_allocator_digest_is_allowed_only_when_expected(self):
        plan = parent_plan()
        digest = "d" * 64
        grant = SCHEDULER.create_grant(
            move_plan=plan,
            owner="lc0",
            instance="lc0-shadow",
            phase="STAGED_VERIFY",
            allocation_round=2,
            effective_options_digest="a" * 64,
            elapsed_ms=100.0,
            allocator_decision_digest=digest,
        )
        self.assertEqual(grant.allocator_decision_digest, digest)
        SCHEDULER.validate_grant(
            move_plan=plan,
            grant=grant,
            expected_allocator_decision_digest=digest,
        )
        with self.assertRaises(WorkSchedulerDenied):
            SCHEDULER.validate_grant(
                move_plan=plan,
                grant=grant,
            )
        with self.assertRaises(WorkSchedulerDenied):
            SCHEDULER.validate_grant(
                move_plan=plan,
                grant=grant,
                expected_allocator_decision_digest="e" * 64,
            )

    def test_grant_deadline_never_exceeds_parent_soft_deadline(self):
        plan = parent_plan()
        grant = SCHEDULER.create_grant(
            move_plan=plan,
            owner="lc0",
            instance="lc0-shadow",
            phase="STAGED_VERIFY",
            allocation_round=2,
            effective_options_digest="a" * 64,
            elapsed_ms=max(0.0, plan.soft_budget_ms - 50.0),
        )
        self.assertLessEqual(grant.wall_deadline_ms, plan.soft_budget_ms)

        with self.assertRaises(WorkSchedulerDenied):
            SCHEDULER.create_grant(
                move_plan=plan,
                owner="lc0",
                instance="lc0-shadow",
                phase="STAGED_VERIFY",
                allocation_round=2,
                effective_options_digest="a" * 64,
                elapsed_ms=float(plan.soft_budget_ms),
            )


class BudgetGrantProvenanceTests(unittest.TestCase):
    def test_grant_provenance_is_atomic_and_legacy_calls_still_work(self):
        ledger = BudgetLedger(
            ResourceEnvelope(wall_ms=1000, cpu_ms=1000),
            clock=lambda: 0.0,
            started=0.0,
        )
        legacy = ledger.reserve("legacy", cpu_ms=50)
        self.assertIsNone(legacy.grant_id)
        ledger.release(legacy)

        reservation = ledger.reserve(
            "grant",
            cpu_ms=100,
            grant_id="a" * 64,
            profile_id="stockfish/specialist-engine-opt-v2",
            allocator_decision_digest="b" * 64,
        )
        self.assertEqual(reservation.grant_id, "a" * 64)
        self.assertEqual(
            reservation.profile_id,
            "stockfish/specialist-engine-opt-v2",
        )
        self.assertEqual(reservation.allocator_decision_digest, "b" * 64)
        ledger.release(reservation)

        with self.assertRaises(Exception):
            ledger.reserve(
                "partial",
                cpu_ms=10,
                grant_id="c" * 64,
            )

    def test_concurrent_grant_reservations_cannot_exceed_envelope(self):
        ledger = BudgetLedger(
            ResourceEnvelope(wall_ms=1000, cpu_ms=300),
            clock=lambda: 0.0,
            started=0.0,
        )
        admitted = []
        lock = threading.Lock()

        def worker(index):
            try:
                item = ledger.reserve(
                    f"grant-{index}",
                    cpu_ms=200,
                    grant_id=f"{index + 1:064x}",
                    profile_id="stockfish/specialist-engine-opt-v2",
                    allocator_decision_digest=f"{index + 100:064x}",
                )
            except BudgetExceeded:
                return
            with lock:
                admitted.append(item)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(admitted), 1)
        self.assertTrue(ledger.within_envelope())


class J9ConfigTests(unittest.TestCase):
    def test_j9_runtime_preserves_j7_nonconsumption_and_authority_boundary(self):
        doc = json.loads(
            (ROOT / "config/allfather.m14-j-j9.validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(doc["routing"]["policy"], "conservative_v1")
        self.assertIsNone(doc.get("hybrid_authority"))
        self.assertIsNone(doc.get("refinement"))
        self.assertIsNone(doc.get("crossfeed"))
        self.assertIsNone(doc.get("counterfactual"))
        self.assertEqual(doc["shadow"]["dispatch_limit"], {"nodes": 16})
        self.assertEqual(doc["verification"]["dispatch_limit"], {"nodes": 16})
        self.assertEqual(
            doc["verification"]["staged_extension"]["dispatch_limit"],
            {"nodes": 32},
        )
        stockfish = [
            spec
            for spec in doc["instances"].values()
            if spec["family"] == "stockfish"
        ]
        self.assertTrue(all(spec["options"]["Hash"] == 16 for spec in stockfish))
        lc0 = next(
            spec for spec in doc["instances"].values()
            if spec["family"] == "lc0"
        )
        self.assertEqual(lc0["options"]["MaxPrefetch"], 8)


if __name__ == "__main__":
    unittest.main()
