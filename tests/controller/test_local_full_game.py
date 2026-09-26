#!/usr/bin/env python3
"""Fast LOCAL-1 policy, parsing, and cross-game lifecycle regressions."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tests.harness.local_full_game import (
    complete_pairings,
    parse_proxy_transcript,
    parse_uci_pgn,
)
from tests.controller.online_helpers import shell_fixture,wait_for


def load(path:str):
    return json.loads((ROOT/path).read_text(encoding="utf-8"))


def bestmoves(output):
    return [line for line in output.getvalue().splitlines() if line.startswith("bestmove ")]


class LocalFullGamePolicyTests(unittest.TestCase):
    def test_hybrid_profile_is_exact_g3_runtime_except_replay_root(self):
        g3=load("config/allfather.online-hybrid.validation.json")
        local=load("config/allfather.local-game.validation.json")
        self.assertEqual(
            local["shadow"]["replay_root"],
            "build/replays-local-full-game-hybrid",
        )
        g3["shadow"]["replay_root"]=local["shadow"]["replay_root"]
        self.assertEqual(local,g3)

    def test_control_is_authorization_ablation_not_fake_wrapper_baseline(self):
        hybrid=load("config/allfather.local-game.validation.json")
        control=load("config/allfather.local-control.validation.json")
        self.assertNotIn("hybrid_authority",control)
        expected=json.loads(json.dumps(hybrid))
        expected.pop("hybrid_authority")
        expected["shadow"]["replay_root"]="build/replays-local-full-game-control"
        self.assertEqual(control,expected)

    def test_five_arm_baseline_contains_every_pair_exactly_once(self):
        policy=load("qualification/local-full-game.json")
        arms=policy["baseline"]["arms"]
        self.assertEqual(
            arms,
            ["stockfish","reckless","lc0","allfather-control","allfather-hybrid"],
        )
        self.assertEqual(policy["baseline"]["pairings"],complete_pairings(arms))
        self.assertEqual(len(policy["baseline"]["pairings"]),10)
        self.assertFalse(policy["claim_boundary"]["equal_resource_comparison"])
        self.assertFalse(policy["claim_boundary"]["superiority"])

    def test_special_history_fixtures_are_explicit_not_inferred(self):
        policy=load("qualification/local-full-game.json")
        by_id={item["id"]:item for item in policy["required_cases"]}
        self.assertIn("e1g1",by_id["castling-history"]["expected_history_prefix"])
        self.assertIn("e5d6",by_id["en-passant-history"]["expected_history_prefix"])
        self.assertIn("a7a8q",by_id["promotion-history"]["expected_history_prefix"])
        self.assertEqual(
            by_id["repetition-history"]["expected_history_prefix"],
            ["g1f3","g8f6","f3g1","f6g8"],
        )
        self.assertTrue(by_id["terminal-checkmate"]["terminal_without_search"])
        self.assertTrue(by_id["terminal-stalemate"]["terminal_without_search"])

    def test_fastchess_lock_is_content_addressed(self):
        lock=load("qualification/fastchess.lock.json")
        self.assertEqual(lock["commit"],"f618e34540f94f4719ad3817950618dabe441318")
        self.assertEqual(lock["tree"],"7af51c972096164b267d617ea4c32a856a17b586")
        self.assertEqual(lock["license"],"MIT")


class LocalFullGameParserTests(unittest.TestCase):
    def test_uci_pgn_parser_preserves_full_history(self):
        text='''[Event "x"]
[White "Allfather-Hybrid"]
[Black "Stockfish"]
[Result "1/2-1/2"]
[Termination "normal"]

1. e2e4 e7e5 2. g1f3 b8c6 1/2-1/2
'''
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"x.pgn"
            path.write_text(text,encoding="utf-8")
            games=parse_uci_pgn(path)
        self.assertEqual(len(games),1)
        self.assertEqual(games[0]["moves"],["e2e4","e7e5","g1f3","b8c6"])

    def test_proxy_parser_detects_duplicate_terminal_output(self):
        rows=[
            {"schema_version":1,"event":"proxy_start","proxy_instance":"p","seq":1},
            {"schema_version":1,"event":"line","direction":"in","line":"position startpos",
             "proxy_instance":"p","seq":2,"game_index":0,"request_index":None,
             "position":"position startpos"},
            {"schema_version":1,"event":"line","direction":"in","line":"go wtime 1000 btime 1000",
             "proxy_instance":"p","seq":3,"game_index":0,"request_index":1,
             "position":"position startpos"},
            {"schema_version":1,"event":"line","direction":"out","line":"bestmove e2e4",
             "proxy_instance":"p","seq":4,"game_index":0,"request_index":1,
             "position":"position startpos"},
            {"schema_version":1,"event":"protocol_error","direction":"out",
             "line":"bestmove d2d4","reason":"duplicate bestmove for request",
             "proxy_instance":"p","seq":5,"game_index":0,"request_index":1,
             "position":"position startpos"},
            {"schema_version":1,"event":"line","direction":"out","line":"bestmove d2d4",
             "proxy_instance":"p","seq":6,"game_index":0,"request_index":1,
             "position":"position startpos"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"t.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
            parsed=parse_proxy_transcript(path)
        self.assertEqual(len(parsed["requests"]),1)
        self.assertEqual(len(parsed["requests"][0]["bestmoves"]),2)
        self.assertTrue(parsed["protocol_errors"])


class LocalFullGameRuntimeTests(unittest.TestCase):
    def test_multiple_plies_then_ucinewgame_keep_anchor_healthy(self):
        with shell_fixture() as (shell,manager,shadow,out,tmp):
            shell.handle_command("go movetime 500")
            wait_for(lambda:len(bestmoves(out))==1)
            wait_for(lambda:list(tmp.glob("replays/*/manifest.json")),3)

            shell.handle_command("position startpos moves e2e4")
            shell.handle_command("go movetime 500")
            wait_for(lambda:len(bestmoves(out))==2)
            wait_for(lambda:len(list(tmp.glob("replays/*/manifest.json")))>=2,3)

            shell.handle_command("ucinewgame")
            shell.handle_command("position startpos")
            shell.handle_command("go movetime 500")
            wait_for(lambda:len(bestmoves(out))==3)
            self.assertTrue(manager.healthy)
            self.assertEqual(bestmoves(out),["bestmove e2e4"]*3)

    def test_late_shadow_exit_degrades_evidence_not_next_game_authority(self):
        with shell_fixture(
            args={"lc0-shadow":["--exit-on-go-number","2"]}
        ) as (shell,manager,shadow,out,tmp):
            shell.handle_command("go movetime 500")
            wait_for(lambda:len(bestmoves(out))==1)
            wait_for(lambda:list(tmp.glob("replays/*/manifest.json")),3)

            shell.handle_command("position startpos moves e2e4")
            shell.handle_command("go movetime 500")
            wait_for(lambda:len(bestmoves(out))==2)
            wait_for(lambda:not manager.shadow_available("lc0-shadow"),3)

            shell.handle_command("ucinewgame")
            shell.handle_command("position startpos")
            shell.handle_command("go movetime 500")
            wait_for(lambda:len(bestmoves(out))==3)
            self.assertTrue(manager.healthy)
            self.assertFalse(manager.shadow_available("lc0-shadow"))
            self.assertEqual(bestmoves(out)[-1],"bestmove e2e4")


if __name__=="__main__":
    unittest.main()
