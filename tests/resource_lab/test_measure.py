#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from adapters.resource.linux_proc import ProcessDelta
from tools.resource_lab.measure import (
    candidate_summary,
    parse_search_observation,
    percentile,
)


class MeasureTests(unittest.TestCase):
    def test_parses_move_pv_eval_work_and_physical_cost(self):
        delta=ProcessDelta(
            pid=42,start_time_ticks=99,wall_ms=12.5,cpu_ms=10.0,
            start_rss_bytes=100,end_rss_bytes=120,vm_hwm_bytes=130,
        )
        result=parse_search_observation(
            [
                "info depth 4 nodes 16 nps 1000 score cp 23 pv e2e4 e7e5 g1f3",
                "bestmove e2e4",
            ],
            family="stockfish",
            delta=delta,
        )
        self.assertEqual(result.bestmove,"e2e4")
        self.assertEqual(result.pv,("e2e4","e7e5","g1f3"))
        self.assertEqual(result.evaluation.kind,"cp")
        self.assertEqual(result.evaluation.value,23)
        self.assertEqual(result.native_work_value,16)
        self.assertEqual(result.native_work_semantics,"stockfish.uci_nodes")
        self.assertEqual(result.process_start_time_ticks,99)
        self.assertEqual(result.vm_hwm_bytes,130)

    def test_wdl_is_preserved_as_family_native_evidence(self):
        delta=ProcessDelta(
            pid=1,start_time_ticks=1,wall_ms=1,cpu_ms=1,
            start_rss_bytes=None,end_rss_bytes=None,vm_hwm_bytes=None,
        )
        result=parse_search_observation(
            ["info nodes 8 wdl 500 300 200 pv d2d4","bestmove d2d4"],
            family="lc0",delta=delta,
        )
        self.assertEqual(result.evaluation.kind,"wdl")
        self.assertEqual(result.evaluation.value,(500,300,200))

    def test_repeatability_is_vector_level_not_single_case(self):
        case_ids=("a","b")
        rows=[]
        for repeat in range(3):
            for case,move in zip(case_ids,("e2e4","d2d4")):
                rows.append({
                    "repeat_index":repeat,"case_id":case,"status":"completed",
                    "measurement":{
                        "bestmove":move,"native_work_value":16,
                        "wall_ms":10+repeat,"cpu_ms":9+repeat,"vm_hwm_bytes":100,
                    }
                })
        summary=candidate_summary(rows,case_ids=case_ids,repeats=3)
        self.assertTrue(summary["complete"])
        self.assertTrue(summary["bestmove_repeatable"])
        self.assertTrue(summary["native_work_repeatable"])
        rows[-1]["measurement"]["bestmove"]="g1f3"
        summary=candidate_summary(rows,case_ids=case_ids,repeats=3)
        self.assertFalse(summary["bestmove_repeatable"])

    def test_failure_is_retained_not_disappeared(self):
        rows=[
            {"repeat_index":0,"case_id":"a","status":"error","error":"boom","measurement":None}
        ]
        summary=candidate_summary(rows,case_ids=("a",),repeats=1)
        self.assertFalse(summary["complete"])
        self.assertIn("boom",summary["errors"][0])

    def test_percentile_is_deterministic_nearest_rank(self):
        self.assertEqual(percentile([1,2,3,4,5],0.95),5)


if __name__=="__main__":
    unittest.main()
