"""Fail-closed pre-registered G3-versus-anchor *exploratory* pilot contracts."""
from __future__ import annotations

from copy import deepcopy
import unittest

from tools import g3_pilot as pilot
from tools.local_game.common import QualificationError


class G3PilotContracts(unittest.TestCase):
    def setUp(self):
        self.policy = pilot.pilot_policy()

    def test_frozen_eight_reversed_color_pairs(self):
        plans, blocks = pilot.planned_games(self.policy)
        self.assertEqual(len(plans), 8)
        self.assertEqual(len(blocks), 50)
        self.assertEqual([x["opening_index"] for x in plans],
                         [0, 6, 12, 18, 24, 30, 36, 42])
        self.assertEqual(sum(2 for _ in plans), 16)
        self.assertEqual(plans[0]["arms"], list(pilot.ARMS))
        self.assertEqual(plans[1]["arms"], list(reversed(pilot.ARMS)))
        self.assertTrue(all(x["clock"] == "0:30+1" for x in plans))

    def test_claim_bools_are_false_even_in_a_successful_pilot(self):
        for key in ("elo", "superiority", "equal_compute", "j12_meta1_campaign",
                    "j12_authority_qualified", "live_lichess", "deployment"):
            self.assertIs(pilot.CLAIM_BOUNDARY[key], False)

    def test_unfrozen_opening_subset_or_policy_claim_is_rejected(self):
        for field, value in (
            ("opening_indices", list(reversed(self.policy["opening_indices"]))),
            ("total_games", 14),
            ("games_per_opening", 1),
            ("clock", "0:10+1"),
            ("source_runtime", "config/allfather.orchestrated-v1.validation.json"),
            ("arms", list(reversed(pilot.ARMS))),
            ("meta1_campaign_qualified", True),
            ("same_compute_claim", True),
            ("public_bot_release", True),
            ("host_j12_authority_qualified", True),
            ("elo_gate", True),
            ("live_api_permitted", True),
        ):
            with self.subTest(field=field):
                altered = deepcopy(self.policy)
                altered[field] = value
                with self.assertRaises(QualificationError):
                    pilot.pilot_policy(altered)

    def test_points_are_color_invariant_and_completed_only(self):
        g3, anchor = pilot.ARMS
        self.assertEqual(pilot.points("1-0", g3, g3, anchor), 1.0)
        self.assertEqual(pilot.points("0-1", g3, anchor, g3), 1.0)
        self.assertEqual(pilot.points("1/2-1/2", g3, g3, anchor), 0.5)
        self.assertEqual(pilot.points("1-0", anchor, g3, anchor), 0.0)
        with self.assertRaises(QualificationError):
            pilot.points("*", g3, g3, anchor)

    def make_draw_fixture(self):
        plans, _ = pilot.planned_games(self.policy)
        games = []
        for p in plans:
            for n in range(2):
                colors = p["arms"] if n == 0 else list(reversed(p["arms"]))
                games.append({
                    "block": p["id"], "opening_index": p["opening_index"],
                    "game": n, "white": colors[0], "black": colors[1],
                    "result": "1/2-1/2",
                })
        plies = [
            {"arm": "allfather-g3", "response_ms": 42.0,
             "observed_cpu_ms": 13.0, "physical_cpu_ms": 11.0,
             "authority": "ANCHOR_FALLBACK", "override": False},
            {"arm": "allfather-anchor", "response_ms": 39.0,
             "observed_cpu_ms": 17.0},
        ]
        return games, plies

    def test_all_draws_are_paired_without_strength_inference(self):
        games, plies = self.make_draw_fixture()
        report = pilot.summarize(self.policy, games, plies)
        self.assertEqual(report["real_chess_games_verified"], 16)
        self.assertEqual(report["scores"]["allfather-g3"]["D"], 16)
        self.assertEqual(report["scores"]["allfather-anchor"]["D"], 16)
        self.assertEqual(report["g3_minus_anchor_points"], 0.0)
        self.assertEqual(len(report["opening_pairs"]), 8)
        self.assertEqual(report["arm_telemetry"]["allfather-g3"]
                         ["actual_anchor_changing_overrides"], 0)

    def test_missing_or_corrupted_game_blocks_fail_closed(self):
        games, plies = self.make_draw_fixture()
        for broken in (games[:-1], games[:]):
            if len(broken) == len(games):
                broken = deepcopy(broken)
                broken[1]["white"] = broken[0]["white"]
                broken[1]["black"] = broken[0]["black"]
            with self.assertRaises(QualificationError):
                pilot.summarize(self.policy, broken, plies)

    def test_missing_independent_chess_move_evidence_fails(self):
        games, plies = self.make_draw_fixture()
        with self.assertRaises(QualificationError):
            pilot.summarize(self.policy, games, [])
        with self.assertRaises(QualificationError):
            pilot.summarize(self.policy, games, plies[:1])


if __name__ == "__main__":
    unittest.main()
