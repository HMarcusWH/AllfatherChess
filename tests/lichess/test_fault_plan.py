"""Exact, single-shot ONLINE-4A fault scheduling contracts (no engines)."""
import copy
import threading
import unittest
from pathlib import Path

from tests.lichess.fault_plan import FaultScheduler, load_scenarios, parse_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "tests/fixtures/lichess/recovery_scenarios.json"


class FaultPlanTests(unittest.TestCase):
    def test_frozen_scenarios_and_selection(self):
        rows = load_scenarios(SCENARIOS)
        self.assertEqual(set(rows), {f"R{i:02d}" for i in range(15)})
        self.assertEqual(rows["R02"]["action"], "commit_then_close")
        self.assertEqual(rows["R00"]["colors"], ["white", "black"])

    def test_one_shot_ordinal_is_deterministic_even_under_concurrency(self):
        row = {"id": "R02", "mode": "integration", "colors": ["white"],
               "action": "commit_then_close", "target": {"endpoint": "move_post", "ordinal": 3}}
        f = FaultScheduler(row)
        results = []
        def hit():
            results.append(f.hit("move_post", ply=1, request_id="test"))
        workers = [threading.Thread(target=hit) for _ in range(30)]
        for t in workers: t.start()
        for t in workers: t.join()
        self.assertEqual(results.count("commit_then_close"), 1)
        self.assertEqual(len(f.evidence()["activations"]), 1)
        self.assertEqual(f.evidence()["activations"][0]["endpoint_ordinal"], 3)
        self.assertEqual(f.evidence()["endpoint_counts"]["move_post"], 30)

    def test_invalid_specs_are_rejected_not_silently_ignored(self):
        normal = load_scenarios(SCENARIOS)["R02"]
        for key, value in (
            ("action", "untrusted_python"),
            ("colors", []),
            ("colors", ["white", "white"]),
            ("mode", "live"),
            ("target", {"endpoint": "game_state", "ordinal": 1}),
            ("target", {"endpoint": "move_post", "ordinal": True}),
            ("target", {"endpoint": "move_post", "ordinal": 0}),
        ):
            bad = copy.deepcopy(normal)
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                parse_scenario(bad)
        bad = dict(normal, other="ignored")
        with self.assertRaises(ValueError):
            parse_scenario(bad)


if __name__ == "__main__":
    unittest.main()
