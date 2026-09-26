#!/usr/bin/env python3
from __future__ import annotations
import copy
import json
import sys
import tempfile
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
    revoke_final_decision_to_anchor,
    select_final_decision,
)
from controller.final_decision import (
    _verify_clocked_authority,
    seal_final_decision_artifact,
    verify_final_decision_integrity,
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

    def test_g3_workflow_tracks_all_bundle_inputs_on_pr_and_main(self):
        workflow = (
            ROOT / ".github/workflows/online-hybrid-qualification.yml"
        ).read_text(encoding="utf-8")
        critical_inputs = (
            "qualification/online-cpu-reference.json",
            "qualification/lc0-strength.lock.json",
            "qualification/lc0-strength-profile.json",
            "config/allfather.online.cpu-reference.json",
            "scripts/build-online-cpu-reference.sh",
            "scripts/build-lc0-strength.sh",
            "scripts/verify-vendor.sh",
            "scripts/vendor-lock.py",
            "scripts/fetch-*.py",
            "scripts/fetch-*.sh",
            "scripts/lc0-*.py",
            "adapters/crossfeed/**",
            "tests/controller/test_online_profile.py",
            "engines/**",
            "vendor.lock.json",
        )
        for path in critical_inputs:
            with self.subTest(path=path):
                self.assertEqual(
                    workflow.count(f"- {path}"),
                    2,
                    f"{path} must trigger both pull-request and main qualification",
                )

    def test_skip_authority_rejected(self):
        cfg = copy.deepcopy(load_json(CONFIG))
        cfg["hybrid_authority"]["allow_skipped_extension_authority"] = True
        with self.assertRaises(OnlineHybridProfileError):
            validate_online_hybrid_profile(load_json(POLICY), cfg, load_json(ONLINE2))

class FinalDecisionAuditTests(unittest.TestCase):
    def test_denied_g3_fallback_is_auditable_without_unconsumed_specialist_artifacts(self):
        ev = evidence()
        snap = snapshot(
            terminal_source="verification",
            staged_complete=False,
            staged_intervention=None,
            staged_generation=None,
            staged_candidate_roots=(),
            route_action=None,
            route_buy_extension=None,
            route_decision_digest=None,
            authority_evidence_frozen_before_soft_deadline=False,
        )
        authorization = authorize_decision(
            proposal(ev),
            ev,
            snap,
            policy=CLOCKED_AUTHORIZATION_POLICY,
        )
        self.assertFalse(authorization.authorized)
        final = select_final_decision(
            anchor_move="d2d4",
            proposal=proposal(ev),
            authorization=authorization,
            authorization_snapshot=snap,
        )
        self.assertEqual(final.authority, "ANCHOR_FALLBACK")

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "decision").mkdir(parents=True)
            parent = {
                "generation": 7,
                "position": {"position_id": "pos"},
                "time_plan": {
                    "plan_id": snap.time_plan_id,
                    "request_class": snap.time_plan_request_class,
                    "generation": 7,
                    "position_id": "pos",
                    "anchor_go_command": snap.time_plan_anchor_go_command,
                },
                "outward_decision": final.as_dict(),
            }
            (run / "manifest.json").write_text(
                json.dumps(parent), encoding="utf-8"
            )
            (run / "route.json").write_text("{}", encoding="utf-8")
            (run / "resource.json").write_text("{}", encoding="utf-8")

            artifact = seal_final_decision_artifact(final, run)
            self.assertTrue(artifact["audit_complete"])
            self.assertEqual(artifact["missing_sources"], [])
            self.assertEqual(verify_final_decision_integrity(run), [])


    def test_authorized_g3_replay_requires_route_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "decision").mkdir(parents=True)
            (run / "staged_verification").mkdir(parents=True)
            decision = {
                "authority": "HYBRID",
                "anchor_move": "d2d4",
                "proposal_move": "e2e4",
                "emitted_move": "e2e4",
            }
            snapshot_doc = snapshot(route_decision_digest=None).as_dict()
            parent = {
                "generation": 7,
                "position": {"position_id": "pos"},
                "time_plan": {
                    "plan_id": snapshot_doc["time_plan_id"],
                    "request_class": snapshot_doc["time_plan_request_class"],
                    "generation": 7,
                    "position_id": "pos",
                    "anchor_go_command": snapshot_doc["time_plan_anchor_go_command"],
                },
                "outward_decision": decision,
            }
            (run / "manifest.json").write_text(
                json.dumps(parent), encoding="utf-8"
            )
            (run / "route.json").write_text(
                json.dumps({"value_decisions": []}), encoding="utf-8"
            )
            (run / "staged_verification" / "manifest.json").write_text(
                json.dumps({}), encoding="utf-8"
            )
            (run / "decision" / "counterfactual.json").write_text(
                json.dumps({}), encoding="utf-8"
            )

            problems: list[str] = []
            _verify_clocked_authority(
                run,
                decision,
                {
                    "policy": CLOCKED_AUTHORIZATION_POLICY,
                    "authorized": True,
                },
                snapshot_doc,
                problems,
            )
            self.assertIn(
                "authorized G3 decision is missing a valid route decision digest",
                problems,
            )


class AuthorityTests(unittest.TestCase):
    def test_clean_staged_buy_authorizes(self):
        ev = evidence()
        result = authorize_decision(proposal(ev), ev, snapshot(), policy=CLOCKED_AUTHORIZATION_POLICY)
        self.assertTrue(result.authorized)
        self.assertEqual(result.move, "e2e4")

    def test_clock_revocation_rebinds_hybrid_to_auditable_anchor_fallback(self):
        ev = evidence()
        granted = authorize_decision(
            proposal(ev),
            ev,
            snapshot(),
            policy=CLOCKED_AUTHORIZATION_POLICY,
        )
        selected = select_final_decision(
            anchor_move="d2d4",
            proposal=proposal(ev),
            authorization=granted,
            authorization_snapshot=snapshot(),
        )
        self.assertEqual(selected.authority, "HYBRID")
        revoked = revoke_final_decision_to_anchor(
            selected,
            reason="clock authority revoked before outward write",
        )
        self.assertEqual(revoked.authority, "ANCHOR_FALLBACK")
        self.assertEqual(revoked.emitted_move, "d2d4")
        self.assertEqual(revoked.anchor_move, "d2d4")
        self.assertEqual(revoked.proposal_move, "e2e4")
        self.assertFalse(revoked.authorization.authorized)
        self.assertTrue(revoked.authorization_snapshot.authority_blocked)
        self.assertEqual(
            revoked.authorization.snapshot_digest,
            revoked.authorization_snapshot.digest,
        )

        already_denied = select_final_decision(
            anchor_move="d2d4",
            proposal=proposal(ev),
            authorization=authorize_decision(
                proposal(ev),
                ev,
                snapshot(authority_blocked=True),
                policy=CLOCKED_AUTHORIZATION_POLICY,
            ),
            authorization_snapshot=snapshot(authority_blocked=True),
        )
        self.assertIs(
            revoke_final_decision_to_anchor(
                already_denied,
                reason="clock authority remains revoked",
            ),
            already_denied,
        )

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
