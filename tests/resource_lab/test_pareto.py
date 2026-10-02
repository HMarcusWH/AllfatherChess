#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tools.resource_lab.candidate_matrix import expand_candidates,load_lab_spec
from tools.resource_lab.pareto import (
    REFERENCE_NATIVE_WORK_BLOCKER,
    build_pareto_report,
    dominates,
)

SPEC=ROOT/"qualification/resource-lab-v1.json"


def fake_manifest():
    return {
        "artifacts":{
            "engines":{
                "stockfish":{"sha256":"1"*64},
                "reckless":{"sha256":"2"*64},
                "lc0":{"sha256":"3"*64},
            },
            "networks":{
                "stockfish":{"sha256":"4"*64},
                "reckless":{"sha256":"5"*64},
                "lc0":{"sha256":"6"*64},
            },
        }
    }


def rows_for(candidates,case_ids):
    rows=[]
    for candidate in candidates:
        variant_penalty=0 if candidate.reference else 10
        for repeat in range(3):
            for index,case in enumerate(case_ids):
                rows.append({
                    "candidate_id":candidate.candidate_id,
                    "candidate_digest":candidate.digest,
                    "repeat_index":repeat,
                    "case_id":case,
                    "status":"completed",
                    "measurement":{
                        "bestmove":"e2e4" if index%2==0 else "d2d4",
                        "native_work_value":candidate.nodes,
                        "wall_ms":100+variant_penalty,
                        "cpu_ms":0.25+variant_penalty,
                        "procfs_cpu_ms":0,
                        "cpu_clock_resolution_ns":1,
                        "vm_hwm_bytes":1000+variant_penalty,
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
    return rows


class ParetoTests(unittest.TestCase):
    def test_dominance_requires_no_worse_everywhere_and_strict_somewhere(self):
        left={
            "wall_ms":{"median":1,"p95":1},
            "cpu_ms":{"median":1,"p95":1},
            "vm_hwm_bytes":{"p95":1},
        }
        right={
            "wall_ms":{"median":2,"p95":2},
            "cpu_ms":{"median":2,"p95":2},
            "vm_hwm_bytes":{"p95":2},
        }
        dims=("median_wall_ms","p95_wall_ms","median_cpu_ms","p95_cpu_ms","p95_vm_hwm_bytes")
        self.assertTrue(dominates(left,right,dims))
        self.assertFalse(dominates(right,left,dims))

    def test_reference_candidates_dominate_slower_behavior_equivalent_variants(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        case_ids=tuple(f"c{i}" for i in range(8))
        report=build_pareto_report(
            spec=spec,candidates=candidates,rows=rows_for(candidates,case_ids),
            case_ids=case_ids,
        )
        for group in report["groups"]:
            self.assertIn(group["reference_candidate_id"],group["pareto"])

    def test_lc0_reference_native_work_instability_blocks_promotion_group(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        case_ids=tuple(f"c{i}" for i in range(8))
        rows=rows_for(candidates,case_ids)
        targets={
            16: ("c4", (19, 19, 14)),
            32: ("c3", (26, 26, 33)),
        }
        reference_ids={
            candidate.nodes: candidate.candidate_id
            for candidate in candidates
            if candidate.family=="lc0"
            and candidate.reference
            and candidate.nodes in targets
        }
        for row in rows:
            nodes=next(
                (
                    budget
                    for budget,candidate_id in reference_ids.items()
                    if row["candidate_id"]==candidate_id
                ),
                None,
            )
            if nodes is None:
                continue
            case_id,values=targets[nodes]
            if row["case_id"]==case_id:
                row["measurement"]["native_work_value"]=values[row["repeat_index"]]

        report=build_pareto_report(
            spec=spec,candidates=candidates,rows=rows,case_ids=case_ids
        )
        for nodes in targets:
            group=next(
                g for g in report["groups"]
                if g["family"]=="lc0" and g["nodes"]==nodes
            )
            summary=report["candidate_summaries"][
                group["reference_candidate_id"]
            ]
            self.assertTrue(summary["bestmove_repeatable"])
            self.assertFalse(summary["native_work_repeatable"])
            self.assertFalse(group["promotion_eligible"])
            self.assertEqual(
                group["promotion_blockers"],
                [REFERENCE_NATIVE_WORK_BLOCKER],
            )
            self.assertEqual(group["eligible"],[])
            self.assertEqual(group["pareto"],[])
            expected_ids={
                candidate.candidate_id
                for candidate in candidates
                if candidate.family=="lc0" and candidate.nodes==nodes
            }
            rejected={
                item["candidate_id"]: item["reasons"]
                for item in group["rejected"]
            }
            self.assertEqual(set(rejected),expected_ids)
            self.assertTrue(
                all(
                    REFERENCE_NATIVE_WORK_BLOCKER in reasons
                    for reasons in rejected.values()
                )
            )

    def test_unmeasurable_cpu_rejects_candidate_before_pareto(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        case_ids=tuple(f"c{i}" for i in range(8))
        rows=rows_for(candidates,case_ids)
        target=next(
            candidate for candidate in candidates
            if candidate.family=="stockfish" and candidate.nodes==16 and not candidate.reference
        )
        for row in rows:
            if row["candidate_id"]==target.candidate_id:
                row["measurement"]["cpu_ms"]=0.0
        report=build_pareto_report(spec=spec,candidates=candidates,rows=rows,case_ids=case_ids)
        group=next(g for g in report["groups"] if g["family"]=="stockfish" and g["nodes"]==16)
        rejected={row["candidate_id"]:row["reasons"] for row in group["rejected"]}
        self.assertIn("cpu-measurement-unusable",rejected[target.candidate_id])

    def test_incomplete_process_cpu_scope_rejects_candidate_before_pareto(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        case_ids=tuple(f"c{i}" for i in range(8))
        rows=rows_for(candidates,case_ids)
        target=next(
            candidate for candidate in candidates
            if candidate.family=="reckless" and candidate.nodes==16 and not candidate.reference
        )
        for row in rows:
            if row["candidate_id"]==target.candidate_id:
                row["process_cpu_scope"]["complete"]=False
                row["process_cpu_scope"]["reasons"]=["separate-child-process-observed"]
        report=build_pareto_report(spec=spec,candidates=candidates,rows=rows,case_ids=case_ids)
        group=next(g for g in report["groups"] if g["family"]=="reckless" and g["nodes"]==16)
        rejected={row["candidate_id"]:row["reasons"] for row in group["rejected"]}
        self.assertIn("process-cpu-scope-incomplete",rejected[target.candidate_id])

    def test_bestmove_drift_rejects_candidate_before_pareto(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        case_ids=tuple(f"c{i}" for i in range(8))
        rows=rows_for(candidates,case_ids)
        target=next(
            candidate for candidate in candidates
            if candidate.family=="stockfish" and candidate.nodes==16 and not candidate.reference
        )
        for row in rows:
            if row["candidate_id"]==target.candidate_id and row["case_id"]=="c0":
                row["measurement"]["bestmove"]="g1f3"
        report=build_pareto_report(spec=spec,candidates=candidates,rows=rows,case_ids=case_ids)
        group=next(g for g in report["groups"] if g["family"]=="stockfish" and g["nodes"]==16)
        rejected={row["candidate_id"]:row["reasons"] for row in group["rejected"]}
        self.assertIn(target.candidate_id,rejected)
        self.assertIn("bestmove-reference-drift",rejected[target.candidate_id])


if __name__=="__main__":
    unittest.main()
