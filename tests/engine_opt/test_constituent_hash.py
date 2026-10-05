#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest

from tools.engine_opt.constituent_hash import (
    ConstituentHashError,
    expected_options,
    load_case_ids,
    load_policy,
    qualify_hash_matrix,
    schedule,
)

SOURCE = "a" * 40


def document(mode: str = "qualified") -> dict:
    policy = load_policy()
    case_ids = load_case_ids(policy)
    rows = []
    for slot in schedule(case_ids, repeats=policy["repeats"]):
        repeat = slot["repeat_index"]
        hash_mb = slot["hash_mb"]
        if mode == "failure":
            wall = 100.0 if hash_mb == 32 else 104.0
        elif mode == "inconclusive":
            selected_wall = 101.0 if repeat % 2 == 0 else 105.0
            wall = 100.0 if hash_mb == 32 else selected_wall
        else:
            wall = 100.0
        rows.append({
            **slot,
            "family": "reckless",
            "options": expected_options("reckless", hash_mb),
            "nodes_requested": policy["nodes"]["reckless"],
            "metrics": {
                "wall_ms": wall,
                "cpu_ms": wall,
                "native_work_value": policy["nodes"]["reckless"] + 1,
                "bestmove": "e2e4",
            },
            "transcript": [
                f">> setoption name Hash value {hash_mb}",
                f">> go nodes {policy['nodes']['reckless']}",
            ],
        })
    return {
        "schema_version": 2,
        "kind": "engine-opt-hash-matrix-v2",
        "family": "reckless",
        "source": {"commit": SOURCE},
        "nodes": policy["nodes"]["reckless"],
        "protocol": {
            "protocol_id": policy["protocol_id"],
            "repeats": policy["repeats"],
            "hash_mb": policy["hash_mb"],
            "ordering": policy["ordering"],
            "attempt_policy": policy["attempt_policy"],
            "efficiency_band": policy["efficiency_band"],
            "repeat_interval": policy["repeat_interval"],
        },
        "rows": rows,
        "summaries": {},
        "errors": [],
    }


class ScheduleTests(unittest.TestCase):
    def test_frozen_schedule_is_complete_unique_and_balanced(self):
        policy = load_policy()
        case_ids = load_case_ids(policy)
        plan = schedule(case_ids, repeats=policy["repeats"])
        self.assertEqual(len(plan), 400)
        keys = {
            (row["repeat_index"], row["case_id"], row["hash_mb"])
            for row in plan
        }
        self.assertEqual(len(keys), 400)
        first = {value: 0 for value in policy["hash_mb"]}
        for row in plan:
            if row["order_index"] == 0:
                first[row["hash_mb"]] += 1
        self.assertEqual(set(first.values()), {16})


class QualificationTests(unittest.TestCase):
    def qualify(self, doc):
        return qualify_hash_matrix(
            doc,
            expected_source_commit=SOURCE,
            selected_hash_mb=16,
        )

    def test_clear_pass_qualifies(self):
        result = self.qualify(document())
        self.assertTrue(result["qualified"])
        self.assertEqual(result["disposition"], "QUALIFIED")

    def test_clear_failure_does_not_qualify(self):
        result = self.qualify(document("failure"))
        self.assertFalse(result["qualified"])
        self.assertEqual(result["disposition"], "NOT_QUALIFIED")
        self.assertGreater(result["contrasts"]["32"]["lower"], 1.03)

    def test_threshold_crossing_is_inconclusive_not_pass(self):
        result = self.qualify(document("inconclusive"))
        self.assertFalse(result["qualified"])
        self.assertEqual(result["disposition"], "INCONCLUSIVE")
        row = result["contrasts"]["32"]
        self.assertLessEqual(row["lower"], 1.03)
        self.assertGreater(row["upper"], 1.03)

    def test_missing_or_reordered_rows_are_rejected(self):
        missing = document()
        missing["rows"].pop()
        with self.assertRaises(ConstituentHashError):
            self.qualify(missing)

        reordered = document()
        reordered["rows"][0], reordered["rows"][1] = (
            reordered["rows"][1],
            reordered["rows"][0],
        )
        with self.assertRaises(ConstituentHashError):
            self.qualify(reordered)

    def test_option_and_transcript_drift_are_rejected(self):
        bad = document()
        bad["rows"][0]["options"]["Hash"] = 999
        with self.assertRaises(ConstituentHashError):
            self.qualify(bad)

        bad = document()
        bad["rows"][0]["transcript"][0] = ">> setoption name Hash value 999"
        with self.assertRaises(ConstituentHashError):
            self.qualify(bad)

    def test_behavioral_mutations_fail_closed(self):
        bad = document()
        bad["rows"][0]["metrics"]["bestmove"] = "d2d4"
        result = self.qualify(bad)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["disposition"], "NOT_QUALIFIED_BEHAVIOR")

        bad = document()
        target = next(
            row
            for row in bad["rows"]
            if row["repeat_index"] == 1
            and row["case_id"] == "startpos"
            and row["hash_mb"] == 16
        )
        target["metrics"]["native_work_value"] += 1
        result = self.qualify(bad)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["disposition"], "NOT_QUALIFIED_BEHAVIOR")

    def test_producer_summaries_are_not_trusted(self):
        bad = document("failure")
        bad["summaries"] = {"16": {"median_wall_ms": 1.0}, "32": {"median_wall_ms": 9999.0}}
        result = self.qualify(bad)
        self.assertEqual(result["disposition"], "NOT_QUALIFIED")


if __name__ == "__main__":
    unittest.main()
