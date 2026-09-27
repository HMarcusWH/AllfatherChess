"""Recorded search restrictions and numeric costs must match the frozen campaign."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.local_game.test_contracts import events
from tools.local_game.common import QualificationError
from tools.local_game.integrity import verify_session_commands, finite_metrics
from tools.local_game.validate import apply_scope_flags, verify_runner_log


class EffectiveCommandTests(unittest.TestCase):
    def test_effective_commands_cannot_sneak_a_baseline_work_limit(self):
        plan = {"clock": "0:30+1", "driver_nodes": None}
        lines = events([("in", "setoption name UCI_Chess960 value false"),
                        ("in", "ucinewgame"),
                        ("in", "position startpos"),
                        ("in", "go wtime 30000 btime 30000 winc 1000 binc 1000")])
        verify_session_commands(lines, "allfather-g3", plan, {})
        for suffix in (" nodes 1", " movetime 500", " searchmoves e2e4"):
            changed = copy.deepcopy(lines)
            changed[-1]["line"] += suffix
            with self.assertRaises(QualificationError):
                verify_session_commands(changed, "allfather-g3", plan, {})
        changed = copy.deepcopy(lines)
        changed[-1]["line"] = changed[-1]["line"].replace("binc 1000", "binc 2000")
        with self.assertRaises(QualificationError):
            verify_session_commands(changed, "allfather-g3", plan, {})


    def test_known_scoreless_wrapper_warning_is_scoped_but_other_warnings_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.log"
            path.write_text(
                "Warning; No info line available to extract score from engine allfather-g3\n",
                encoding="utf-8",
            )
            verify_runner_log(path, {"id": "known"})
            path.write_text("Warning; unexpected thing\n", encoding="utf-8")
            with self.assertRaises(QualificationError):
                verify_runner_log(path, {"id": "bad"})

    def test_partial_soak_shard_cannot_claim_full_campaign(self):
        report = {"claim_boundary": {"full_game_lifecycle": False}}
        apply_scope_flags(
            report,
            mode="soak",
            shard={"index": 4, "count": 10},
            errors=[],
        )
        self.assertTrue(report["passed"])
        self.assertTrue(report["shard_passed"])
        self.assertEqual(report["execution_scope"], "partial_soak_shard")
        self.assertFalse(report["baseline_is_complete"])
        self.assertFalse(report["aggregate_soak_complete"])
        self.assertFalse(report["claim_boundary"]["full_game_lifecycle"])

    def test_nonfinite_or_missing_measurement_cannot_be_presented_as_cost(self):
        for value in (None, float("nan"), float("inf"), -1, True):
            with self.assertRaises(QualificationError):
                finite_metrics({"search_metrics": [{"cpu_ms_observed": value}],
                                "resources": {"cpu_complete": True,
                                              "reaped_subtree_cpu_ms": 1,
                                              "proxy_cpu_ms": 1}})
        with self.assertRaises(QualificationError):
            finite_metrics({"search_metrics": [{"cpu_ms_observed": 1}],
                            "resources": {"cpu_complete": False,
                                          "reaped_subtree_cpu_ms": 1,
                                          "proxy_cpu_ms": 1}})

    def test_first_transmitted_clock_must_match_frozen_control(self):
        plan = {"clock": "0:30+1", "driver_nodes": None}
        lines = events([
            ("in", "setoption name UCI_Chess960 value false"),
            ("in", "ucinewgame"),
            ("in", "position startpos"),
            ("in", "go wtime 30000 btime 30000 winc 1000 binc 1000"),
        ])
        verify_session_commands(lines, "allfather-g3", plan, {})
        changed = copy.deepcopy(lines)
        changed[-1]["line"] = "go wtime 1 btime 1 winc 1000 binc 1000"
        with self.assertRaises(QualificationError):
            verify_session_commands(changed, "allfather-g3", plan, {})


if __name__ == "__main__":
    unittest.main()
