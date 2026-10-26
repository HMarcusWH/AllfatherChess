"""S1 E01 frozen source controls; these tests do not assert chess strength."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest

from tools.local_game.common import ROOT, QualificationError, load
from tools.strength_lab.s1_campaign import (
    ARMS, CLAIMS, INDICES, command, planned_games, policy,
)


class S1CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = policy()

    def test_frozen_openings_and_reversed_arm_order(self):
        plans, blocks = planned_games(self.p)
        self.assertEqual(len(blocks), 50)
        self.assertEqual(len(plans), 8)
        self.assertEqual([p["opening_index"] for p in plans], list(INDICES))
        self.assertEqual(plans[0]["arms"], list(ARMS))
        self.assertEqual(plans[1]["arms"], list(reversed(ARMS)))
        self.assertEqual(sum(self.p["games_per_opening"] for _ in plans), 16)
        self.assertFalse(CLAIMS["equal_compute"])
        self.assertFalse(CLAIMS["superiority"])
        self.assertFalse(CLAIMS["live_lichess"])

    def test_only_two_uci_proxy_executables_change(self):
        plans, _ = planned_games(self.p)
        source = load(ROOT / self.p["source_runtime"])
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            opening = directory / "opening-00.pgn"
            opening.write_text("[Event \"synthetic\"]\n\n*\n", encoding="utf-8")
            args = command(
                plans[0], directory, self.p, source, directory / "fastchess",
                opening, write_specs=False, root=ROOT,
                python_executable=sys.executable,
            )
            self.assertEqual(
                sum("tools.strength_lab.participant_scope" in x for x in args), 2)
            self.assertEqual(
                sum("tools.local_game.proxy" in x for x in args), 0)
            self.assertEqual(args[args.index("-site") + 1], "S1-E01")
            for arm in ARMS:
                self.assertIn(
                    f"args=-m tools.strength_lab.participant_scope --spec "
                    f"{directory / (arm + '.json')}", args)

    def test_policy_mutations_rejected_before_games(self):
        baseline = copy.deepcopy(self.p)
        changes = [
            ("arms", ["allfather-g3", "allfather-anchor"]),
            ("games_per_opening", 1),
            ("total_games", 4),
            ("concurrency", 2),
            ("clock", "1:00+0"),
            ("opening_indices", list(reversed(INDICES))),
            ("resource_accounting", "procfs_guess"),
            ("require_strict_resource_receipts", False),
            ("require_scope_no_leak", False),
            ("require_real_network", False),
            ("equal_compute", True),
            ("elo_gate", True),
            ("live_api_permitted", True),
            ("artifact_distribution_authorized", True),
            ("status", "QUALIFIED"),
        ]
        for name, value in changes:
            with self.subTest(name=name):
                modified = copy.deepcopy(baseline)
                modified[name] = value
                with self.assertRaises(QualificationError):
                    policy(modified)


if __name__ == "__main__":
    unittest.main()
