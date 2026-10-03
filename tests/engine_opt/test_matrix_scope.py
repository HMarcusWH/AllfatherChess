#!/usr/bin/env python3
"""Regression tests for qualification-only LC0 matrix scope."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location(
    "engine_opt_matrix",
    ROOT / "scripts/engine-opt-matrix.py",
)
assert SPEC and SPEC.loader
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


class MatrixScopeTests(unittest.TestCase):
    def test_qualification_only_runs_exactly_baseline_and_selected(self):
        rows = matrix.profiles_for_run(
            "b7-p8-c256k-warm64",
            "v1-current-cold",
            True,
        )
        self.assertEqual(
            [row[0] for row in rows],
            ["v1-current-cold", "b7-p8-c256k-warm64"],
        )

    def test_candidate_qualification_only_ignores_unrelated_exploratory_profiles(self):
        rows = matrix.profiles_for_run(
            "b4-p0-c256k-cold",
            "v1-current-cold",
            True,
        )
        names = [row[0] for row in rows]
        self.assertEqual(names, ["v1-current-cold", "b4-p0-c256k-cold"])
        self.assertNotIn("v1-current-warm64", names)
        self.assertNotIn("auto-adaptive-c2m-telemetry", names)

    def test_full_matrix_retains_all_exploratory_profiles(self):
        rows = matrix.profiles_for_run(
            "b4-p0-c256k-cold",
            "v1-current-cold",
            False,
        )
        self.assertEqual(rows, matrix.PROFILES)


if __name__ == "__main__":
    unittest.main()
