#!/usr/bin/env python3
"""M14-J J10 allocation-feature semantic parity tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.allocation_features import staged_serving_features
from controller.regimes import VerifierRegimeFeatures
from controller.unified_value_router import _serving_features as g2_serving_features


def verifiers() -> tuple[VerifierRegimeFeatures, ...]:
    return (
        VerifierRegimeFeatures(
            owner="stockfish",
            terminal_move="e2e4",
            observation_count=8,
            leader_flips=1,
            stable_run_fraction=0.75,
            pv_persistence=0.8,
        ),
        VerifierRegimeFeatures(
            owner="reckless",
            terminal_move="d2d4",
            observation_count=6,
            leader_flips=2,
            stable_run_fraction=0.60,
            pv_persistence=0.7,
        ),
        VerifierRegimeFeatures(
            owner="lc0",
            terminal_move="g1f3",
            observation_count=10,
            leader_flips=0,
            stable_run_fraction=0.90,
            pv_persistence=0.9,
        ),
    )


class AllocationFeatureParityTests(unittest.TestCase):
    def test_j10_staged_serving_features_match_frozen_g2_semantics(self):
        items = verifiers()
        transition = "same-process:n16->n32"
        disposition = "NO_PROPOSAL_NONUNANIMOUS"
        self.assertEqual(
            staged_serving_features(
                transition=transition,
                disposition=disposition,
                verifiers=items,
            ),
            g2_serving_features(
                transition=transition,
                disposition=disposition,
                verifiers=items,
            ),
        )

    def test_feature_surface_contains_no_future_or_quality_label(self):
        payload = staged_serving_features(
            transition="same-process:n16->n32",
            disposition="NO_PROPOSAL_NONUNANIMOUS",
            verifiers=verifiers(),
        )
        text = " ".join(payload).lower()
        for forbidden in (
            "extension_terminal",
            "decision_changed",
            "game_result",
            "winner",
            "elo",
            "strength",
            "correct",
            "better",
        ):
            self.assertNotIn(forbidden, text)

    def test_empty_verifier_plane_is_rejected(self):
        with self.assertRaises(Exception):
            staged_serving_features(
                transition="same-process:n16->n32",
                disposition="NO_PROPOSAL_NONUNANIMOUS",
                verifiers=(),
            )


if __name__ == "__main__":
    unittest.main()
