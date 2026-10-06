#!/usr/bin/env python3
"""Contracts for the shared canonical/candidate LC0 matrix qualifier."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tools.engine_opt.matrix_qualification as mq

SOURCE = "a" * 40


def seal(doc):
    core = copy.deepcopy(doc)
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    core["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    return core


def summary(bestmoves, *, wall, native_work=(10, 11)):
    return {
        "cases": 2,
        "completion_rate": 1.0,
        "bestmoves": list(bestmoves),
        "native_work_values": list(native_work),
        "median_wall_ms": wall,
        "max_wall_ms": wall + 10,
        "max_cpu_ms": wall + 5,
    }


class MatrixQualificationTests(unittest.TestCase):
    def selection(self, *, node_stop_policy: bool = False):
        qualification = {
            "baseline_profile": "baseline",
            "confirmation_repeats": 3,
            "corpus_cases": 2,
            "max_median_wall_ratio": 0.40,
            "max_wall_ms": 600,
        }
        if node_stop_policy:
            qualification["native_work_policy"] = {
                "id": "lc0-node-stop-contract-v1",
                "requested_nodes": 16,
                "terminal_counter_semantics": "lc0.uci_nodes",
                "require_exact_terminal_counter_repeatability": False,
                "required_options": {
                    "MaxConcurrentSearchers": 1,
                    "TaskWorkers": 0,
                    "Threads": 1,
                },
            }
        return {
            "selected": {
                "lc0": {
                    "matrix_profile": "selected",
                    "nn_cache_size": 1,
                    "minibatch_size": 4,
                    "max_prefetch": 0,
                    "adaptive_prefetch": False,
                    "warmup_nodes": None,
                }
            },
            "qualification": qualification,
        }

    def matrix(self, *, selected_native_work=None):
        selected_native_work = selected_native_work or [(10, 11), (10, 11), (10, 11)]
        repeats = {
            "baseline": [summary(("a", "b"), wall=1000) for _ in range(3)],
            "selected": [
                summary(("a", "b"), wall=300, native_work=native)
                for native in selected_native_work
            ],
        }
        selected_options = {
            "NNCacheSize": 1,
            "MinibatchSize": 4,
            "MaxPrefetch": 0,
            "AdaptivePrefetch": False,
            "MaxConcurrentSearchers": 1,
            "TaskWorkers": 0,
            "Threads": 1,
        }
        rows = []
        case_ids = ("case-a", "case-b")
        for repeat_index, native in enumerate(selected_native_work):
            for case_index, case_id in enumerate(case_ids):
                rows.append(
                    {
                        "profile": "selected",
                        "repeat_index": repeat_index,
                        "case_id": case_id,
                        "family": "lc0",
                        "nodes_requested": 16,
                        "options": dict(selected_options),
                        "warmup": None,
                        "metrics": {
                            "native_work_semantics": "lc0.uci_nodes",
                            "native_work_value": native[case_index],
                            "completed_before_deadline": True,
                        },
                    }
                )
        return seal(
            {
                "source": {"commit": SOURCE},
                "errors": [],
                "execution_domain": {"domain": "same"},
                "binary": {"sha256": "engine"},
                "weights": {"sha256": "network"},
                "confirmation": {
                    "selected_profile": "selected",
                    "baseline_profile": "baseline",
                    "repeats": 3,
                },
                "repeat_summaries": repeats,
                "rows": rows,
            }
        )

    def qualify(self, matrix=None, *, node_stop_policy: bool = False):
        with (
            mock.patch.object(mq, "validate_execution_domain", return_value={"domain": "same"}),
            mock.patch.object(mq, "require_same_execution_domain", return_value={"domain": "same"}),
        ):
            return mq.qualify_lc0_matrix(
                matrix=matrix or self.matrix(),
                selection=self.selection(node_stop_policy=node_stop_policy),
                expected_source_commit=SOURCE,
                expected_execution_domain={"domain": "same"},
                candidate_bundle={
                    "artifacts": {
                        "engines": {"lc0": {"sha256": "engine"}},
                        "networks": {"lc0": {"sha256": "network"}},
                    }
                },
            )

    def test_shared_matrix_contract_qualifies_repeatable_equivalent_profile(self):
        result = self.qualify()
        self.assertTrue(result["qualified"])
        self.assertEqual(result["qualification_failures"], [])
        self.assertTrue(result["details"]["selected_native_work_exact_repeatability_required"])

    def test_node_stop_policy_retains_counter_variation_as_diagnostic(self):
        matrix = self.matrix(selected_native_work=[(10, 11), (10, 11), (10, 12)])
        result = self.qualify(matrix, node_stop_policy=True)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["qualification_failures"], [])
        self.assertEqual(result["details"]["native_work_policy"], "lc0-node-stop-contract-v1")
        self.assertFalse(result["details"]["selected_native_work_exact_repeatability_required"])
        self.assertFalse(result["details"]["selected"]["native_work_stable"])

    def test_native_work_variation_still_blocks_without_explicit_node_stop_policy(self):
        matrix = self.matrix(selected_native_work=[(10, 11), (10, 11), (10, 12)])
        result = self.qualify(matrix)
        codes = {row["code"] for row in result["qualification_failures"]}
        self.assertIn("NOT_QUALIFIED_REPEATABILITY", codes)
        self.assertFalse(result["qualified"])

    def test_node_stop_policy_rejects_wrong_requested_work(self):
        matrix = self.matrix()
        matrix.pop("content_sha256")
        matrix["rows"][0]["nodes_requested"] = 17
        matrix = seal(matrix)
        with self.assertRaisesRegex(mq.MatrixQualificationError, "requested node count"):
            self.qualify(matrix, node_stop_policy=True)

    def test_node_stop_policy_rejects_wrong_terminal_counter_semantics(self):
        matrix = self.matrix()
        matrix.pop("content_sha256")
        matrix["rows"][0]["metrics"]["native_work_semantics"] = "other.counter"
        matrix = seal(matrix)
        with self.assertRaisesRegex(mq.MatrixQualificationError, "terminal counter semantics"):
            self.qualify(matrix, node_stop_policy=True)

    def test_node_stop_policy_rejects_missing_or_invalid_terminal_counter(self):
        matrix = self.matrix()
        matrix.pop("content_sha256")
        matrix["rows"][0]["metrics"]["native_work_value"] = None
        matrix = seal(matrix)
        with self.assertRaisesRegex(mq.MatrixQualificationError, "terminal counter is invalid"):
            self.qualify(matrix, node_stop_policy=True)

    def test_behavioral_drift_is_valid_negative_evidence(self):
        matrix = self.matrix()
        matrix.pop("content_sha256")
        matrix["repeat_summaries"]["selected"][0]["bestmoves"] = ["x", "b"]
        matrix = seal(matrix)
        result = self.qualify(matrix)
        codes = {row["code"] for row in result["qualification_failures"]}
        self.assertIn("NOT_QUALIFIED_REPEATABILITY", codes)
        self.assertIn("NOT_QUALIFIED_BEHAVIORAL_EQUIVALENCE", codes)
        self.assertFalse(result["qualified"])

    def test_wrong_source_is_invalid_evidence(self):
        matrix = self.matrix()
        matrix.pop("content_sha256")
        matrix["source"]["commit"] = "b" * 40
        matrix = seal(matrix)
        with self.assertRaises(mq.MatrixQualificationError):
            self.qualify(matrix)


if __name__ == "__main__":
    unittest.main()
