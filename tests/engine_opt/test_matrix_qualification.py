#!/usr/bin/env python3
"""Contracts for the shared canonical/candidate LC0 matrix qualifier."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest import mock

import tools.engine_opt.matrix_qualification as mq

SOURCE = "a" * 40


def seal(doc):
    core = copy.deepcopy(doc)
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    core["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    return core


def summary(bestmoves, *, wall):
    return {
        "cases": 2,
        "completion_rate": 1.0,
        "bestmoves": list(bestmoves),
        "native_work_values": [10, 11],
        "median_wall_ms": wall,
        "max_wall_ms": wall + 10,
        "max_cpu_ms": wall + 5,
    }


class MatrixQualificationTests(unittest.TestCase):
    def selection(self):
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
            "qualification": {
                "baseline_profile": "baseline",
                "confirmation_repeats": 3,
                "corpus_cases": 2,
                "max_median_wall_ratio": 0.40,
                "max_wall_ms": 600,
            },
        }

    def matrix(self):
        repeats = {
            "baseline": [summary(("a", "b"), wall=1000) for _ in range(3)],
            "selected": [summary(("a", "b"), wall=300) for _ in range(3)],
        }
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
                "rows": [{"profile": "selected", "options": {}, "warmup": None}],
            }
        )

    def qualify(self, matrix=None):
        with (
            mock.patch.object(mq, "validate_execution_domain", return_value={"domain": "same"}),
            mock.patch.object(mq, "require_same_execution_domain", return_value={"domain": "same"}),
            mock.patch.object(mq, "validate_selected_lc0_rows", return_value=None),
        ):
            return mq.qualify_lc0_matrix(
                matrix=matrix or self.matrix(),
                selection=self.selection(),
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
