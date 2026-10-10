#!/usr/bin/env python3
"""Fail-closed contracts for canonical b4's source-frozen 23-case G3 authority."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.engine_opt.g3_b4 import (
    B4G3ContractError, B4_POLICY, CANDIDATE_POLICY, LEGACY_POLICY,
    WITNESS_CORPUS, file_hash, require_positive_g3, validate_b4_policy,
)


def witness_report():
    row = {
        "case": "discovery-base-00-03-g000016", "run_id": "g000016",
        "authority": "HYBRID",
        "authorization_policy": "clocked_staged_preanchor_v1",
        "authorization_granted": True,
        "terminal_source": "staged_verification",
        "route_action": "BUY_STAGED_VERIFY",
        "staged_complete": True, "anchor_move": "e8d6",
        "proposal_move": "h7h6", "emitted_move": "h7h6",
        "envelope_claimed": True, "resource_qualified": True,
        "route_resource_qualified": True,
        "clock_outcome": {"output_within_deadline": True},
    }
    return {"evidence_valid": True, "authority_qualified": True,
            "passed": True, "positive_case": row, "cases": [row],
            "contracts": {"policy_sha256": file_hash(ROOT, B4_POLICY)}}


class B4PolicyTests(unittest.TestCase):
    def test_frozen_canonical_policy_preserves_case_order_and_runtime(self):
        p = validate_b4_policy(ROOT)
        self.assertEqual(len(p["positive_cases"]), 23)
        self.assertEqual(p["positive_cases"][7]["source_set"], "discovery_local1_v1")
        self.assertEqual(p["runtime_config"], "config/allfather.online-hybrid-v2.validation.json")

    def test_mutations_to_corpus_authority_budget_or_provenance_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path in (B4_POLICY, CANDIDATE_POLICY, LEGACY_POLICY, WITNESS_CORPUS):
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / path).read_bytes())
            original = json.loads((root / B4_POLICY).read_text())
            for mutation in ("witness", "authority", "budget", "legacy", "path", "order", "missing"):
                with self.subTest(mutation=mutation):
                    p = copy.deepcopy(original)
                    if mutation == "witness":
                        p["positive_cases"][8]["command"] = "go movetime 4000"
                    elif mutation == "authority":
                        p["positive_requirement"]["require_non_anchor_move"] = False
                    elif mutation == "budget":
                        p["clock"]["max_move_ms"] = 5000
                    elif mutation == "legacy":
                        p["positive_cases"][0]["moves"] = ["e2e4"]
                    elif mutation == "path":
                        p["runtime_config"] = "config/allfather.online-hybrid-v2.candidate.validation.json"
                    elif mutation == "order":
                        p["positive_cases"][7:9] = list(reversed(p["positive_cases"][7:9]))
                    elif mutation == "missing":
                        p["positive_cases"].pop()
                    (root / B4_POLICY).write_text(json.dumps(p))
                    with self.assertRaises(B4G3ContractError):
                        validate_b4_policy(root)
            (root / B4_POLICY).write_text(json.dumps(original))
            corpus = json.loads((root / WITNESS_CORPUS).read_text())
            corpus["cases"][0]["command"] = "go movetime 4000"
            (root / WITNESS_CORPUS).write_text(json.dumps(corpus))
            with self.assertRaises(B4G3ContractError):
                validate_b4_policy(root)


class WitnessTests(unittest.TestCase):
    def test_real_non_anchor_witness(self):
        self.assertEqual(
            require_positive_g3(witness_report(), policy_sha256=file_hash(ROOT, B4_POLICY))["proposal_move"],
            "h7h6",
        )

    def test_no_witness_and_diagnostic_report_are_not_qualification(self):
        report = witness_report()
        report.update(passed=False, authority_qualified=False, positive_case=None)
        with self.assertRaisesRegex(B4G3ContractError, "positive authority witness missing"):
            require_positive_g3(report, policy_sha256=file_hash(ROOT, B4_POLICY))
        for name in ("diagnostic", "orphan", "same-move", "no-staged", "expired", "no-resource", "no-authority"):
            with self.subTest(name=name):
                r = witness_report()
                p = r["positive_case"]
                if name == "diagnostic":
                    r["contracts"]["policy_sha256"] = "f" * 64
                elif name == "orphan":
                    r["cases"] = []
                elif name == "same-move":
                    p["anchor_move"] = p["proposal_move"]
                elif name == "no-staged":
                    p["staged_complete"] = False
                elif name == "expired":
                    p["clock_outcome"]["output_within_deadline"] = False
                elif name == "no-resource":
                    p["resource_qualified"] = False
                elif name == "no-authority":
                    p["authorization_granted"] = False
                with self.assertRaises(B4G3ContractError):
                    require_positive_g3(r, policy_sha256=file_hash(ROOT, B4_POLICY))


if __name__ == "__main__":
    unittest.main()
