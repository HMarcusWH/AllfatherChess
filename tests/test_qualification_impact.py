#!/usr/bin/env python3
"""Tests for the fail-closed legacy-qualification impact classifier."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "classify-qualification-impact.py"
spec = importlib.util.spec_from_file_location("impact_classifier", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QualificationImpactTests(unittest.TestCase):
    def test_host_observation_surface_remains_control_plane_only(self):
        paths = sorted(module.ALWAYS_CONTROL_PLANE)
        self.assertEqual(
            module.classify(paths),
            "control_plane_only",
        )

    def test_exact_j2_bootstrap_may_include_ci_plumbing(self):
        paths = sorted(
            module.ALWAYS_CONTROL_PLANE | module.J2_BOOTSTRAP_ONLY
        )
        self.assertEqual(
            module.classify(
                paths,
                base_sha=module.J2_BOOTSTRAP_BASE_SHA,
            ),
            "control_plane_only",
        )

    def test_workflow_or_makefile_edits_are_not_future_exemptions(self):
        for path in (
            ".github/workflows/engine-optimization.yml",
            ".github/workflows/full-game-qualification.yml",
            ".github/workflows/lc0-strength-qualification.yml",
            ".github/workflows/online-profile-qualification.yml",
            ".github/workflows/online-hybrid-qualification.yml",
            ".github/workflows/baseline.yml",
            "Makefile",
            "scripts/classify-qualification-impact.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    module.classify(
                        [path],
                        base_sha="0" * 40,
                    ),
                    "runtime_affected",
                )

    def test_runtime_controller_change_is_not_exempt(self):
        self.assertEqual(
            module.classify(["controller/runtime.py"]),
            "runtime_affected",
        )

    def test_engine_config_and_qualification_changes_are_not_exempt(self):
        for path in (
            "engines/lc0/src/search/classic/search.cc",
            "config/allfather.online-hybrid-v2.validation.json",
            "qualification/engine-opt-v2-selection.json",
            "scripts/qualify-online-hybrid-v2.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    module.classify([path]),
                    "runtime_affected",
                )

    def test_empty_or_unsafe_input_is_runtime_affected(self):
        self.assertEqual(module.classify([]), "runtime_affected")
        self.assertEqual(
            module.classify(["../controller/runtime.py"]),
            "runtime_affected",
        )

    def test_one_runtime_file_poisoning_control_plane_set_fails_closed(self):
        self.assertEqual(
            module.classify(
                [
                    "controller/host_capabilities.py",
                    "controller/runtime.py",
                ],
                base_sha=module.J2_BOOTSTRAP_BASE_SHA,
            ),
            "runtime_affected",
        )


if __name__ == "__main__":
    unittest.main()
