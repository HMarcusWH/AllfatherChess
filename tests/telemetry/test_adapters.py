#!/usr/bin/env python3
"""Static tests for read-only UCI-to-telemetry v1 adapters."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.telemetry import (
    Lc0TelemetryAdapter,
    RecklessTelemetryAdapter,
    StockfishTelemetryAdapter,
    TelemetryParseError,
)
from common.telemetry import STARTPOS_FEN, TelemetryError


def started(adapter):
    event = adapter.start(
        position={"base_fen": STARTPOS_FEN, "moves": []},
        request={"limits": [{"name": "nodes", "value": 128, "semantics": "uci.go.nodes"}]},
        observed_ms=0,
    )
    assert event["event_type"] == "search.started"
    return adapter


def stockfish_adapter():
    return started(
        StockfishTelemetryAdapter(
            search_id="sf-test",
            engine_instance="stockfish-0",
            position_id="fixture:startpos",
        )
    )


def reckless_adapter():
    return started(
        RecklessTelemetryAdapter(
            search_id="rk-test",
            engine_instance="reckless-0",
            position_id="fixture:startpos",
        )
    )


def lc0_adapter(score_type="centipawn"):
    return started(
        Lc0TelemetryAdapter(
            search_id=f"lc0-{score_type}",
            engine_instance="lc0-0",
            position_id="fixture:startpos",
            score_type=score_type,
        )
    )


class TelemetryAdapterTests(unittest.TestCase):
    def test_stockfish_cp_wdl_and_upper_bound(self):
        event = stockfish_adapter().consume(
            "info depth 12 seldepth 18 multipv 1 score cp 31 upperbound "
            "wdl 412 391 197 nodes 50000 time 63 nps 793650 hashfull 4 "
            "tbhits 0 pv e2e4 e7e5 g1f3",
            observed_ms=64,
        )[0]
        self.assertEqual(event["event_type"], "candidate.update")
        self.assertEqual(event["candidate"]["multipv_index"], 1)
        self.assertEqual(event["candidate"]["pv"][0], "e2e4")
        self.assertEqual(event["candidate"]["evaluations"][0]["semantics"], "stockfish.uci_cp")
        self.assertEqual(event["candidate"]["evaluations"][0]["bound"], "upper")
        self.assertEqual(event["candidate"]["evaluations"][1]["semantics"], "stockfish.uci_wdl")
        self.assertEqual(event["work"][0]["unit"], "nodes")
        self.assertEqual(event["engine_time"]["value"], 63)
        self.assertEqual(event["native"]["data"]["depth"], 12)

    def test_stockfish_mate(self):
        event = stockfish_adapter().consume(
            "info depth 20 multipv 1 score mate 3 nodes 90 time 2 pv h5h7",
            observed_ms=3,
        )[0]
        evaluation = event["candidate"]["evaluations"][0]
        self.assertEqual(evaluation["kind"], "mate")
        self.assertEqual(evaluation["semantics"], "stockfish.uci_mate")

    def test_reckless_score_and_bound_stays_reckless_native(self):
        event = reckless_adapter().consume(
            "info depth 8 seldepth 11 multipv 1 score cp -22 lowerbound "
            "nodes 1024 time 4 nps 250000 hashfull 0 tbhits 0 pv d2d4 d7d5",
            observed_ms=5,
        )[0]
        evaluation = event["candidate"]["evaluations"][0]
        self.assertEqual(evaluation["semantics"], "reckless.uci_cp")
        self.assertEqual(evaluation["bound"], "lower")

    def test_no_pv_info_is_native_event(self):
        event = reckless_adapter().consume("info depth 0 score mate 0", observed_ms=1)[0]
        self.assertEqual(event["event_type"], "native.event")
        self.assertEqual(event["native"]["schema"], "reckless.uci.v1")
        self.assertIn("score mate 0", event["native"]["data"]["raw"])

    def test_lc0_single_pv_defaults_to_multipv_one(self):
        event = lc0_adapter().consume(
            "info depth 4 seldepth 9 score cp 17 nodes 512 time 18 "
            "nps 1700 tbhits 0 pv g2g4 g7g6",
            observed_ms=20,
        )[0]
        self.assertEqual(event["candidate"]["multipv_index"], 1)
        self.assertEqual(event["work"][0]["unit"], "count")
        self.assertEqual(
            event["candidate"]["evaluations"][0]["semantics"],
            "lc0.uci_score.centipawn",
        )

    def test_lc0_explicit_multipv_is_preserved(self):
        event = lc0_adapter().consume(
            "info depth 4 multipv 2 score cp -5 pv d2d4 d7d5",
            observed_ms=3,
        )[0]
        self.assertEqual(event["candidate"]["multipv_index"], 2)

    def test_all_lc0_score_types_are_explicit_semantics(self):
        score_types = [
            "centipawn",
            "centipawn_with_drawscore",
            "centipawn_2019",
            "centipawn_2018",
            "win_percentage",
            "Q",
            "W-L",
            "WDL_mu",
        ]
        for index, score_type in enumerate(score_types):
            adapter = started(
                Lc0TelemetryAdapter(
                    search_id=f"lc0-score-{index}",
                    engine_instance="lc0-0",
                    position_id="fixture:startpos",
                    score_type=score_type,
                )
            )
            event = adapter.consume(
                "info depth 2 score cp 42 pv e2e4 e7e5",
                observed_ms=1,
            )[0]
            evaluation = event["candidate"]["evaluations"][0]
            self.assertEqual(evaluation["semantics"], f"lc0.uci_score.{score_type}")
            expected_kind = "cp" if score_type.startswith("centipawn") else "scalar"
            self.assertEqual(evaluation["kind"], expected_kind)

    def test_lc0_wdl_is_second_channel(self):
        event = lc0_adapter().consume(
            "info depth 2 score cp 11 wdl 401 338 261 pv e2e4 e7e5",
            observed_ms=2,
        )[0]
        evaluations = event["candidate"]["evaluations"]
        self.assertEqual(len(evaluations), 2)
        self.assertEqual(evaluations[1]["kind"], "wdl")
        self.assertEqual(evaluations[1]["semantics"], "lc0.uci_wdl")
        self.assertEqual(evaluations[1]["scale"], 1000)

    def test_lc0_bestmove_metadata_is_preserved(self):
        adapter = lc0_adapter()
        event = adapter.consume(
            "bestmove e2e4 ponder e7e5 player 3 gameid 9 side white",
            observed_ms=7,
        )[0]
        self.assertEqual(event["event_type"], "search.complete")
        self.assertEqual(event["bestmove"], "e2e4")
        self.assertEqual(event["ponder"], "e7e5")
        self.assertEqual(event["native"]["data"]["player"], 3)
        self.assertEqual(event["native"]["data"]["gameid"], 9)
        self.assertEqual(event["native"]["data"]["side"], "white")

    def test_bestmove_null_sentinels(self):
        cases = [
            (stockfish_adapter(), "(none)"),
            (reckless_adapter(), "0000"),
            (lc0_adapter(), "a1a1"),
        ]
        for adapter, sentinel in cases:
            event = adapter.consume(f"bestmove {sentinel}", observed_ms=1)[0]
            self.assertIsNone(event["bestmove"])

    def test_pv_stops_at_non_move_trailing_material(self):
        event = lc0_adapter().consume(
            "info depth 2 score cp 1 pv e2e4 e7e5 string trailing comment",
            observed_ms=2,
        )[0]
        self.assertEqual(event["candidate"]["pv"], ["e2e4", "e7e5"])
        self.assertEqual(event["native"]["data"]["comment"], "trailing comment")

    def test_generic_info_string_is_native_event(self):
        event = stockfish_adapter().consume("info string hello world", observed_ms=1)[0]
        self.assertEqual(event["event_type"], "native.event")
        self.assertEqual(event["native"]["data"]["comment"], "hello world")

    def test_lc0_defect_iteration_and_summary_are_lossless_native_events(self):
        adapter = lc0_adapter()
        iteration = adapter.consume(
            'info string DEFECT_TELEMETRY_ITER {"v":1,"iteration":1,'
            '"leader_move_raw":1234,"runner_up_move_raw":0}',
            observed_ms=1,
        )[0]
        summary = adapter.consume(
            'info string DEFECT_TELEMETRY_SUMMARY {"v":1,"iterations":1,'
            '"speculative_unused":7}',
            observed_ms=2,
        )[0]
        self.assertEqual(iteration["native"]["schema"], "lc0.defect.iter.v1")
        self.assertEqual(iteration["native"]["data"]["leader_move_raw"], 1234)
        self.assertEqual(summary["native"]["schema"], "lc0.defect.summary.v1")
        self.assertEqual(summary["native"]["data"]["speculative_unused"], 7)

    def test_lc0_can_defer_completion_until_post_search_defect_flush(self):
        adapter = started(
            Lc0TelemetryAdapter(
                search_id="lc0-deferred",
                engine_instance="lc0-0",
                position_id="fixture:startpos",
                score_type="centipawn",
                defer_completion_until_flush=True,
            )
        )
        self.assertEqual(adapter.consume("bestmove e2e4", observed_ms=5), [])
        summary = adapter.consume(
            'info string DEFECT_TELEMETRY_SUMMARY {"v":1,"iterations":1}',
            observed_ms=6,
        )[0]
        complete = adapter.flush_completion(observed_ms=7)
        self.assertEqual(summary["native"]["schema"], "lc0.defect.summary.v1")
        self.assertEqual(complete["event_type"], "search.complete")
        self.assertEqual(complete["native"]["data"]["received_observed_ms"], 5)

    def test_malformed_lc0_defect_json_is_an_error(self):
        adapter = lc0_adapter()
        with self.assertRaises(TelemetryParseError):
            adapter.consume(
                'info string DEFECT_TELEMETRY_SUMMARY {"v":1,',
                observed_ms=1,
            )

    def test_adapter_rejects_output_after_completion(self):
        adapter = stockfish_adapter()
        adapter.consume("bestmove e2e4", observed_ms=1)
        with self.assertRaises(TelemetryError):
            adapter.consume("info depth 1 multipv 1 pv e2e4", observed_ms=2)


if __name__ == "__main__":
    unittest.main()
