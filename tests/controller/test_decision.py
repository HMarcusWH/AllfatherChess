#!/usr/bin/env python3
"""Pure decision-policy tests for counterfactual hybrid proposals."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.crossfeed import (
    CROSSFEED_POLICY,
    CandidateHint,
    CrossFeedCandidate,
    CrossFeedView,
    NativeEvaluation,
)
from controller.decision import (
    COUNTERFACTUAL_POLICY,
    DecisionAuthorization,
    DecisionError,
    VerificationTerminalEvidence,
    attach_anchor,
    build_decision_evidence,
    evaluate_decision_policy,
    freeze_decision_proposal,
)


OWNERS = ("stockfish", "reckless", "lc0")
CANDIDATES = ("e2e4", "d2d4", "g1f3")


def _view(*, value: int = 20, complete: bool = True, faults: tuple[str, ...] = ()) -> CrossFeedView:
    candidates = []
    for index, (owner, move) in enumerate(zip(OWNERS, CANDIDATES)):
        hint = CandidateHint(
            move=move,
            observed_move=move,
            source_owner=owner,
            source_instance=f"{owner}-shadow",
            source_family=owner,
            source_phase="VERIFY",
            source_rank=1,
            pv_prefix=(move, "e7e5"),
            evaluations=(
                NativeEvaluation(
                    kind="scalar" if owner == "lc0" else "cp",
                    semantics=(
                        "lc0.uci_score.Q" if owner == "lc0" else f"{owner}.uci_cp"
                    ),
                    value=value + index,
                    bound="none",
                    perspective="unknown",
                ),
            ),
            work=(),
            search_id=f"run-1:verify:{owner}-shadow:0",
            source_run_id="run-1",
            sequence=2,
            observed_ms=20.0,
            stage_disposition="completed",
        )
        candidates.append(
            CrossFeedCandidate(move=move, original_owner=owner, hints=(hint,))
        )
    return CrossFeedView(
        run_id="run-1",
        generation=1,
        position_id="pos-1",
        policy=CROSSFEED_POLICY,
        verification_id="run-1:verify-v1",
        verification_disposition="completed" if complete else "incomplete",
        verification_complete=complete,
        refinement_id=None,
        refinement_disposition="absent",
        candidates=tuple(candidates),
        evidence_faults=faults,
    )


def _terminal(
    finals: tuple[str | None, str | None, str | None],
    *,
    complete: bool = True,
    disposition: str = "completed",
    faults: tuple[str, ...] = (),
) -> VerificationTerminalEvidence:
    return VerificationTerminalEvidence(
        verification_id="run-1:verify-v1",
        candidate_roots=CANDIDATES,
        final_by_owner=tuple(zip(OWNERS, finals)),
        stage_disposition_by_owner=tuple(
            (owner, "completed" if move is not None else "incomplete")
            for owner, move in zip(OWNERS, finals)
        ),
        run_disposition=disposition,
        complete=complete,
        faults=faults,
    )


class DecisionPolicyTests(unittest.TestCase):
    def _evaluate(self, finals, **kwargs):
        evidence = build_decision_evidence(_view(), _terminal(finals, **kwargs))
        return evidence, evaluate_decision_policy(evidence)

    def test_three_way_unanimity_proposes_exact_common_candidate(self):
        evidence, evaluation = self._evaluate(("g1f3", "g1f3", "g1f3"))
        self.assertEqual(evaluation.policy, COUNTERFACTUAL_POLICY)
        self.assertEqual(evaluation.disposition.code, "PROPOSED")
        self.assertEqual(evaluation.move, "g1f3")
        self.assertEqual(evaluation.source_owner, "lc0")
        self.assertEqual(evaluation.evidence_digest, evidence.digest)

    def test_two_to_one_is_not_a_vote(self):
        _, evaluation = self._evaluate(("g1f3", "g1f3", "e2e4"))
        self.assertEqual(
            evaluation.disposition.code,
            "NO_PROPOSAL_NONUNANIMOUS",
        )
        self.assertIsNone(evaluation.move)
        self.assertIsNone(evaluation.source_owner)

    def test_three_way_split_is_no_proposal(self):
        _, evaluation = self._evaluate(("e2e4", "d2d4", "g1f3"))
        self.assertEqual(
            evaluation.disposition.code,
            "NO_PROPOSAL_NONUNANIMOUS",
        )

    def test_incomplete_verify_never_becomes_unanimity(self):
        terminal = _terminal(
            ("e2e4", "e2e4", None),
            complete=False,
            disposition="incomplete",
        )
        evidence = build_decision_evidence(_view(complete=False), terminal)
        evaluation = evaluate_decision_policy(evidence)
        self.assertEqual(
            evaluation.disposition.code,
            "NO_PROPOSAL_VERIFY_INCOMPLETE",
        )

    def test_completed_verify_with_missing_terminal_move_is_no_proposal(self):
        terminal = VerificationTerminalEvidence(
            verification_id="run-1:verify-v1",
            candidate_roots=CANDIDATES,
            final_by_owner=(
                ("stockfish", "e2e4"),
                ("reckless", "e2e4"),
                ("lc0", None),
            ),
            stage_disposition_by_owner=tuple(
                (owner, "completed") for owner in OWNERS
            ),
            run_disposition="completed",
            complete=True,
        )
        evidence = build_decision_evidence(_view(), terminal)
        evaluation = evaluate_decision_policy(evidence)
        self.assertEqual(
            evaluation.disposition.code,
            "NO_PROPOSAL_TERMINAL_INCOMPLETE",
        )

    def test_explicit_evidence_fault_fails_closed(self):
        evidence = build_decision_evidence(
            _view(faults=("VERIFY stream lossy for lc0",)),
            _terminal(("e2e4", "e2e4", "e2e4")),
        )
        evaluation = evaluate_decision_policy(evidence)
        self.assertEqual(
            evaluation.disposition.code,
            "NO_PROPOSAL_EVIDENCE_FAULT",
        )

    def test_terminal_move_outside_common_support_is_invalid_not_a_vote(self):
        evidence = build_decision_evidence(
            _view(),
            _terminal(("a2a3", "a2a3", "a2a3")),
        )
        evaluation = evaluate_decision_policy(evidence)
        self.assertEqual(evaluation.disposition.code, "INVALID_EVIDENCE")
        self.assertIsNone(evaluation.move)

    def test_numeric_native_evaluations_do_not_change_policy_v1_outcome(self):
        first_evidence = build_decision_evidence(
            _view(value=10),
            _terminal(("g1f3", "g1f3", "g1f3")),
        )
        second_evidence = build_decision_evidence(
            _view(value=9000),
            _terminal(("g1f3", "g1f3", "g1f3")),
        )
        first = evaluate_decision_policy(first_evidence)
        second = evaluate_decision_policy(second_evidence)
        self.assertEqual(first.disposition.code, second.disposition.code)
        self.assertEqual(first.move, second.move)
        self.assertEqual(first.source_owner, second.source_owner)
        # The evidence digest still records that the native evidence changed.
        self.assertNotEqual(first.evidence_digest, second.evidence_digest)

    def test_policy_version_is_explicit_and_unknown_policy_fails(self):
        evidence = build_decision_evidence(
            _view(),
            _terminal(("e2e4", "e2e4", "e2e4")),
        )
        with self.assertRaises(DecisionError):
            evaluate_decision_policy(evidence, policy="majority_vote_v1")

    def test_freeze_stamps_timing_without_changing_semantics(self):
        evidence = build_decision_evidence(
            _view(),
            _terminal(("d2d4", "d2d4", "d2d4")),
        )
        evaluation = evaluate_decision_policy(evidence)
        proposal = freeze_decision_proposal(
            evaluation,
            frozen_observed_ms=123.456,
            frozen_before_anchor=True,
        )
        self.assertEqual(proposal.move, "d2d4")
        self.assertTrue(proposal.frozen_before_anchor)
        self.assertEqual(proposal.evidence_digest, evidence.digest)

    def test_counterfactual_anchor_relation_is_descriptive_only(self):
        evidence = build_decision_evidence(
            _view(),
            _terminal(("g1f3", "g1f3", "g1f3")),
        )
        proposal = freeze_decision_proposal(
            evaluate_decision_policy(evidence),
            frozen_observed_ms=50.0,
            frozen_before_anchor=True,
        )
        decision = attach_anchor(proposal, anchor_move="e2e4")
        self.assertTrue(decision.would_change_outward_move)
        self.assertFalse(decision.proposal_matches_anchor)
        self.assertEqual(decision.outward_authority, "stockfish-anchor")

    def test_counterfactual_layer_cannot_grant_authorization(self):
        with self.assertRaises(DecisionError):
            DecisionAuthorization(
                authorized=True,
                move="e2e4",
                reason="forbidden in PR22",
            )


if __name__ == "__main__":
    unittest.main()
