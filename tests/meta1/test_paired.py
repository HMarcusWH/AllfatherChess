#!/usr/bin/env python3
from __future__ import annotations

import unittest

from tools.local_game.common import QualificationError
from tools.meta1.paired import arm_telemetry, paired_blocks


LIVE = "allfather-orchestrated"
CONTROL = "allfather-anchor-control"


def fixture():
    games = []
    plies = []
    for opening in range(50):
        block = f"meta1-{opening:02d}"
        first = [LIVE, CONTROL] if opening % 2 == 0 else [CONTROL, LIVE]
        second = list(reversed(first))
        if opening % 3 == 0:
            r0 = "1-0" if first[0] == LIVE else "0-1"
            r1 = "1-0" if second[0] == LIVE else "0-1"
        elif opening % 3 == 1:
            r0 = r1 = "1/2-1/2"
        else:
            r0 = "1-0" if first[0] == CONTROL else "0-1"
            r1 = "1-0" if second[0] == CONTROL else "0-1"
        for game, colors, result in ((0, first, r0), (1, second, r1)):
            games.append({
                "block": block,
                "opening_index": opening,
                "game": game,
                "white": colors[0],
                "black": colors[1],
                "result": result,
            })
            plies.extend([
                {
                    "block": block,
                    "game": game,
                    "arm": LIVE,
                    "ply": 8,
                    "response_ms": 100.0 + opening,
                    "physical_cpu_ms": 10.0,
                    "authorization_granted": True,
                    "authorized_non_anchor": True,
                    "override": True,
                    "suppressed_authorized_non_anchor": False,
                    "work_grants_authorized": 9,
                    "work_grants_settled": 9,
                    "route_action": "BUY_STAGED_VERIFY",
                },
                {
                    "block": block,
                    "game": game,
                    "arm": CONTROL,
                    "ply": 9,
                    "response_ms": 101.0 + opening,
                    "physical_cpu_ms": 11.0,
                    "authorization_granted": True,
                    "authorized_non_anchor": True,
                    "override": False,
                    "suppressed_authorized_non_anchor": True,
                    "work_grants_authorized": 9,
                    "work_grants_settled": 9,
                    "route_action": "BUY_STAGED_VERIFY",
                },
            ])
    return games, plies


class PairedReportTests(unittest.TestCase):
    def test_fifty_opening_blocks_are_scored_as_pairs(self):
        games, plies = fixture()
        blocks, summary = paired_blocks(games, plies)
        self.assertEqual(len(blocks), 50)
        self.assertEqual(
            summary["live_higher"] + summary["tied"] + summary["control_higher"],
            50,
        )
        self.assertAlmostEqual(
            summary["sum_point_delta"],
            summary["live_points"] - summary["control_points"],
        )
        self.assertEqual(blocks[0]["first_hybrid_intervention_ply"] if "first_hybrid_intervention_ply" in blocks[0] else blocks[0]["games"][0]["first_hybrid_intervention_ply"], 8)

    def test_arm_telemetry_counts_authority_and_resources(self):
        _, plies = fixture()
        telemetry = arm_telemetry(plies)
        self.assertEqual(telemetry[LIVE]["plies"], 100)
        self.assertEqual(telemetry[LIVE]["hybrid_interventions"], 100)
        self.assertEqual(
            telemetry[CONTROL]["suppressed_authorized_non_anchor"],
            100,
        )
        self.assertEqual(telemetry[LIVE]["work_grants_authorized"], 900)
        self.assertEqual(telemetry[LIVE]["work_grants_settled"], 900)
        self.assertEqual(
            telemetry[CONTROL]["route_actions"],
            {"BUY_STAGED_VERIFY": 100},
        )

    def test_missing_block_or_unreversed_colors_fail_closed(self):
        games, plies = fixture()
        with self.assertRaises(QualificationError):
            paired_blocks(games[:-2], plies)
        bad = [dict(row) for row in games]
        bad[1]["white"], bad[1]["black"] = bad[0]["white"], bad[0]["black"]
        with self.assertRaises(QualificationError):
            paired_blocks(bad, plies)


if __name__ == "__main__":
    unittest.main()
