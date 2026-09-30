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
from tools.resource_lab.pareto import build_pareto_report,dominates

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
