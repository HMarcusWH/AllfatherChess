#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tools.resource_lab.qualify import (
    ResourceLabQualificationError,
    validate_attempt_order,
)


class QualifyAttemptTests(unittest.TestCase):
    def test_exact_blocked_attempt_order_passes(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
            {
                "candidate_id":"b","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":1,"attempt_ordinal":1,
            },
        )
        rows=[
            {**expected[0],"attempt_index":0},
            {**expected[1],"attempt_index":0},
        ]
        validate_attempt_order(
            rows,expected,identity_field="candidate_id",label="Stage-A"
        )

    def test_reordered_attempt_fails(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
            {
                "candidate_id":"b","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":1,"attempt_ordinal":1,
            },
        )
        rows=[
            {**expected[1],"attempt_index":0},
            {**expected[0],"attempt_index":0},
        ]
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                rows,expected,identity_field="candidate_id",label="Stage-A"
            )

    def test_missing_attempt_fails(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
            {
                "candidate_id":"b","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":1,"attempt_ordinal":1,
            },
        )
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                [{**expected[0],"attempt_index":0}],
                expected,
                identity_field="candidate_id",
                label="Stage-A",
            )

    def test_retry_index_fails_even_without_duplicate_row(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
        )
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                [{**expected[0],"attempt_index":1}],
                expected,
                identity_field="candidate_id",
                label="Stage-A",
            )

    def test_tampered_order_metadata_fails(self):
        expected=(
            {
                "composition_id":"c0","repeat_index":0,"case_id":"x",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
        )
        row={**expected[0],"attempt_index":0,"block_index":7}
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                [row],expected,identity_field="composition_id",label="Stage-B"
            )


if __name__=="__main__":
    unittest.main()
