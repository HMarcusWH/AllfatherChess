"""Negative controls for independent ONLINE-4A evidence validation.

Do not execute engine builds in this test: ONLINE-3B bridge-contracts discovers
all tests/lichess/test_*.py, so these must stay hermetic.
"""
from __future__ import annotations
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

import chess
from tests.lichess.fault_plan import load_scenarios
from tests.lichess.transport_trace import state_digest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/qualify-online-recovery.py"
SPEC = importlib.util.spec_from_file_location("online4a_qualifier", MODULE_PATH)
QUALIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFIER)
SCENARIOS = load_scenarios(ROOT / "tests/fixtures/lichess/recovery_scenarios.json")


class IndependentEvidenceTests(unittest.TestCase):
    def trace(self):
        board = chess.Board()
        before = board.fen()
        board.push_uci("e2e4")
        return [
            {"seq": 1, "event": "move_attempt", "game_id": "off3b001",
             "uci": "e2e4", "ply_at_receipt": 0},
            {"seq": 2, "event": "board_commit", "game_id": "off3b001",
             "origin": "allfather", "request_id": "request-1",
             "uci": "e2e4", "ply_before": 0, "ply_after": 1,
             "fen_before": before, "fen_after": board.fen()},
        ]

    def game(self):
        return {"moves": ["e2e4"], "accepted": [{"ply": 1, "uci": "e2e4"}],
                "complete": False}

    def test_independently_replays_accepted_move(self):
        x = QUALIFIER.verify_trace(self.trace(), self.game())
        self.assertEqual(x["bot_commits"], 1)
        self.assertEqual(x["http_attempts"], 1)

    def test_rejects_invalid_sequence_and_illegal_commit(self):
        t = self.trace()
        for bad in (
            dict(t[1], seq=3),
            dict(t[1], ply_before=4),
            dict(t[1], uci="e7e5"),
            dict(t[1], fen_after="bogus"),
            dict(t[1], request_id="not-observed"),
        ):
            changed = copy.deepcopy(t)
            changed[1] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                QUALIFIER.verify_trace(changed, self.game())

    def test_does_not_confuse_http_504_with_uncommitted_move(self):
        t = self.trace()
        t.insert(1, {"seq": 2, "event": "fault_activated", "action": "commit_then_504",
                     "endpoint": "move_post", "request_id": "request-1", "ply": 0})
        t[2]["seq"] = 3
        t.append({"seq": 4, "event": "response_write_completed", "status": 504})
        a = {"scenario": SCENARIOS["R03"], "activations": [{
            "action": "commit_then_504", "endpoint": "move_post",
            "endpoint_ordinal": 1, "request_id": "request-1"}]}
        QUALIFIER.verify_scenario_witness(SCENARIOS["R03"], a, t, {})
        t[-1]["status"] = 200
        with self.assertRaises(ValueError):
            QUALIFIER.verify_scenario_witness(SCENARIOS["R03"], a, t, {})

    def test_rejects_missing_fault_witness_even_if_producer_claims_success(self):
        with self.assertRaisesRegex(ValueError, "not activated"):
            QUALIFIER.verify_scenario_witness(
                SCENARIOS["R02"], {"activations": []}, self.trace(),
                {"passed": True})

    def test_rejects_missing_or_tampered_replay_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "replay-index.json").write_text(
                '[{"manifest":"replays/id/manifest.json","run_id":"id",'
                '"generation":1,"position_id":"x","sha256":"wrong"}]'
            )
            with self.assertRaises(ValueError):
                QUALIFIER.validate_replays(root)

    def test_sha256_of_stream_payload_is_stable(self):
        x = {"type": "gameState", "moves": "e2e4", "wtime": 1000}
        self.assertEqual(state_digest(x), state_digest(dict(reversed(list(x.items())))))


if __name__ == "__main__":
    unittest.main()
