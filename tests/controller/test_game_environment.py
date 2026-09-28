#!/usr/bin/env python3
"""Contract tests for immutable M14-J GameEnvironment."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.game_environment import EnvironmentSource, GameEnvironment
from controller.resource_profiles import OrchestrationContractError


class GameEnvironmentContractTests(unittest.TestCase):
    def bridge(self, **overrides) -> GameEnvironment:
        values = {
            "source": EnvironmentSource.BRIDGE_PREDECLARED,
            "base_ms": 600000,
            "increment_ms": 5000,
            "moves_to_go": None,
            "white_time_ms": None,
            "black_time_ms": None,
            "concurrency": 1,
            "network_policy": "online-default-v1",
            "network_reserve_ms": 100,
            "host_profile_id": "host/cpu4",
            "candidate_composition_ids": (
                "composition/cpu4-balanced",
                "composition/v2-fallback",
            ),
        }
        values.update(overrides)
        return GameEnvironment(**values)

    def test_bridge_environment_round_trip_and_stable_digest(self):
        first = self.bridge()
        raw = first.as_dict()
        second = GameEnvironment.from_dict(raw)
        self.assertEqual(first, second)
        self.assertEqual(first.digest, second.digest)
        self.assertTrue(first.environment_id.startswith("game-env/"))
        self.assertFalse(raw["authority"]["outward_move"])

        reordered = self.bridge(
            candidate_composition_ids=tuple(
                reversed(first.candidate_composition_ids)
            )
        )
        self.assertEqual(first.as_dict(), reordered.as_dict())
        self.assertEqual(first.digest, reordered.digest)

    def test_claim_bearing_change_changes_digest(self):
        self.assertNotEqual(
            self.bridge().digest,
            self.bridge(network_reserve_ms=250).digest,
        )

    def test_bridge_source_requires_declared_time_control(self):
        with self.assertRaises(OrchestrationContractError):
            self.bridge(base_ms=None)
        with self.assertRaises(OrchestrationContractError):
            self.bridge(increment_ms=None)

    def test_uci_observed_requires_both_clocks_and_increment(self):
        observed = GameEnvironment(
            source="uci_observed",
            base_ms=None,
            increment_ms=1000,
            moves_to_go=None,
            white_time_ms=30000,
            black_time_ms=28000,
            concurrency=1,
            network_policy="generic-uci-v1",
            network_reserve_ms=50,
        )
        self.assertEqual(observed.source, EnvironmentSource.UCI_OBSERVED)

        with self.assertRaises(OrchestrationContractError):
            GameEnvironment(
                source="uci_observed",
                base_ms=None,
                increment_ms=1000,
                moves_to_go=None,
                white_time_ms=30000,
                black_time_ms=None,
                concurrency=1,
                network_policy="generic-uci-v1",
                network_reserve_ms=50,
            )

    def test_unknown_environment_may_not_invent_timing(self):
        unknown = GameEnvironment(
            source="unknown",
            base_ms=None,
            increment_ms=None,
            moves_to_go=None,
            white_time_ms=None,
            black_time_ms=None,
            concurrency=1,
            network_policy="fallback-v1",
            network_reserve_ms=100,
        )
        self.assertEqual(unknown.source, EnvironmentSource.UNKNOWN)

        with self.assertRaises(OrchestrationContractError):
            GameEnvironment(
                source="unknown",
                base_ms=60000,
                increment_ms=None,
                moves_to_go=None,
                white_time_ms=None,
                black_time_ms=None,
                concurrency=1,
                network_policy="fallback-v1",
                network_reserve_ms=100,
            )

    def test_bool_as_numeric_is_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            self.bridge(concurrency=True)
        with self.assertRaises(OrchestrationContractError):
            self.bridge(network_reserve_ms=False)

    def test_duplicate_composition_candidates_are_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            self.bridge(
                candidate_composition_ids=(
                    "composition/a",
                    "composition/a",
                )
            )

    def test_tampered_authority_marker_is_rejected(self):
        raw = self.bridge().as_dict()
        raw = copy.deepcopy(raw)
        raw["authority"]["outward_move"] = True
        with self.assertRaises(OrchestrationContractError):
            GameEnvironment.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
