"""Freeze the native-clock matrix and force truthful non-qualifying modes."""
from __future__ import annotations
import json
from pathlib import Path
import unittest
from common.search_request import parse_position_command
from controller.strength.native_clock import NativeClockSettings,make_native_plan
from controller.strength.contracts import StrengthContractError
from tools.strength_lab.e00_evidence import reconstruct_stdout_times

ROOT=Path(__file__).resolve().parents[2]
POLICY=ROOT/"qualification/strength/e00-native-equivalence-v1.json"

class NativeMatrixContracts(unittest.TestCase):
    def test_frozen_real_native_matrix_has_eight_supported_requests(self):
        policy=json.loads(POLICY.read_text(encoding="utf-8"))
        cases=policy["native_clock_cases_v1"]
        self.assertEqual(len(cases),policy["smoke_count"])
        for side,position in (("w","position startpos"),
                              ("b","position startpos moves e2e4")):
            for go in cases:
                with self.subTest(side=side,go=go):
                    plan=make_native_plan(command=go,
                        position=parse_position_command(position),generation=1,
                        settings=NativeClockSettings(),
                        received_monotonic=1.0,controller_cpu_started_ns=1)
                    self.assertEqual(plan.anchor_go_command,go)
                    self.assertGreater(plan.hard_budget_ms,plan.soft_budget_ms)
        self.assertFalse(policy["claims"]["native_clock_qualified"])
        self.assertFalse(policy["claims"]["strength_qualified"])

    def test_mixed_clock_sequence_is_not_accepted_as_one_request(self):
        rows=[
            {"direction":"stdin","line":"go movetime 1800","ns":100},
            {"direction":"stdout","line":"bestmove e2e4","ns":200},
            {"direction":"stdin","line":"go wtime 30000 btime 30000","ns":300},
            {"direction":"stdout","line":"bestmove e7e5","ns":400}
        ]
        x=reconstruct_stdout_times(rows,["go movetime 1800",
                                         "go wtime 30000 btime 30000"],2)
        self.assertEqual(len(x),2)
        with self.assertRaises(ValueError):
            reconstruct_stdout_times(rows,["go movetime 1800","go nodes 1"],2)

if __name__=="__main__":
    unittest.main()
