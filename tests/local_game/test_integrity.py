"""Recorded search restrictions and numeric costs must match the frozen campaign."""
import copy
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.local_game.test_contracts import events
from tools.local_game.common import QualificationError
from tools.local_game.integrity import verify_session_commands, finite_metrics


class EffectiveCommandTests(unittest.TestCase):
    def test_effective_commands_cannot_sneak_a_baseline_work_limit(self):
        plan = {"clock": "0:30+1", "driver_nodes": None}
        lines = events([("in", "setoption name UCI_Chess960 value false"),
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

    def test_nonfinite_measurement_cannot_be_presented_as_cost(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(QualificationError):
                finite_metrics({"search_metrics": [{"cpu_ms_observed": value}],
                                "resources": {"reaped_subtree_cpu_ms": 1, "proxy_cpu_ms": 1}})


if __name__ == "__main__":
    unittest.main()
