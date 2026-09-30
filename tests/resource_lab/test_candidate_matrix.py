#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tools.resource_lab.candidate_matrix import (
    LabSpec,
    ResourceLabSpecError,
    expand_candidates,
    expand_compositions,
    load_lab_spec,
    stage_a_attempt_plan,
    stage_b_attempt_plan,
    validate_lab_spec,
    validate_reference_contract,
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


class CandidateMatrixTests(unittest.TestCase):
    def test_frozen_matrix_expands_to_57_unique_exact_candidates(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        self.assertEqual(len(candidates),57)
        self.assertEqual(len({row.candidate_id for row in candidates}),57)
        self.assertEqual(sum(row.family=="stockfish" for row in candidates),15)
        self.assertEqual(sum(row.family=="reckless" for row in candidates),15)
        self.assertEqual(sum(row.family=="lc0" for row in candidates),27)
        self.assertEqual(sum(row.reference for row in candidates),9)

    def test_stage_b_is_frozen_and_references_only_stage_a_candidates(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        compositions=expand_compositions(spec,candidates)
        self.assertEqual([row.composition_id for row in compositions],[
            "c0-four-way-v2-current",
            "c1-anchor2-reckless-lc0",
            "c2-anchor2-lc0-threads2",
        ])
        known={row.candidate_id for row in candidates}
        for composition in compositions:
            self.assertEqual(sum(member.role=="anchor" for member in composition.members),1)
            self.assertTrue(all(member.candidate_id in known for member in composition.members))

    def test_chess_policy_option_cannot_be_promoted_to_variable_axis(self):
        raw=json.loads(SPEC.read_text())
        mutated=copy.deepcopy(raw)
        mutated["families"]["stockfish"]["variable_options"].append("MultiPV")
        mutated["families"]["stockfish"]["variants"][0]["overrides"]["MultiPV"]=2
        with self.assertRaises(ResourceLabSpecError):
            validate_lab_spec(mutated)

    def test_retry_order_placement_and_measurement_policies_are_frozen(self):
        raw=json.loads(SPEC.read_text())
        mutations=(
            ("attempt_policy","retry-until-green"),
            ("attempt_order_policy","candidate-major"),
            ("placement_mode","affinity"),
        )
        for key,value in mutations:
            mutated=copy.deepcopy(raw)
            mutated[key]=value
            with self.subTest(key=key):
                with self.assertRaises(ResourceLabSpecError):
                    validate_lab_spec(mutated)
        mutated=copy.deepcopy(raw)
        mutated["cpu_measurement"]["required_method"]="procfs-jiffies"
        with self.assertRaises(ResourceLabSpecError):
            validate_lab_spec(mutated)

    def test_candidate_identity_binds_artifact_hashes(self):
        spec=load_lab_spec(SPEC)
        first=expand_candidates(spec,fake_manifest())[0]
        altered=fake_manifest()
        altered["artifacts"]["engines"]["stockfish"]["sha256"]="f"*64
        second=expand_candidates(spec,altered)[0]
        self.assertNotEqual(first.candidate_id,second.candidate_id)
        self.assertNotEqual(first.digest,second.digest)

    def test_v2_reference_contract_matches_runtime_catalog_and_build_policy(self):
        validate_reference_contract(load_lab_spec(SPEC),ROOT)

    def test_reference_contract_rejects_fixed_policy_drift(self):
        raw=json.loads(SPEC.read_text())
        mutations=[
            ("stockfish","fixed_options","MultiPV",2),
            ("reckless","fixed_options","Minimal",True),
            ("lc0","fixed_options","ScoreType","centipawn"),
        ]
        for family,section,key,value in mutations:
            mutated=copy.deepcopy(raw)
            mutated["families"][family][section][key]=value
            spec=LabSpec(raw=mutated,path=SPEC)
            with self.subTest(family=family,key=key):
                with self.assertRaises(ResourceLabSpecError):
                    validate_reference_contract(spec,ROOT)

    def test_reference_contract_rejects_warmup_argv_and_environment_drift(self):
        raw=json.loads(SPEC.read_text())
        cases=[]
        one=copy.deepcopy(raw)
        one["families"]["lc0"]["warmup_nodes"]=32
        cases.append(one)
        two=copy.deepcopy(raw)
        two["families"]["lc0"]["args"]=[]
        cases.append(two)
        three=copy.deepcopy(raw)
        three["families"]["lc0"]["environment"]["OMP_NUM_THREADS"]="2"
        cases.append(three)
        for index,mutated in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ResourceLabSpecError):
                    validate_reference_contract(LabSpec(raw=mutated,path=SPEC),ROOT)

    def test_blocked_cyclic_attempt_plan_interleaves_candidates_and_compositions(self):
        spec=load_lab_spec(SPEC)
        candidates=expand_candidates(spec,fake_manifest())
        compositions=expand_compositions(spec,candidates)
        cases=("a","b")
        a=stage_a_attempt_plan(candidates,cases,2)
        self.assertEqual(len(a),len(candidates)*4)
        first_block=[row["candidate_id"] for row in a if row["block_index"]==0]
        second_block=[row["candidate_id"] for row in a if row["block_index"]==1]
        self.assertEqual(second_block,first_block[1:]+first_block[:1])
        self.assertEqual([row["attempt_ordinal"] for row in a],list(range(len(a))))
        b=stage_b_attempt_plan(compositions,cases,2)
        first_b=[row["composition_id"] for row in b if row["block_index"]==0]
        second_b=[row["composition_id"] for row in b if row["block_index"]==1]
        self.assertEqual(second_b,first_b[1:]+first_b[:1])


if __name__=="__main__":
    unittest.main()
