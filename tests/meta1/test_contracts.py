#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest

from tools.local_game.common import ROOT, load, runtime_config, verify_controller_derivation
from tools.meta1.common import ARMS, opening_blocks, policy, schedule


class Meta1ContractTests(unittest.TestCase):
    def test_frozen_schedule_is_fifty_reversed_pairs(self):
        p = policy()
        jobs = schedule(p)
        self.assertEqual(len(jobs), 50)
        self.assertEqual(len(opening_blocks(ROOT / p["opening_file"])), 50)
        self.assertEqual(2 * len(jobs), 100)
        block_hashes = set()
        for index, job in enumerate(jobs):
            expected = list(ARMS if index % 2 == 0 else tuple(reversed(ARMS)))
            self.assertEqual(job["opening_index"], index)
            self.assertEqual(job["arms"], expected)
            self.assertIsNone(job["driver_nodes"])
            self.assertFalse(job["allow_resource_denial"])
            self.assertEqual(len(job["opening_sha256"]), 64)
            block_hashes.add(job["opening_sha256"])
        self.assertEqual(len(block_hashes), 50)

    def test_control_runtime_diff_is_only_relocation_plus_outward_mode(self):
        source = load(ROOT / "config/allfather.orchestrated-v1.validation.json")
        original = copy.deepcopy(source)
        replay = ROOT / "build/test-meta1-control-replays"
        derived = runtime_config(source, "allfather-anchor-control", ROOT, replay)
        verify_controller_derivation(
            source, derived, "allfather-anchor-control", ROOT, replay,
        )
        self.assertEqual(source, original)
        self.assertEqual(
            derived["hybrid_authority"]["outward_mode"],
            "anchor_control_v1",
        )
        self.assertEqual(
            derived["hybrid_authority"]["policy"],
            source["hybrid_authority"]["policy"],
        )


if __name__ == "__main__":
    unittest.main()
