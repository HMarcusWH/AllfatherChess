#!/usr/bin/env python3
"""M14-J J11 independent orchestration-integrity tests."""

from __future__ import annotations

import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.budget import BudgetLedger, ResourceEnvelope
from controller.orchestration_integrity import (
    AUTHORITY_BINDING_VERSION,
    OrchestrationIntegrityError,
    allocation_trace_digest,
    build_authority_binding,
    replay_budget_journal,
)
from controller.resource_allocator import (
    BUY_BUNDLE,
    STAGED_BUNDLE_ID,
    AllocationDecision,
    AllocationGate,
    load_allocation_policy,
)
from tests.controller.test_move_resource_plan import plan as base_plan


SHA = "a" * 64


def envelope() -> ResourceEnvelope:
    return ResourceEnvelope(
        wall_ms=4000,
        cpu_ms=12000,
        gpu_ms=0,
        verification_reserve_fraction=0.3,
        controller_overhead_reserve_ms=250,
    )


def decision(move_plan_id: str) -> AllocationDecision:
    policy = load_allocation_policy(
        ROOT / "qualification/adaptive-resource-allocation-v1.json"
    )
    return AllocationDecision(
        policy_id="adaptive_resource_v1",
        allocation_policy_digest=policy.digest,
        generation=3,
        position_id=base_plan().position_id,
        move_resource_plan_id=move_plan_id,
        allocation_round=2,
        candidate_bundle_ids=(STAGED_BUNDLE_ID,),
        selected_bundle_id=STAGED_BUNDLE_ID,
        action=BUY_BUNDLE,
        feature_digest=SHA,
        budget_snapshot_digest="b" * 64,
        staged_model_id=None,
        regime_model_id=None,
        change_probability=None,
        support=None,
        position_group_support=None,
        heldout_bucket_observed=False,
        regime_bucket=None,
        regime_in_domain=False,
        gates=(AllocationGate("promotion", False, "unpromoted"),),
        reason="fail closed buy",
    )


class BudgetJournalTests(unittest.TestCase):
    def test_journal_replays_reserve_settle_release_and_controller_charge(self):
        ledger = BudgetLedger(envelope(), clock=lambda: 0.0)
        first = ledger.reserve(
            "grant/0/stockfish",
            cpu_ms=100,
            purpose="solver",
            grant_id="1" * 64,
            profile_id="stockfish/specialist-engine-opt-v2",
            allocator_decision_digest="2" * 64,
        )
        ledger.settle(
            first,
            actual_cpu_ms=80,
            cpu_source="measured",
        )
        second = ledger.reserve(
            "grant/1/reckless",
            cpu_ms=100,
            purpose="verify",
            grant_id="3" * 64,
            profile_id="reckless/specialist-engine-opt-v2",
            allocator_decision_digest="4" * 64,
        )
        ledger.release(second)
        ledger.charge_elapsed(
            "controller",
            cpu_ms=10,
            note="unit",
            purpose="controller",
        )

        journal = ledger.journal()
        self.assertEqual(
            [row["event"] for row in journal],
            ["reserve", "settle", "reserve", "release", "controller_charge"],
        )
        self.assertEqual([row["sequence"] for row in journal], [1, 2, 3, 4, 5])
        replayed = replay_budget_journal(
            journal,
            envelope=ledger.snapshot()["envelope"],
        )
        self.assertEqual(replayed["open_reservations"], 0)
        self.assertAlmostEqual(replayed["committed_cpu_ms"], 90.0)
        self.assertTrue(replayed["within_envelope"])
        self.assertTrue(replayed["within_partition_caps"])
        self.assertEqual(ledger.journal(), journal)
        self.assertEqual(ledger.journal_digest(), ledger.journal_digest())

    def test_replay_rejects_tampered_affordability_and_double_terminal_event(self):
        ledger = BudgetLedger(envelope(), clock=lambda: 0.0)
        reservation = ledger.reserve(
            "verify",
            cpu_ms=100,
            purpose="verify",
        )
        ledger.settle(reservation, actual_cpu_ms=90)
        journal = ledger.journal()

        too_large = copy.deepcopy(journal)
        too_large[0]["cpu_ms"] = 999999
        with self.assertRaisesRegex(
            OrchestrationIntegrityError,
            "not CPU-affordable",
        ):
            replay_budget_journal(
                too_large,
                envelope=ledger.snapshot()["envelope"],
            )

        duplicate = copy.deepcopy(journal)
        duplicate.append(
            {
                **copy.deepcopy(duplicate[-1]),
                "sequence": len(duplicate) + 1,
            }
        )
        with self.assertRaisesRegex(
            OrchestrationIntegrityError,
            "not open",
        ):
            replay_budget_journal(
                duplicate,
                envelope=ledger.snapshot()["envelope"],
            )


class AllocationReconstructionTests(unittest.TestCase):
    def test_allocation_decision_round_trip_reconstructs_identity(self):
        p = replace(base_plan(), allocator_policy_id="adaptive-resource-v1")
        item = decision(p.plan_id)
        restored = AllocationDecision.from_dict(item.as_dict())
        self.assertEqual(restored, item)
        self.assertEqual(restored.digest, item.digest)

        tampered = item.as_dict()
        tampered["action"] = "STOP_BUYING"
        with self.assertRaises(Exception):
            AllocationDecision.from_dict(tampered)

    def test_authority_binding_is_resource_evidence_only(self):
        p = replace(base_plan(), allocator_policy_id="adaptive-resource-v1")
        item = decision(p.plan_id)
        route = {
            "run_id": "run-3",
            "allocation_decisions": [item.as_dict()],
            "work_scheduler": {
                "policy_id": "legacy_fixed_workgrant_v1",
                "catalog_id": "work-grant-scheduler-v1",
                "catalog_digest": "c" * 64,
                "settlement_complete": True,
                "events": [],
            },
        }
        binding = build_authority_binding(move_plan=p, route=route)
        self.assertEqual(binding["version"], AUTHORITY_BINDING_VERSION)
        self.assertTrue(binding["work_grant_settlement_complete"])
        self.assertEqual(binding["open_work_grant_reservations"], 0)
        self.assertFalse(binding["authority"]["resource_authorization"])
        self.assertFalse(binding["authority"]["outward_move"])

        first = allocation_trace_digest(route)
        route["work_scheduler"]["events"].append(
            {"event": "authorize", "grant_id": "f" * 64, "granted": False}
        )
        self.assertNotEqual(first, allocation_trace_digest(route))


if __name__ == "__main__":
    unittest.main()
