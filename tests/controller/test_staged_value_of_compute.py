#!/usr/bin/env python3
"""M14-G1 staged value-of-compute causality regressions."""

from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import DecisionDisposition, DecisionEvaluation
from controller.staged_value_of_compute import (
    StagedVerifyTransition,
    build_dataset,
    load_dataset,
    write_dataset,
)
from controller.value_of_compute import VerifierFeatures


OWNERS = ("stockfish", "reckless", "lc0")
CANDIDATES = ("e2e4", "d2d4", "g1f3")
NOMINEES = tuple(zip(OWNERS, CANDIDATES))


def _evaluation(
    code: str,
    move: str | None,
    owner: str | None,
    digest_char: str,
) -> DecisionEvaluation:
    return DecisionEvaluation(
        policy="unanimous_verify_v1",
        disposition=DecisionDisposition(code, f"fixture {code}"),
        move=move,
        source_owner=owner,
        evidence_digest=digest_char * 64,
    )


def _features() -> tuple[VerifierFeatures, ...]:
    return tuple(
        VerifierFeatures(
            owner=owner,
            terminal_move=move,
            observation_count=8,
            leader_flips=1,
            stable_run_fraction=0.75,
            pv_persistence=0.8,
            self_retained=True,
            stage_elapsed_ms=12.5,
            native_work_value=64.0,
            native_work_semantics=(
                "lc0.uci_nodes" if owner == "lc0" else f"{owner}.uci_nodes"
            ),
        )
        for owner, move in NOMINEES
    )


def _transition(
    *,
    group: str = "p1",
    replicate: int = 0,
    extension_terminals: tuple[tuple[str, str | None], ...] | None = None,
    extension_eval: DecisionEvaluation | None = None,
) -> StagedVerifyTransition:
    base_terminals = tuple((owner, move) for owner, move in NOMINEES)
    if extension_terminals is None:
        extension_terminals = base_terminals
    if extension_eval is None:
        extension_eval = _evaluation(
            "NO_PROPOSAL_NONUNANIMOUS",
            None,
            None,
            "b",
        )
    return StagedVerifyTransition(
        run_id=f"run-{group}-{replicate}",
        position_id=f"pos-{group}",
        position_group=group,
        replicate=replicate,
        intervention="same_process_staged_verify_v1",
        base_nodes=64,
        extension_nodes=128,
        candidate_roots=CANDIDATES,
        nominees_by_owner=NOMINEES,
        base_terminal_by_owner=base_terminals,
        extension_terminal_by_owner=extension_terminals,
        base_evaluation=_evaluation(
            "NO_PROPOSAL_NONUNANIMOUS",
            None,
            None,
            "a",
        ),
        extension_evaluation=extension_eval,
        verifier_features=_features(),
        source_hashes=(
            ("parent_manifest", "1" * 64),
            ("staged_verification_manifest", "2" * 64),
            ("verification_manifest", "3" * 64),
        ),
    )


class StagedValueCausalityTests(unittest.TestCase):
    def test_future_extension_cannot_change_base_feature_digest(self):
        first = _transition()
        second = _transition(
            extension_terminals=(
                ("stockfish", "e2e4"),
                ("reckless", "e2e4"),
                ("lc0", "e2e4"),
            ),
            extension_eval=_evaluation("PROPOSED", "e2e4", "stockfish", "c"),
        )
        self.assertEqual(first.feature_digest, second.feature_digest)
        self.assertNotEqual(first.label_digest, second.label_digest)
        self.assertFalse(first.label_payload()["decision_changed"])
        self.assertTrue(second.label_payload()["decision_changed"])

    def test_feature_surface_contains_no_extension_outcome(self):
        payload = _transition().feature_payload()
        serialized = str(payload).lower()
        for forbidden in (
            "extension_terminal",
            "extension_decision",
            "decision_changed",
            "proposal_emerged",
            "label_observed",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_dataset_round_trip_preserves_content_identity(self):
        dataset = build_dataset(
            [_transition(group="p1"), _transition(group="p2")]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(dataset, Path(tmp))
            loaded = load_dataset(path)
        self.assertEqual(loaded["dataset_id"], dataset["dataset_id"])
        self.assertEqual(loaded["content_sha256"], dataset["content_sha256"])

    def test_decision_change_label_is_descriptive_not_quality_named(self):
        labels = _transition().label_payload()
        joined = " ".join(labels).lower()
        for forbidden in ("better", "correct", "elo", "strength", "gain"):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
