"""Negative controls: the campaign must be capable of failing honestly."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.local_game.common import (ARMS, QualificationError, contained, cpu_delta, load,
                                    policy, runtime_config, verify_g3_derivation)
from tools.local_game.runner import command, engine_options, retain_report_runs, schedule
from tools.local_game.validate import position, trace_searches, match_game, rules_result
from tools.local_game.probes import check_transition

HAS_CHESS = importlib.util.find_spec("chess") is not None


def events(lines):
    game = search = 0
    out = []
    for seq, (direction, line) in enumerate(lines, 1):
        if direction == "in" and line == "ucinewgame": game += 1
        if direction == "in" and line.startswith("go "): search += 1
        out.append({"seq": seq, "ns": seq * 1000, "direction": direction,
                    "line": line, "game": game, "search": search})
    return out


def good_trace():
    return events([("in", "ucinewgame"), ("in", "position startpos"),
                   ("in", "go wtime 30000 btime 30000"), ("out", "bestmove e2e4"),
                   ("in", "isready"), ("out", "readyok")])


class ContractTests(unittest.TestCase):
    def test_round_robin_is_balanced_and_distinct_from_lifecycle_driver(self):
        jobs = schedule(policy(ROOT), "required")
        baseline = [j for j in jobs if j["kind"] == "baseline"]
        self.assertEqual(len(baseline), 10)
        for arm in ARMS:
            self.assertEqual(sum(arm in j["arms"] for j in baseline), 4)
        self.assertTrue(all(j["driver_nodes"] is None for j in baseline))
        self.assertEqual(len({j["clock"] for j in baseline}), 1)
        self.assertEqual(len([j for j in schedule(policy(ROOT), "soak") if j["kind"] == "baseline"]), 100)


    def test_lc0_empty_default_option_is_not_serialized_into_fastchess_cli(self):
        source = load(ROOT / "config/allfather.online-hybrid.validation.json")
        options, _ = engine_options("lc0", source)
        self.assertNotIn("BackendOptions", options)
        job = {"id": "x", "kind": "baseline", "arms": ["lc0", "stockfish"],
               "clock": "0:30+1", "opening": "history.pgn", "restart": False,
               "driver_nodes": None, "allow_resource_denial": False}
        argv = command(job, ROOT / "build" / "dummy-local1", policy(ROOT), source,
                       Path("/tmp/fastchess"), write_specs=False)
        self.assertFalse(any(arg.startswith("option.BackendOptions=") for arg in argv))

    def test_prerequisite_replay_retention_uses_only_fresh_reported_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            for name in ("old", "fresh"):
                (source / name).mkdir()
                (source / name / "manifest.json").write_text("{}", encoding="utf-8")
            destination = root / "retained"
            retain_report_runs(source, destination, ["fresh"], {"old"}, "test")
            self.assertEqual({p.name for p in destination.iterdir()}, {"fresh"})
            with self.assertRaises(QualificationError):
                retain_report_runs(source, root / "bad", ["old"], {"old"}, "test")

    def test_replay_retention_rejects_nested_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            run = source / "fresh"
            run.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            (run / "escape").symlink_to(outside)
            with self.assertRaises(QualificationError):
                retain_report_runs(source, root / "retained", ["fresh"], set(), "test")

    def test_profile_is_only_output_relocation_and_not_a_new_policy(self):
        source = {"schema_version": 2, "online_time": {"max_move_ms": 4000},
                  "hybrid_authority": {"allow_skipped_extension_authority": False},
                  "shadow": {"replay_root": "old"},
                  "instances": {"stockfish-anchor": {"family": "stockfish", "options": {"Threads": 1}}}}
        original = copy.deepcopy(source)
        root, replay = Path("/tmp/repo"), Path("/tmp/repo/build/replays")
        derived = runtime_config(source, "allfather-g3", root, replay)
        verify_g3_derivation(source, derived, root, replay)
        self.assertEqual(source, original)
        derived["online_time"]["max_move_ms"] = 5000
        with self.assertRaises(QualificationError): verify_g3_derivation(source, derived, root, replay)
        anchor = runtime_config(source, "allfather-anchor", root, replay)
        self.assertEqual(list(anchor["instances"]), ["stockfish-anchor"])
        self.assertNotIn("online_time", anchor)
        self.assertNotIn("shadow", anchor)

    def test_exactly_one_terminal_and_session_scoped_repeated_position(self):
        trace = good_trace()
        groups = trace_searches(trace)
        self.assertEqual(groups[1][0]["move"], "e2e4")
        more = events([("in", "ucinewgame"), ("in", "position startpos"),
                       ("in", "go wtime 30000 btime 30000"), ("out", "bestmove e2e4")])
        for x in more:
            x["seq"] += len(trace); x["ns"] += len(trace) * 1000
            x["game"] += 1; x["search"] += 1
        self.assertEqual(set(trace_searches(trace + more)), {1, 2})

    def test_skipped_game_ordinal_is_rejected(self):
        trace = good_trace()
        more = events([("in", "ucinewgame"), ("in", "position startpos"),
                       ("in", "go wtime 30000 btime 30000"), ("out", "bestmove e2e4")])
        for x in more:
            x["seq"] += len(trace)
            x["ns"] += len(trace) * 1000
            x["game"] += 3
            x["search"] += 1
        with self.assertRaises(QualificationError):
            trace_searches(trace + more)

    def test_duplicate_terminal_rejected_even_after_readyok(self):
        trace = good_trace()
        trace.append({**trace[-1], "seq": 7, "ns": 7000, "line": "bestmove e2e4"})
        with self.assertRaisesRegex(QualificationError, "duplicate"):
            trace_searches(trace)

    def test_missing_null_stale_or_overlapping_terminal_rejected(self):
        for mutation in ("missing", "null", "stale", "overlap", "sequence"):
            with self.subTest(mutation=mutation):
                trace = good_trace()
                if mutation == "missing": trace = trace[:3]
                elif mutation == "null": trace[3]["line"] = "bestmove 0000"
                elif mutation == "stale": trace[3]["search"] = 99
                elif mutation == "overlap": trace[3].update(direction="in", line="go wtime 1 btime 1")
                elif mutation == "sequence": trace[3]["seq"] = 10
                with self.assertRaises(QualificationError): trace_searches(trace)

    def test_process_identity_change_is_missing_not_zero(self):
        a = {"10:1": {"cpu_ms": 100}}
        self.assertEqual(cpu_delta(a, {"10:1": {"cpu_ms": 120}}), 20)
        self.assertIsNone(cpu_delta(a, {"10:2": {"cpu_ms": 0}}))
        self.assertIsNone(cpu_delta(a, {}))
        self.assertIsNone(cpu_delta(a, {"10:1": {"cpu_ms": 99}}))

    def test_json_and_artifact_integrity_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for text in ('{"x": NaN}', '{"x": 1, "x": 2}'):
                p = root / "bad.json"; p.write_text(text)
                with self.assertRaises(QualificationError): load(p)
            for path in ("../escape", "/tmp/escape", ""):
                with self.assertRaises(QualificationError): contained(root, path)
            (root / "link").symlink_to("/tmp")
            with self.assertRaises(QualificationError): contained(root, "link/escape")


@unittest.skipUnless(HAS_CHESS, "install pinned qualification/local-game-requirements.txt")
class RulesTests(unittest.TestCase):
    def test_actual_rule_transitions(self):
        cases = load(ROOT / "tests/fixtures/local_full_game/rule-probes.json")
        for case in cases:
            with self.subTest(case=case["id"]): check_transition(case, case["move"])

    def test_fen_cannot_replace_repetition_history(self):
        import chess
        case = next(c for c in load(ROOT / "tests/fixtures/local_full_game/rule-probes.json")
                    if c["expect"] == "threefold")
        board = check_transition(case, case["move"])
        self.assertTrue(board.is_repetition(3))
        self.assertFalse(chess.Board(board.fen()).is_repetition(3))
        with self.assertRaises(QualificationError): position("position startpos moves e2e5")

    def test_pgn_trace_bijection_and_rules_result(self):
        import chess, chess.pgn
        board = chess.Board()
        moves = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]
        streams = {"stockfish": [], "reckless": []}
        history = []
        wtime = btime = 31000
        for index, uci in enumerate(moves):
            arm = "stockfish" if board.turn else "reckless"
            streams[arm].append({
                "position": "position startpos" + (" moves " + " ".join(history) if history else ""),
                "command": f"go wtime {wtime} btime {btime} winc 1000 binc 1000",
                "move": uci, "search": len(streams[arm])+1, "game": 1, "session_id": arm,
                "sent_ns": index*100, "received_ns": index*100+10,
                "metrics": {"cpu_ms_observed": 1},
            })
            # Zero elapsed is the maximal legal growth path: the mover receives
            # exactly one increment while the opponent clock is unchanged.
            if board.turn:
                wtime += 1000
            else:
                btime += 1000
            history.append(uci); board.push_uci(uci)
        game = chess.pgn.Game.from_board(board)
        game.headers.update(White="stockfish", Black="reckless", Result="1-0", Termination="normal")
        plan = {"clock": "0:30+1", "driver_nodes": None}
        self.assertEqual(len(match_game(game, streams, False, None, plan)), 7)
        streams["stockfish"][1]["position"] = "position startpos"
        with self.assertRaisesRegex(QualificationError, "history"):
            match_game(game, streams, False, None, plan)
        self.assertEqual(rules_result(board), "1-0")
        self.assertIsNone(rules_result(chess.Board()))


if __name__ == "__main__":
    unittest.main()
