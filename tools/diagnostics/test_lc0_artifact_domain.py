#!/usr/bin/env python3
"""Synthetic contract tests for the LC0 artifact/domain comparator."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.diagnostics.compare_lc0_artifact_domain import (
    DiagnosticError,
    EXPECTED_A,
    EXPECTED_B,
    SURFACE_PATHS,
    _core_digest,
    analyze_matrix_documents,
    classify_diagnosis,
    extract_lc0_identity,
    index_matrix,
    load_sealed_json,
    validate_host_report,
    validate_surface_proof,
    write_sealed_json,
)


PROFILES = [
    "v1-current-cold",
    "b7-p8-c256k-warm64",
    "other-profile",
]
CASES = [f"case-{idx}" for idx in range(8)]


def matrix(
    *,
    source: str,
    binary_hash: str,
    weights_hash: str = "f" * 64,
    bestmove_overrides: dict[tuple[str, int, str], str] | None = None,
    native_overrides: dict[tuple[str, int, str], int] | None = None,
    build_id: str | None = None,
) -> dict:
    bestmove_overrides = bestmove_overrides or {}
    native_overrides = native_overrides or {}
    short = build_id if build_id is not None else source[:7]
    rows = []
    repeat_summaries = {}
    for profile in PROFILES:
        repeat_count = 3 if profile in set(PROFILES[:2]) else 1
        summaries = []
        for repeat in range(repeat_count):
            bestmoves = []
            native = []
            for pos, case_id in enumerate(CASES):
                key = (profile, repeat, case_id)
                move = bestmove_overrides.get(
                    key,
                    ["e2e4", "d2d4", "g1f3", "c2c4"][pos % 4],
                )
                work = native_overrides.get(key, 16 + pos)
                bestmoves.append(move)
                native.append(work)
                rows.append(
                    {
                        "profile": profile,
                        "repeat_index": repeat,
                        "case_id": case_id,
                        "metrics": {
                            "bestmove": move,
                            "native_work_value": work,
                            "native_work_semantics": "lc0.uci_nodes",
                            "wall_ms": 1.0 + pos,
                            "cpu_ms": 1.0 + pos,
                            "rss_kib_end": 100.0,
                            "completed_before_deadline": True,
                        },
                        "transcript": [
                            ">> uci",
                            f"<< id name Lc0 v0.31.0+git.{short}",
                            "<< uciok",
                        ],
                    }
                )
            summaries.append(
                {
                    "cases": 8,
                    "bestmoves": bestmoves,
                    "native_work_values": native,
                    "median_wall_ms": 4.5,
                    "max_wall_ms": 8.0,
                    "median_cpu_ms": 4.5,
                    "max_cpu_ms": 8.0,
                }
            )
        repeat_summaries[profile] = summaries
    return {
        "schema_version": 1,
        "kind": "lc0-cpu-runtime-matrix",
        "source": {"commit": source, "tree": "1" * 40},
        "host": {},
        "binary": {"path": "lc0", "sha256": binary_hash},
        "weights": {"path": "791556.pb.gz", "sha256": weights_hash},
        "nodes": 16,
        "deadline_ms": 3500.0,
        "profiles": list(PROFILES),
        "rows": rows,
        "summaries": {},
        "repeat_summaries": repeat_summaries,
        "confirmation": {
            "selected_profile": PROFILES[1],
            "baseline_profile": PROFILES[0],
            "repeats": 3,
        },
        "errors": [],
    }


class ComparatorTests(unittest.TestCase):
    def test_behaviorally_equivalent(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertEqual(result["diagnosis"], "BEHAVIORALLY_EQUIVALENT")
        self.assertFalse(result["signals"]["a_vs_b_bestmove_drift"])
        self.assertTrue(result["signals"]["b_vs_c_binary_bytes_equal"])

    def test_artifact_correlated_drift(self):
        change = {
            (PROFILES[1], repeat, CASES[0]): "h2h3"
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertEqual(result["diagnosis"], "ARTIFACT_CORRELATED_DRIFT")
        self.assertFalse(result["cross_build"]["a_vs_b"]["selected"]["bestmove_equivalent"])
        self.assertTrue(result["cross_build"]["b_vs_c"]["selected"]["bestmove_equivalent"])

    def test_build_bytes_can_differ_without_behavior_drift(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="c" * 64)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "c" * 64}
        )
        self.assertEqual(
            result["diagnosis"],
            "BUILD_BYTE_NONREPRODUCIBLE_BEHAVIOR_STABLE",
        )

    def test_build_nonreproducible_behavior(self):
        change = {(PROFILES[2], 0, CASES[1]): "a2a3"}
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="c" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "c" * 64}
        )
        self.assertEqual(result["diagnosis"], "BUILD_NONREPRODUCIBLE")
        self.assertTrue(result["signals"]["b_vs_c_bestmove_drift"])

    def test_within_binary_instability_wins_classification(self):
        change = {(PROFILES[1], 1, CASES[0]): "h2h3"}
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64, bestmove_overrides=change)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertEqual(result["diagnosis"], "WITHIN_BINARY_INSTABILITY")
        self.assertFalse(
            result["within_repeatability"]["a"]["selected"]["bestmove_stable"]
        )

    def test_native_work_drift_is_separate_from_bestmove_drift(self):
        native = {
            (PROFILES[1], repeat, CASES[0]): 99
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, native_overrides=native)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, native_overrides=native)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertFalse(result["signals"]["a_vs_b_bestmove_drift"])
        self.assertTrue(result["signals"]["a_vs_b_native_work_drift"])

    def test_selected_and_baseline_are_reported_separately(self):
        change = {
            (PROFILES[1], repeat, CASES[0]): "h2h3"
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertTrue(result["cross_build"]["a_vs_b"]["baseline"]["bestmove_equivalent"])
        self.assertFalse(result["cross_build"]["a_vs_b"]["selected"]["bestmove_equivalent"])

    def test_baseline_drift_is_visible(self):
        change = {
            (PROFILES[0], repeat, CASES[0]): "h2h3"
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertFalse(result["cross_build"]["a_vs_b"]["baseline"]["bestmove_equivalent"])

    def test_missing_repeat_fails_closed(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        doc["rows"] = [
            row
            for row in doc["rows"]
            if not (row["profile"] == PROFILES[1] and row["repeat_index"] == 2)
        ]
        with self.assertRaises(DiagnosticError):
            index_matrix(doc)

    def test_missing_case_fails_closed(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        doc["rows"] = [
            row
            for row in doc["rows"]
            if not (
                row["profile"] == PROFILES[0]
                and row["repeat_index"] == 1
                and row["case_id"] == CASES[-1]
            )
        ]
        with self.assertRaises(DiagnosticError):
            index_matrix(doc)

    def test_case_order_mismatch_fails_closed(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        indexes = [
            idx
            for idx, row in enumerate(doc["rows"])
            if row["profile"] == PROFILES[1] and row["repeat_index"] == 1
        ]
        left, right = indexes[0], indexes[1]
        doc["rows"][left], doc["rows"][right] = doc["rows"][right], doc["rows"][left]
        with self.assertRaises(DiagnosticError):
            index_matrix(doc)

    def test_profile_set_mismatch_fails_closed(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c["profiles"][-1] = "different"
        with self.assertRaises(DiagnosticError):
            analyze_matrix_documents(
                a, b, c,
                binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64},
            )

    def test_network_mismatch_is_visible_to_caller_contract(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64, weights_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, weights_hash="b" * 64)
        self.assertNotEqual(a["weights"]["sha256"], b["weights"]["sha256"])

    def test_surface_mismatch_fails_closed(self):
        proof = {
            "kind": "lc0-artifact-domain-source-equivalence",
            "a": {"commit": EXPECTED_A},
            "b": {"commit": EXPECTED_B},
            "paths": [
                {
                    "path": path,
                    "a_object": "1" * 40,
                    "b_object": "1" * 40,
                    "equal": True,
                }
                for path in SURFACE_PATHS
            ],
            "equivalent": True,
        }
        proof["paths"][0]["b_object"] = "2" * 40
        proof["paths"][0]["equal"] = False
        proof["equivalent"] = False
        with self.assertRaises(DiagnosticError):
            validate_surface_proof(proof)

    def test_tampered_sealed_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sealed.json"
            write_sealed_json(path, {"schema_version": 1, "value": 1})
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["value"] = 2
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(DiagnosticError):
                load_sealed_json(path, label="tampered")

    def test_write_seal_matches_independent_digest(self):
        payload = {"schema_version": 1, "value": ["x", 2]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sealed.json"
            write_sealed_json(path, payload)
            raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["content_sha256"], _core_digest(raw))

    def test_incomplete_host_report_fails_closed(self):
        with self.assertRaises(DiagnosticError):
            validate_host_report(
                {
                    "host_capabilities": {},
                    "runtime_substrate": {},
                    "resource_measurement": {"clock_ticks_per_second": 100},
                }
            )

    def test_malformed_lc0_build_identifier_fails_closed(self):
        doc = matrix(
            source=EXPECTED_A,
            binary_hash="a" * 64,
            build_id="not-a-git-id",
        )
        with self.assertRaises(DiagnosticError):
            extract_lc0_identity(doc, expected_commit=EXPECTED_A)

    def test_classifier_table(self):
        self.assertEqual(
            classify_diagnosis(
                within_unstable=False,
                b_c_bytes_equal=True,
                a_b_behavior_equal=True,
                b_c_behavior_equal=True,
            ),
            "BEHAVIORALLY_EQUIVALENT",
        )
        self.assertEqual(
            classify_diagnosis(
                within_unstable=False,
                b_c_bytes_equal=True,
                a_b_behavior_equal=False,
                b_c_behavior_equal=True,
            ),
            "ARTIFACT_CORRELATED_DRIFT",
        )


if __name__ == "__main__":
    unittest.main()
