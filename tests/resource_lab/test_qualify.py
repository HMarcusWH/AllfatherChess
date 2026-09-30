#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tools.resource_lab.qualify import (
    ResourceLabQualificationError,
    validate_attempt_keys,
)


class QualifyAttemptTests(unittest.TestCase):
    def test_exact_attempt_set_passes(self):
        expected={("a",0,"c0"),("a",1,"c0")}
        rows=[
            {"candidate_id":"a","repeat_index":0,"case_id":"c0","attempt_index":0},
            {"candidate_id":"a","repeat_index":1,"case_id":"c0","attempt_index":0},
        ]
        seen=validate_attempt_keys(
            rows,expected,
            fields=("candidate_id","repeat_index","case_id"),
            label="Stage-A",
        )
        self.assertEqual(seen,expected)

    def test_duplicate_attempt_is_retry_and_fails(self):
        expected={("a",0,"c0")}
        row={"candidate_id":"a","repeat_index":0,"case_id":"c0","attempt_index":0}
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_keys(
                [row,dict(row)],expected,
                fields=("candidate_id","repeat_index","case_id"),
                label="Stage-A",
            )

    def test_missing_attempt_fails(self):
        expected={("a",0,"c0"),("a",1,"c0")}
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_keys(
                [{"candidate_id":"a","repeat_index":0,"case_id":"c0","attempt_index":0}],
                expected,
                fields=("candidate_id","repeat_index","case_id"),
                label="Stage-A",
            )

    def test_retry_index_fails_even_without_duplicate_row(self):
        expected={("a",0,"c0")}
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_keys(
                [{"candidate_id":"a","repeat_index":0,"case_id":"c0","attempt_index":1}],
                expected,
                fields=("candidate_id","repeat_index","case_id"),
                label="Stage-A",
            )

    def test_unexpected_attempt_fails(self):
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_keys(
                [{"candidate_id":"b","repeat_index":0,"case_id":"c0","attempt_index":0}],
                {("a",0,"c0")},
                fields=("candidate_id","repeat_index","case_id"),
                label="Stage-A",
            )


if __name__=="__main__":
    unittest.main()
