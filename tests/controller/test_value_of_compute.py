#!/usr/bin/env python3
"""Prospective VERIFY value-of-compute unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.value_of_compute import (
    ComputeTransition,
    ValueOfComputeError,
    VerifierFeatures,
    VerifyBudgetPoint,
    build_dataset,
    build_transition,
    build_transitions,
    full_budget_labels,
)


OWNERS = ("stockfish", "reckless", "lc0")
CANDIDATES = ("e2e4", "d2d4", "g1f3")


def _point(
    *,
    group: str = "pos-a",
    replicate: int = 0,
    nodes: int = 64,
    fingerprint: str = "a" * 64,
    disposition: str = "NO_PROPOSAL_NONUNANIMOUS",
    move: str | None = None,
    terminals: tuple[str | None, str | None, str | None] = CANDIDATES,
    stable: float = 0.5,
    flips: int = 1,
    observations: int = 8,
) -> VerifyBudgetPoint:
    features = tuple(
        VerifierFeatures(
            owner=owner,
            terminal_move=terminal,
            observation_count=observations + index,
            leader_flips=flips,
            stable_run_fraction=stable,
            pv_persistence=0.5,
            self_retained=True,
            stage_elapsed_ms=10.0 + index,
            native_work_value=float(nodes),
            native_work_semantics=f"{owner}.uci_nodes",
        )
        for index, (owner, terminal) in enumerate(zip(OWNERS, terminals))
    )
    return VerifyBudgetPoint(
        run_id=f"{group}-r{replicate}-n{nodes}",
        position_id=group,
        position_group=group,
        replicate=replicate,
        verify_nodes=nodes,
        upstream_fingerprint=fingerprint,
        candidate_roots=CANDIDATES,
        nominees_by_owner=tuple(zip(OWNERS, CANDIDATES)),
        terminal_by_owner=tuple(zip(OWNERS, terminals)),
        proposal_disposition=disposition,
        proposal_move=move,
        proposal_source_owner=("lc0" if move == "g1f3" else "stockfish" if move == "e2e4" else None),
        proposal_pre_anchor=True,
        verifier_features=features,
        evidence_complete=True,
        source_hashes=(
            ("counterfactual_artifact", "1" * 64),
            ("crossfeed_manifest", "2" * 64),
            ("parent_manifest", "3" * 64),
            ("verification_manifest", "4" * 64),
        ),
    )


class TransitionTests(unittest.TestCase):
    def test_identical_upstream_builds_eligible_transition(self):
        lower = _point(nodes=64)
        upper = _point(nodes=128)
        transition = build_transition(lower, upper)
        self.assertEqual(transition.transition_key, "n64->n128")
        self.assertEqual(transition.additional_nodes_per_owner, 64)
        self.assertFalse(transition.decision_changed)

    def test_upstream_mismatch_is_rejected(self):
        lower = _point(nodes=64, fingerprint="a" * 64)
        upper = _point(nodes=128, fingerprint="b" * 64)
        with self.assertRaises(ValueOfComputeError):
            build_transition(lower, upper)

    def test_replicate_mismatch_is_rejected(self):
        with self.assertRaises(ValueOfComputeError):
            build_transition(
                _point(nodes=64, replicate=0),
                _point(nodes=128, replicate=1),
            )

    def test_proposal_emergence_is_descriptive_label(self):
        lower = _point(nodes=64)
        upper = _point(
            nodes=128,
            disposition="PROPOSED",
            move="g1f3",
            terminals=("g1f3", "g1f3", "g1f3"),
        )
        transition = build_transition(lower, upper)
        self.assertTrue(transition.decision_changed)
        self.assertTrue(transition.proposal_emerged)
        self.assertFalse(transition.proposal_disappeared)
        self.assertTrue(transition.terminal_vector_changed)

    def test_proposal_move_change_is_distinct_from_emergence(self):
        lower = _point(
            nodes=64,
            disposition="PROPOSED",
            move="e2e4",
            terminals=("e2e4", "e2e4", "e2e4"),
        )
        upper = _point(
            nodes=128,
            disposition="PROPOSED",
            move="g1f3",
            terminals=("g1f3", "g1f3", "g1f3"),
        )
        transition = build_transition(lower, upper)
        self.assertTrue(transition.decision_changed)
        self.assertTrue(transition.proposal_move_changed)
        self.assertFalse(transition.proposal_emerged)

    def test_terminal_vector_can_change_without_policy_decision_change(self):
        lower = _point(
            nodes=64,
            terminals=("e2e4", "d2d4", "g1f3"),
        )
        upper = _point(
            nodes=128,
            terminals=("d2d4", "e2e4", "g1f3"),
        )
        transition = build_transition(lower, upper)
        self.assertTrue(transition.terminal_vector_changed)
        self.assertFalse(transition.decision_changed)

    def test_future_label_cannot_change_lower_feature_digest(self):
        lower = _point(nodes=64)
        upper_a = _point(nodes=128)
        upper_b = _point(
            nodes=128,
            disposition="PROPOSED",
            move="g1f3",
            terminals=("g1f3", "g1f3", "g1f3"),
        )
        first = build_transition(lower, upper_a)
        second = build_transition(lower, upper_b)
        self.assertEqual(first.feature_digest, second.feature_digest)
        self.assertNotEqual(first.label_digest, second.label_digest)

    def test_native_work_remains_per_engine_and_source_tagged(self):
        payload = _point().feature_payload()
        work = [item["native_work"] for item in payload["verifiers"]]
        self.assertEqual(len(work), 3)
        self.assertEqual(
            [item["semantics"] for item in work],
            ["stockfish.uci_nodes", "reckless.uci_nodes", "lc0.uci_nodes"],
        )
        self.assertNotIn("total_work", payload)
        self.assertNotIn("total_nodes", payload)


class DatasetTests(unittest.TestCase):
    def test_adjacent_pairing_never_crosses_replicates(self):
        points = [
            _point(nodes=64, replicate=0),
            _point(nodes=128, replicate=0),
            _point(nodes=64, replicate=1),
            _point(nodes=128, replicate=1),
        ]
        transitions = build_transitions(points)
        self.assertEqual(len(transitions), 2)
        self.assertEqual({item.lower.replicate for item in transitions}, {0, 1})

    def test_duplicate_budget_in_one_causal_group_is_rejected(self):
        with self.assertRaises(ValueOfComputeError):
            build_transitions([_point(nodes=64), _point(nodes=64)])

    def test_full_budget_labels_keep_no_proposal_as_null_candidate_survival(self):
        points = [
            _point(nodes=64),
            _point(
                nodes=128,
                disposition="PROPOSED",
                move="g1f3",
                terminals=("g1f3", "g1f3", "g1f3"),
            ),
            _point(
                nodes=256,
                disposition="PROPOSED",
                move="g1f3",
                terminals=("g1f3", "g1f3", "g1f3"),
            ),
        ]
        labels = full_budget_labels(points)
        by_nodes = {item["checkpoint_nodes"]: item for item in labels}
        self.assertIsNone(by_nodes[64]["candidate_survived_full_budget"])
        self.assertTrue(by_nodes[128]["candidate_survived_full_budget"])
        self.assertTrue(by_nodes[128]["decision_stabilized"])

    def test_dataset_contains_separate_feature_and_label_addresses(self):
        lower = _point(nodes=64)
        upper = _point(nodes=128)
        dataset = build_dataset([lower, upper])
        self.assertTrue(dataset["dataset_id"].startswith("voc-"))
        row = dataset["transitions"][0]
        self.assertEqual(len(row["feature_digest"]), 64)
        self.assertEqual(len(row["label_digest"]), 64)
        self.assertNotEqual(row["feature_digest"], row["label_digest"])


if __name__ == "__main__":
    unittest.main()
