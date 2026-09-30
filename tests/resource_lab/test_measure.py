#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from adapters.resource.linux_proc import ProcessSnapshot
from tools.resource_lab.measure import (
    candidate_summary,
    classify_failure,
    parse_search_observation,
    percentile,
    physical_primitives,
    reconstruct_physical_measurement,
)
from tools.resource_lab.process_cpu import ProcessCpuClockEvidence


def snapshot(*,mono,utime,stime,rss,hwm):
    return ProcessSnapshot(
        pid=42,
        start_time_ticks=99,
        monotonic_ns=mono,
        user_cpu_ticks=utime,
        system_cpu_ticks=stime,
        rss_bytes=rss,
        vm_hwm_bytes=hwm,
    )


def physical():
    before=snapshot(mono=1_000_000_000,utime=10,stime=2,rss=100,hwm=110)
    after=snapshot(mono=1_012_500_000,utime=11,stime=2,rss=120,hwm=130)
    cpu=ProcessCpuClockEvidence(
        method="posix-process-cpu-clock-v1",
        pid=42,
        resolution_ns=1,
        before_ns=10_000_000,
        after_ns=20_250_000,
    )
    primitives=physical_primitives(before,before,after,cpu)
    result=reconstruct_physical_measurement(
        primitives,
        clock_ticks_per_second=100,
        required_cpu_method="posix-process-cpu-clock-v1",
        max_cpu_resolution_ns=1_000_000,
    )
    return primitives,result


class MeasureTests(unittest.TestCase):
    def test_reconstructs_wall_cpu_and_memory_from_primitives(self):
        primitives,result=physical()
        self.assertEqual(result.wall_ms,12.5)
        self.assertEqual(result.cpu_ms,10.25)
        self.assertEqual(result.procfs_cpu_ms,10.0)
        self.assertEqual(result.start_rss_bytes,100)
        self.assertEqual(result.end_rss_bytes,120)
        self.assertEqual(result.vm_hwm_bytes,130)
        self.assertEqual(result.process_start_time_ticks,99)
        self.assertEqual(primitives["proc_before"]["pid"],42)

    def test_parses_move_pv_eval_work_with_reconstructed_physical_cost(self):
        _,cost=physical()
        result=parse_search_observation(
            [
                "info depth 4 nodes 16 nps 1000 score cp 23 pv e2e4 e7e5 g1f3",
                "bestmove e2e4",
            ],
            family="stockfish",
            physical=cost,
        )
        self.assertEqual(result.bestmove,"e2e4")
        self.assertEqual(result.pv,("e2e4","e7e5","g1f3"))
        self.assertEqual(result.evaluation.kind,"cp")
        self.assertEqual(result.evaluation.value,23)
        self.assertEqual(result.native_work_value,16)
        self.assertEqual(result.native_work_semantics,"stockfish.uci_nodes")
        self.assertEqual(result.physical.cpu_ms,10.25)

    def test_wdl_is_preserved_as_family_native_evidence(self):
        _,cost=physical()
        result=parse_search_observation(
            ["info nodes 8 wdl 500 300 200 pv d2d4","bestmove d2d4"],
            family="lc0",physical=cost,
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
                        "wall_ms":10+repeat,"cpu_ms":0.5+repeat,
                        "procfs_cpu_ms":0,"cpu_clock_resolution_ns":1,
                        "vm_hwm_bytes":100,
                    },
                    "process_cpu_scope":{
                        "complete":True,
                        "root_pid":42,
                        "root_start_time_ticks":99,
                        "observed_process_ids":[42],
                        "child_process_ids":[],
                        "reasons":[],
                    }
                })
        summary=candidate_summary(rows,case_ids=case_ids,repeats=3)
        self.assertTrue(summary["complete"])
        self.assertTrue(summary["bestmove_repeatable"])
        self.assertTrue(summary["native_work_repeatable"])
        self.assertEqual(summary["cpu_clock_resolution_ns"],1)
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

    def test_identity_binding_mismatch_is_rejected(self):
        primitives,_=physical()
        primitives["identity_binding"]["start_time_ticks"]=100
        with self.assertRaises(Exception):
            reconstruct_physical_measurement(
                primitives,
                clock_ticks_per_second=100,
                required_cpu_method="posix-process-cpu-clock-v1",
                max_cpu_resolution_ns=1_000_000,
            )

    def test_failure_taxonomy_separates_measurement_engine_and_timeout(self):
        self.assertEqual(
            classify_failure("cpu_clock_init",RuntimeError("boom")),
            ("cpu_clock_init","measurement_substrate"),
        )
        self.assertEqual(
            classify_failure("search",RuntimeError("boom")),
            ("search","engine"),
        )
        self.assertEqual(
            classify_failure("search",TimeoutError("timeout")),
            ("search","timeout"),
        )

    def test_percentile_is_deterministic_nearest_rank(self):
        self.assertEqual(percentile([1,2,3,4,5],0.95),5)


if __name__=="__main__":
    unittest.main()
