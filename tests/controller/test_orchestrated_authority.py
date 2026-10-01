#!/usr/bin/env python3
"""M14-J J12 composition and authority-boundary tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.adaptive_resource_router import project_allocation_decision
from controller.decision import (
    CLOCKED_AUTHORIZATION_POLICY,
    ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
    DecisionAuthorizationSnapshot,
    DecisionCandidate,
    DecisionEvidence,
    DecisionDisposition,
    DecisionProposal,
    OrchestrationAuthorityProvenance,
    VerificationTerminalEvidence,
    authorize_decision,
)
from controller.resource_allocator import (
    BUY_BUNDLE,
    FALLBACK,
    STOP_BUYING,
    STAGED_BUNDLE_ID,
    AllocationDecision,
    AllocationGate,
    load_allocation_policy,
)
from controller.runtime import RuntimeError as ControllerRuntimeError
from controller.runtime import load_runtime_config
from controller.routing import build_router


CONFIG = ROOT / "config/allfather.orchestrated-v1.validation.json"


def load_static_config(document: dict | None = None):
    raw = (
        json.loads(CONFIG.read_text(encoding="utf-8"))
        if document is None
        else document
    )
    for spec in raw.get("instances", {}).values():
        spec["binary"] = sys.executable
        spec.pop("fallback_glob", None)
    raw["root"] = "."
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "j12.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        return load_runtime_config(path)
    finally:
        directory.cleanup()


def evidence() -> DecisionEvidence:
    roots = ("e2e4", "d2d4", "g1f3")
    terminal = VerificationTerminalEvidence(
        verification_id="verify-v1:test",
        candidate_roots=roots,
        final_by_owner=(
            ("stockfish", "e2e4"),
            ("reckless", "e2e4"),
            ("lc0", "e2e4"),
        ),
        stage_disposition_by_owner=(
            ("stockfish", "completed"),
            ("reckless", "completed"),
            ("lc0", "completed"),
        ),
        run_disposition="completed",
        complete=True,
    )
    return DecisionEvidence(
        evidence_version="decision-evidence-v1",
        run_id="run",
        generation=7,
        position_id="pos",
        crossfeed_policy="typed_verify_refine_v1",
        crossfeed_view_digest="1" * 64,
        crossfeed_verification_complete=True,
        candidates=(
            DecisionCandidate("e2e4", "stockfish"),
            DecisionCandidate("d2d4", "reckless"),
            DecisionCandidate("g1f3", "lc0"),
        ),
        verification_terminal=terminal,
        evidence_faults=(),
    )


def proposal(ev: DecisionEvidence) -> DecisionProposal:
    return DecisionProposal(
        policy="unanimous_verify_v1",
        disposition=DecisionDisposition("PROPOSED", "test"),
        move="e2e4",
        source_owner="stockfish",
        evidence_digest=ev.digest,
        frozen_observed_ms=700.0,
        frozen_before_anchor=True,
    )


def provenance() -> OrchestrationAuthorityProvenance:
    return OrchestrationAuthorityProvenance(
        version="j11-orchestration-authority-binding-v1",
        move_resource_plan_id="move-plan/" + "2" * 64,
        move_resource_plan_digest="2" * 64,
        allocation_policy_digest="3" * 64,
        allocation_decision_digest="4" * 64,
        allocation_trace_digest="5" * 64,
        work_scheduler_catalog_digest="6" * 64,
        profile_catalog_digest="7" * 64,
        composition_profile_digest="8" * 64,
        game_environment_digest="9" * 64,
        host_capabilities_digest="a" * 64,
        work_grant_settlement_complete=True,
        open_work_grant_reservations=0,
    )


def snapshot(**changes) -> DecisionAuthorizationSnapshot:
    base = dict(
        run_id="run",
        generation=7,
        position_id="pos",
        request_class="online_time_v1",
        request_eligible=True,
        request_reason="current TimePlan",
        legal_roots=("e2e4", "d2d4", "g1f3"),
        external_root_restriction=(),
        anchor_request_bounded=True,
        anchor_reserved=True,
        budget_within_envelope=True,
        partitions_within_caps=True,
        wall_within_envelope=True,
        specialist_settlement_complete=True,
        open_specialist_reservations=0,
        open_solver_reservations=0,
        gpu_accounted=True,
        measurement_enabled=True,
        open_non_anchor_measurement_stages=0,
        measurement_provider_available=True,
        measurement_known_failure=False,
        backend_generation_current=True,
        controller_fallback_latched=False,
        time_plan_id="time-" + "b" * 64,
        time_plan_request_class="movetime_deadline_v1",
        time_plan_generation=7,
        time_plan_position_id="pos",
        time_plan_anchor_go_command="go movetime 1450",
        terminal_source="staged_verification",
        staged_complete=True,
        staged_intervention="same_process_staged_verify_v1",
        staged_generation=7,
        staged_candidate_roots=("e2e4", "d2d4", "g1f3"),
        route_action="BUY_STAGED_VERIFY",
        route_buy_extension=True,
        route_decision_digest="c" * 64,
        route_allocation_decision_digest="4" * 64,
        route_move_resource_plan_id="move-plan/" + "2" * 64,
        authority_evidence_frozen_before_soft_deadline=True,
        authority_blocked=False,
    )
    base.update(changes)
    return DecisionAuthorizationSnapshot(**base)


def allocation(action: str) -> AllocationDecision:
    policy = load_allocation_policy(
        ROOT / "qualification/adaptive-resource-allocation-v1.json"
    )
    return AllocationDecision(
        policy_id="adaptive_resource_v1",
        allocation_policy_digest=policy.digest,
        generation=7,
        position_id="pos",
        move_resource_plan_id="move-plan/" + "2" * 64,
        allocation_round=2,
        candidate_bundle_ids=(STAGED_BUNDLE_ID,),
        selected_bundle_id=STAGED_BUNDLE_ID if action == BUY_BUNDLE else None,
        action=action,
        feature_digest="d" * 64,
        budget_snapshot_digest="e" * 64,
        staged_model_id=None,
        regime_model_id=None,
        change_probability=None,
        support=None,
        position_group_support=None,
        heldout_bucket_observed=False,
        regime_bucket=None,
        regime_in_domain=False,
        gates=(AllocationGate("test", False, "test"),),
        reason="test",
    )


class OrchestratedAuthorityTests(unittest.TestCase):
    def test_historical_g3_policy_does_not_require_orchestration(self):
        ev = evidence()
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snapshot(),
            policy=CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertTrue(authorization.authorized)

    def test_j12_requires_j11_provenance(self):
        ev = evidence()
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snapshot(
                orchestration_host_provider_id="linux-host-v2",
                orchestration_host_capacity_claim=True,
                orchestration_host_qualification_domain_complete=False,
            ),
            policy=ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertFalse(authorization.authorized)
        self.assertIn("orchestration provenance", authorization.reason)

    def test_j12_rejects_synthetic_host_capacity(self):
        ev = evidence()
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snapshot(
                orchestration_provenance=provenance(),
                orchestration_host_provider_id=(
                    "j12-real-engines-synthetic-capacity"
                ),
                orchestration_host_capacity_claim=True,
                orchestration_host_qualification_domain_complete=False,
            ),
            policy=ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertFalse(authorization.authorized)
        self.assertIn("linux-host-v2", authorization.reason)

    def test_j12_authorizes_only_with_complete_real_orchestration(self):
        ev = evidence()
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snapshot(
                orchestration_provenance=provenance(),
                orchestration_host_provider_id="linux-host-v2",
                orchestration_host_capacity_claim=True,
                orchestration_host_qualification_domain_complete=False,
            ),
            policy=ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertTrue(authorization.authorized)
        self.assertEqual(authorization.move, "e2e4")

    def test_j12_rejects_route_from_different_allocation(self):
        ev = evidence()
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snapshot(
                orchestration_provenance=provenance(),
                orchestration_host_provider_id="linux-host-v2",
                orchestration_host_capacity_claim=True,
                orchestration_host_qualification_domain_complete=False,
                route_allocation_decision_digest="f" * 64,
            ),
            policy=ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertFalse(authorization.authorized)
        self.assertIn("AllocationDecision", authorization.reason)

    def test_j12_rejects_open_work_grant(self):
        ev = evidence()
        bad = replace(
            provenance(),
            work_grant_settlement_complete=False,
            open_work_grant_reservations=1,
        )
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snapshot(
                orchestration_provenance=bad,
                orchestration_host_provider_id="linux-host-v2",
                orchestration_host_capacity_claim=True,
                orchestration_host_qualification_domain_complete=False,
            ),
            policy=ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertFalse(authorization.authorized)
        self.assertIn("WorkGrant", authorization.reason)


class AllocationProjectionTests(unittest.TestCase):
    def test_projection_is_one_way_from_j10_allocation(self):
        buy = project_allocation_decision(allocation(BUY_BUNDLE))
        self.assertEqual(
            buy.version,
            "j12-allocation-route-projection-v1",
        )
        self.assertEqual(buy.action, "BUY_STAGED_VERIFY")
        self.assertTrue(buy.buy_extension)
        self.assertEqual(buy.allocation_action, BUY_BUNDLE)

        stop = project_allocation_decision(allocation(STOP_BUYING))
        self.assertEqual(stop.action, "SKIP_STAGED_VERIFY")
        self.assertFalse(stop.buy_extension)

        fallback = project_allocation_decision(allocation(FALLBACK))
        self.assertEqual(fallback.action, "FALLBACK_ANCHOR")
        self.assertFalse(fallback.buy_extension)


class RuntimeCompositionTests(unittest.TestCase):
    def test_shipped_j12_profile_composes_only_the_explicit_path(self):
        config = load_static_config()
        self.assertIsNotNone(config.orchestration)
        self.assertIsNotNone(config.work_scheduler)
        self.assertIsNotNone(config.resource_allocator)
        self.assertIsNotNone(config.crossfeed)
        self.assertIsNotNone(config.counterfactual)
        self.assertIsNotNone(config.hybrid_authority)
        self.assertEqual(
            config.hybrid_authority.policy,
            ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertEqual(config.routing["policy"], "conservative_v1")
        self.assertEqual(dict(config.shadow.dispatch_limit), {"nodes": 16})
        self.assertEqual(
            dict(config.verification.dispatch_limit),
            {"nodes": 16},
        )
        self.assertEqual(
            dict(config.verification.staged_extension.dispatch_limit),
            {"nodes": 32},
        )

    def _mutated(self, mutator) -> Path:
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        mutator(raw)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "j12.json"
        for spec in raw.get("instances", {}).values():
            spec["binary"] = sys.executable
            spec.pop("fallback_glob", None)
        raw["root"] = "."
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_router_projection_is_enabled_only_for_j12(self):
        def load_with_root(path: Path):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for spec in raw.get("instances", {}).values():
                spec["binary"] = sys.executable
                spec.pop("fallback_glob", None)
            raw["root"] = str(ROOT)
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            target = Path(directory.name) / path.name
            target.write_text(json.dumps(raw), encoding="utf-8")
            return load_runtime_config(target)

        j12 = build_router(load_with_root(CONFIG))
        self.assertTrue(j12.authority_route_projection_enabled)

        j10 = build_router(
            load_with_root(
                ROOT / "config/allfather.m14-j-j10.validation.json"
            )
        )
        self.assertFalse(j10.authority_route_projection_enabled)

    def test_old_g3_policy_cannot_be_smuggled_into_orchestration(self):
        path = self._mutated(
            lambda raw: raw["hybrid_authority"].update(
                {"policy": CLOCKED_AUTHORIZATION_POLICY}
            )
        )
        with self.assertRaises(ControllerRuntimeError):
            load_runtime_config(path)

    def test_j12_rejects_unified_value_as_second_allocator(self):
        path = self._mutated(
            lambda raw: raw["routing"].update({"policy": "unified_value_v1"})
        )
        with self.assertRaises(ControllerRuntimeError):
            load_runtime_config(path)

    def test_j12_requires_counterfactual_and_crossfeed(self):
        def mutate(raw):
            raw.pop("counterfactual")
        path = self._mutated(mutate)
        with self.assertRaises(ControllerRuntimeError):
            load_runtime_config(path)


if __name__ == "__main__":
    unittest.main()
