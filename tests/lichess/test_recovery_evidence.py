"""Negative controls for independent ONLINE-4A evidence validation.

Do not execute engine builds in this test: ONLINE-3B bridge-contracts discovers
all tests/lichess/test_*.py, so these must stay hermetic.
"""
from __future__ import annotations
import copy
import importlib.util
import json
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
    def test_http_target_policy_is_source_attested_and_tamper_detected(self):
        exact_sha="a"*40
        names=json.loads((ROOT/"qualification/online-recovery-v1.json")
                         .read_text())["attested_source_paths"]
        path="tests/lichess/http_target_contract.py"
        self.assertEqual(names.count(path),1)
        file_hashes={name:QUALIFIER.digest(ROOT/name) for name in names}
        document={"source_commit":exact_sha,"files":file_hashes}
        QUALIFIER.verify_sources(document,exact_sha)
        forged=copy.deepcopy(document)
        forged["files"][path]="0"*64
        with self.assertRaisesRegex(ValueError,"source file identity drift"):
            QUALIFIER.verify_sources(forged,exact_sha)

    def trace(self):
        board = chess.Board()
        before = board.fen()
        board.push_uci("e2e4")
        return [
            {"seq": 1, "event": "move_attempt", "game_id": "off3b001",
             "uci": "e2e4", "ply_at_receipt": 0,
             "path": "/api/bot/game/off3b001/move/e2e4"},
            {"seq": 2, "event": "board_commit", "game_id": "off3b001",
             "origin": "allfather", "request_id": "request-1",
             "uci": "e2e4", "ply_before": 0, "ply_after": 1,
             "fen_before": before, "fen_after": board.fen()},
        ]

    def game(self):
        return {"moves": ["e2e4"], "accepted": [{"ply": 1, "uci": "e2e4"}],
                "complete": False, "color":"white"}

    def test_independently_replays_accepted_move(self):
        x = QUALIFIER.verify_trace(self.trace(), self.game())
        self.assertEqual(x["bot_commits"], 1)
        self.assertEqual(x["http_attempts"], 1)

    def test_legal_trace_move_must_match_actual_url(self):
        for path in ("/api/bot/game/off3b001/move/d2d4",
                     "/api/bot/game/off3b001/move/%65%32e4",
                     "/api/bot/game/off3b001/move/e2e4?x=1",
                     "/api/bot/game/off3b001/move/0000"):
            rows=self.trace()
            rows[0]["path"]=path
            with self.subTest(path=path),self.assertRaisesRegex(
                    ValueError,"move path differs"):
                QUALIFIER.verify_trace(rows,self.game())

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

    def test_position_illegal_syntactically_valid_attempt_is_not_safe(self):
        rows=self.trace()
        illegal={"seq":1,"event":"move_attempt","game_id":"off3b001",
                 "uci":"e7e5","path":"/api/bot/game/off3b001/move/e7e5",
                 "ply_at_receipt":0}
        rows[0]["seq"]=2
        rows[1]["seq"]=3
        rows[1]["request_id"]="request-2"
        rows.insert(0,illegal)
        result=QUALIFIER.verify_trace(rows,self.game())
        self.assertEqual(len(result["illegal_attempts"]),1)
        self.assertEqual(result["illegal_attempts"][0]["uci"],"e7e5")
        self.assertEqual(result["illegal_attempts"][0]["reason"],
                         "position_illegal_or_wrong_turn")
        self.assertEqual(result["stale_retry_attempts"],[])
        # A coherent, fully committed R00 game does not erase the illegal
        # earlier HTTP attempt, even when it returns HTTP 400.
        self.assertFalse(not result["illegal_attempts"])

    def test_wrong_receipt_ply_is_evidence_corruption(self):
        rows=self.trace()
        rows[0]["ply_at_receipt"]=1
        with self.assertRaisesRegex(ValueError,"receipt ply"):
            QUALIFIER.verify_trace(rows,self.game())

    def test_second_position_legal_request_may_commit_but_is_unsafe_retry(self):
        # R01's first HTTP request drops before commit; the frozen upstream
        # may submit a second move on the same unchanged position. The second
        # request can commit legally, but doing it blindly is an unsafe witness,
        # not an invalid trace or a safe-continuation positive.
        rows=self.trace()
        first=dict(rows[0])
        rows[0]["seq"]=2
        rows[1]["seq"]=3
        rows[1]["request_id"]="request-2"
        rows.insert(0,first)
        result=QUALIFIER.verify_trace(rows,self.game())
        self.assertEqual(result["bot_commits"],1)
        self.assertEqual(result["http_attempts"],2)
        self.assertEqual(len(result["illegal_attempts"]),1)
        self.assertEqual(result["illegal_attempts"][0]["reason"],
                         "repeated_move_submission_same_ply")
        self.assertEqual(result["illegal_attempts"][0]["original_request_id"],
                         "request-1")

    def test_stale_retry_requires_prior_committed_legal_move_and_lost_ack(self):
        rows=self.trace()
        rows[1]["seq"]=3
        rows.insert(1,{"seq":2,"event":"fault_activated",
                       "action":"commit_then_close","endpoint":"move_post",
                       "request_id":"request-1"})
        rows.extend([
            {"seq":4,"event":"connection_close_requested",
             "request_id":"request-1"},
            {"seq":5,"event":"move_attempt","game_id":"off3b001",
             "uci":"e2e4","path":"/api/bot/game/off3b001/move/e2e4",
             "ply_at_receipt":1},
        ])
        observed=QUALIFIER.verify_trace(rows,self.game())
        self.assertEqual(observed["illegal_attempts"],[])
        self.assertEqual(len(observed["stale_retry_attempts"]),1)
        self.assertEqual(observed["stale_retry_attempts"][0]["original_request_id"],
                         "request-1")
        without_loss=[r for r in rows if r["event"]!="connection_close_requested"]
        # Fill the removed sequence gap to maintain an otherwise valid trace.
        without_loss[-1]["seq"]=4
        rejected=QUALIFIER.verify_trace(without_loss,self.game())
        self.assertEqual(rejected["stale_retry_attempts"],[])
        self.assertEqual(len(rejected["illegal_attempts"]),1)

    def test_move_audit_requires_exact_request_id_and_multiset(self):
        first="/api/bot/game/off3b001/move/e2e4"
        second="/api/bot/game/off3b001/move/d2d4"
        trace=[{"seq":1,"event":"move_attempt",
                "path":first,"uci":"e2e4","game_id":"off3b001"}]
        http=[{"method":"POST","path":first,
               "request_id":"request-1","status":200}]
        self.assertEqual(QUALIFIER.verify_exact_http_move_audit(http,trace),1)
        invalid=[
            http+[{"method":"POST","path":second,"request_id":"request-7",
                   "status":200}],
            http+[dict(http[0])],
            [{**http[0],"request_id":"request-2"}],
            [],
        ]
        for rows in invalid:
            with self.subTest(rows=rows),self.assertRaisesRegex(
                    ValueError,"HTTP move records and trace attempts differ"):
                QUALIFIER.verify_exact_http_move_audit(rows,trace)
        with self.assertRaisesRegex(ValueError,"traced move endpoint identity"):
            QUALIFIER.verify_exact_http_move_audit(
                http,[{**trace[0],"uci":"d2d4"}])

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

    def test_null_move_submission_is_unsafe_but_valid_retry_is_not_misclassified(self):
        attempts = [
            {"method": "POST", "path": "/api/bot/game/off3b001/move/0000", "status": 400},
            {"method": "POST", "path": "/api/bot/game/off3b001/move/e2e4", "status": 409},
            {"method": "POST", "path": "/api/bot/game/off3b001/move/garbage", "status": 400},
        ]
        bad = QUALIFIER.invalid_move_submissions(attempts)
        self.assertEqual([r["uci"] for r in bad], ["0000", "garbage"])
        self.assertEqual(QUALIFIER.invalid_move_submissions([attempts[1]]), [])

    def test_sha256_of_stream_payload_is_stable(self):
        x = {"type": "gameState", "moves": "e2e4", "wtime": 1000}
        self.assertEqual(state_digest(x), state_digest(dict(reversed(list(x.items())))))


    def test_delay_state_requires_actual_monotonic_gap(self):
        witnessed = {"seq": 1, "sid": 4}
        base = [
            {**witnessed, "event": "fault_activated", "action": "delay_state"},
            {"seq": 2, "sid": 4, "event": "stream_fault_source",
             "action": "delay_state", "monotonic_ns": 1000000000,
             "original_sha256": "a"*64, "original_moves": "e2e4"},
            {"seq": 3, "sid": 4, "event": "stream_emit", "action": "delay_state",
             "monotonic_ns": 1400000000, "payload_sha256": "a"*64,
             "moves": "e2e4"},
        ]
        QUALIFIER.verify_state_fault("delay_state", witnessed, [base[-1]], base)
        stale = copy.deepcopy(base)
        stale[-1]["monotonic_ns"] = 1100000000
        with self.assertRaisesRegex(ValueError, "delay"):
            QUALIFIER.verify_state_fault("delay_state", witnessed, [stale[-1]], stale)

    def test_stale_state_must_deliver_authoritative_predecessor(self):
        before = chess.Board()
        prefen = before.fen()
        before.push_uci("e2e4")
        trace = [
            {"seq": 1, "event": "board_commit", "uci": "e2e4"},
            {"seq": 2, "sid": 4, "event": "fault_activated", "action": "stale_state"},
            {"seq": 3, "sid": 4, "event": "stream_fault_source",
             "action": "stale_state", "monotonic_ns": 2000000000,
             "original_sha256": "a"*64, "original_moves": "e2e4",
             "predecessor_sha256": "b"*64, "predecessor_moves": ""},
            {"seq": 4, "sid": 4, "event": "stream_emit", "action": "stale_state",
             "monotonic_ns": 2000000001,
             "payload_sha256": "b"*64, "moves": ""},
        ]
        witnessed=trace[1]
        QUALIFIER.verify_state_fault("stale_state", witnessed, [trace[-1]], trace)
        for change in (
            {"payload_sha256":"a"*64, "moves":"e2e4"},
            {"payload_sha256":"b"*64, "moves":"e2e4"},
        ):
            bad=copy.deepcopy(trace)
            bad[-1].update(change)
            with self.assertRaises(ValueError):
                QUALIFIER.verify_state_fault("stale_state", witnessed, [bad[-1]], bad)


if __name__ == "__main__":
    unittest.main()
