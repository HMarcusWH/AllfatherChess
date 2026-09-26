#!/usr/bin/env python3
from __future__ import annotations
import copy
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import (
    CLOCKED_AUTHORIZATION_POLICY,
    DecisionAuthorizationSnapshot,
    DecisionCandidate,
    DecisionEvidence,
    DecisionDisposition,
    DecisionProposal,
    VerificationTerminalEvidence,
    authorize_decision,
)
from controller.online_hybrid_profile import (
    OnlineHybridProfileError,
    load_json,
    validate_online_hybrid_profile,
)

POLICY = ROOT / "qualification/online-hybrid-authority.json"
CONFIG = ROOT / "config/allfather.online-hybrid.validation.json"
ONLINE2 = ROOT / "config/allfather.online.cpu-reference.json"

def evidence() -> DecisionEvidence:
    roots = ("e2e4", "d2d4", "g1f3")
    terminal = VerificationTerminalEvidence(
        verification_id="verify-v1:test",
        candidate_roots=roots,
        final_by_owner=(("stockfish", "e2e4"), ("reckless", "e2e4"), ("lc0", "e2e4")),
        stage_disposition_by_owner=(("stockfish", "completed"), ("reckless", "completed"), ("lc0", "completed")),
        run_disposition="completed",
        complete=True,
    )
    return DecisionEvidence(
        evidence_version="decision-evidence-v1",
        run_id="run", generation=7, position_id="pos",
        crossfeed_policy="typed_verify_refine_v1",
        crossfeed_view_digest="1" * 64,
        crossfeed_verification_complete=True,
        candidates=(
            DecisionCandidate("e2e4", "stockfish"),
            DecisionCandidate("d2d4", "reckless"),
            DecisionCandidate("g1f3", "lc0"),
        ),
        verification_terminal=terminal, evidence_faults=(),
    )

def proposal(ev: DecisionEvidence) -> DecisionProposal:
    return DecisionProposal(
        policy="unanimous_verify_v1",
        disposition=DecisionDisposition("PROPOSED", "test"),
        move="e2e4", source_owner="stockfish", evidence_digest=ev.digest,
        frozen_observed_ms=700.0, frozen_before_anchor=True,
    )

def snapshot(**changes) -> DecisionAuthorizationSnapshot:
    base = dict(
        run_id="run", generation=7, position_id="pos",
        request_class="online_time_v1", request_eligible=True, request_reason="current TimePlan",
        legal_roots=("e2e4", "d2d4", "g1f3"), external_root_restriction=(),
        anchor_request_bounded=True, anchor_reserved=True, budget_within_envelope=True,
        partitions_within_caps=True, wall_within_envelope=True, specialist_settlement_complete=True,
        open_specialist_reservations=0, open_solver_reservations=0, gpu_accounted=True,
        measurement_enabled=True, open_non_anchor_measurement_stages=0,
        measurement_provider_available=True, measurement_known_failure=False,
        backend_generation_current=True, controller_fallback_latched=False,
        time_plan_id="time-" + "2" * 64, time_plan_request_class="movetime_deadline_v1",
        time_plan_generation=7, time_plan_position_id="pos", time_plan_anchor_go_command="go movetime 1450",
        terminal_source="staged_verification", staged_complete=True,
        staged_intervention="same_process_staged_verify_v1", staged_generation=7,
        staged_candidate_roots=("e2e4", "d2d4", "g1f3"),
        route_action="BUY_STAGED_VERIFY", route_buy_extension=True, route_decision_digest="3" * 64,
        authority_evidence_frozen_before_soft_deadline=True, authority_blocked=False,
    )
    base.update(changes)
    return DecisionAuthorizationSnapshot(**base)

class ProfileTests(unittest.TestCase):
    def test_shipped_profile(self):
        validate_online_hybrid_profile(load_json(POLICY), load_json(CONFIG), load_json(ONLINE2))

    def test_skip_authority_rejected(self):
        cfg = copy.deepcopy(load_json(CONFIG))
        cfg["hybrid_authority"]["allow_skipped_extension_authority"] = True
        with self.assertRaises(OnlineHybridProfileError):
            validate_online_hybrid_profile(load_json(POLICY), cfg, load_json(ONLINE2))

class AuthorityTests(unittest.TestCase):
    def test_clean_staged_buy_authorizes(self):
        ev = evidence()
        result = authorize_decision(proposal(ev), ev, snapshot(), policy=CLOCKED_AUTHORIZATION_POLICY)
        self.assertTrue(result.authorized)
        self.assertEqual(result.move, "e2e4")

    def test_partial_or_mixed_evidence_fails_closed(self):
        ev = evidence()
        cases = (
            {"terminal_source": "verification"},
            {"staged_complete": False},
            {"route_action": "SKIP_STAGED_VERIFY", "route_buy_extension": False},
            {"authority_evidence_frozen_before_soft_deadline": False},
            {"authority_blocked": True},
            {"time_plan_request_class": "unsupported"},
            {"staged_candidate_roots": ("d2d4", "e2e4", "g1f3")},
        )
        for change in cases:
            with self.subTest(change=change):
                result = authorize_decision(
                    proposal(ev), ev, snapshot(**change), policy=CLOCKED_AUTHORIZATION_POLICY
                )
                self.assertFalse(result.authorized)
                self.assertIsNone(result.move)

if __name__ == "__main__":
    unittest.main()
