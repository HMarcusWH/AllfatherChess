#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.local_game.common import ROOT
from tools.meta1.mini_e2e import run as run_fastchess_mini
from tools.meta1.paired import arm_telemetry, paired_blocks


class Meta1MiniEvidenceTests(unittest.TestCase):
    def test_four_game_fixture_flows_through_paired_report(self):
        payload = json.loads(
            (ROOT / "tests/fixtures/meta1-mini/paired-evidence-v1.json").read_text(
                encoding="utf-8"
            )
        )
        blocks, summary = paired_blocks(
            payload["games"],
            payload["plies"],
            expected_blocks=2,
        )
        telemetry = arm_telemetry(payload["plies"])
        self.assertEqual(len(blocks), 2)
        self.assertEqual(summary["blocks"], 2)
        self.assertEqual(summary["live_points"], 1.5)
        self.assertEqual(summary["control_points"], 2.5)
        self.assertEqual(
            telemetry["allfather-orchestrated"]["hybrid_interventions"],
            2,
        )

    @unittest.skipUnless(
        os.environ.get("META1_FASTCHESS"),
        "set META1_FASTCHESS to run pinned Fastchess smoke",
    )
    def test_pinned_fastchess_executes_two_reversed_pairs(self):
        fastchess = Path(os.environ["META1_FASTCHESS"]).resolve()
        output = ROOT / "build/test-results/meta1-mini-unittest"
        shutil.rmtree(output, ignore_errors=True)
        try:
            report = run_fastchess_mini(fastchess, output)
            self.assertEqual(report["games"], 4)
            self.assertEqual(report["paired_summary"]["blocks"], 2)
        finally:
            shutil.rmtree(output, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
