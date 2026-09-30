#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tools.resource_lab.compose import summarize_composition_interference


class ComposeTests(unittest.TestCase):
    def test_interference_summary_compares_same_candidate_and_case(self):
        isolated=[]
        for repeat in range(3):
            isolated.append({
                "candidate_id":"cand","case_id":"c","status":"completed",
                "measurement":{
                    "wall_ms":100,"cpu_ms":80,"bestmove":"e2e4","native_work_value":64
                },
                "process_cpu_scope":{"complete":True}
            })
        stage_b=[{
            "composition_id":"comp","case_id":"c","status":"completed",
            "batch_wall_ms":150,
            "aggregate_resource":{
                "sum_cpu_ms":100,
                "sum_end_rss_bytes":None,
                "sum_member_vm_hwm_bytes":None,
            },
            "members":[{
                "instance":"stockfish-anchor","candidate_id":"cand","status":"completed",
                "measurement":{
                    "wall_ms":150,"cpu_ms":100,"bestmove":"e2e4","native_work_value":64
                },
                "process_cpu_scope":{"complete":True}
            }]
        }]
        report=summarize_composition_interference(rows=stage_b,isolated_rows=isolated)
        row=report["compositions"][0]["members"]["stockfish-anchor"]
        self.assertEqual(row["median_wall_slowdown_ratio"],1.5)
        self.assertEqual(row["median_cpu_inflation_ratio"],1.25)
        self.assertEqual(row["bestmove_drift_count"],0)
        self.assertEqual(row["native_work_drift_count"],0)
        self.assertEqual(row["reference_unstable_count"],0)
        self.assertEqual(row["behavior_evaluated_count"],1)

    def test_unstable_isolated_reference_is_not_arbitrarily_called_drift(self):
        isolated=[
            {
                "candidate_id":"cand","case_id":"c","status":"completed",
                "measurement":{
                    "wall_ms":100,"cpu_ms":80,"bestmove":"e2e4","native_work_value":64
                },
                "process_cpu_scope":{"complete":True}
            },
            {
                "candidate_id":"cand","case_id":"c","status":"completed",
                "measurement":{
                    "wall_ms":100,"cpu_ms":80,"bestmove":"d2d4","native_work_value":63
                },
                "process_cpu_scope":{"complete":True}
            },
        ]
        stage_b=[{
            "composition_id":"comp","case_id":"c","status":"completed",
            "batch_wall_ms":100,
            "aggregate_resource":{
                "sum_cpu_ms":80,
                "sum_end_rss_bytes":None,
                "sum_member_vm_hwm_bytes":None,
            },
            "members":[{
                "instance":"x","candidate_id":"cand","status":"completed",
                "measurement":{
                    "wall_ms":100,"cpu_ms":80,"bestmove":"d2d4","native_work_value":63
                }
            }]
        }]
        row=summarize_composition_interference(rows=stage_b,isolated_rows=isolated)
        member=row["compositions"][0]["members"]["x"]
        self.assertEqual(member["reference_unstable_count"],1)
        self.assertEqual(member["behavior_evaluated_count"],0)
        self.assertEqual(member["bestmove_drift_count"],0)
        self.assertEqual(member["native_work_drift_count"],0)


if __name__=="__main__":
    unittest.main()
