#!/usr/bin/env python3
"""Tests for the narrow legacy-qualification impact classifier."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "classify-qualification-impact.py"
spec = importlib.util.spec_from_file_location("impact_classifier", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QualificationImpactTests(unittest.TestCase):
    def test_current_j2_surface_is_control_plane_only(self):
        paths = sorted(module.CONTROL_PLANE_ONLY)
        self.assertEqual(module.classify(paths), "control_plane_only")

    def test_execution_domain_repair_surface_is_qualification_only(self):
        paths = sorted(module.QUALIFICATION_INFRA_ONLY)
        self.assertEqual(
            module.classify(paths),
            "qualification_infra_only",
        )
        mixed = [
            "controller/host_capabilities.py",
            "controller/runtime_substrate.py",
            ".github/workflows/engine-optimization.yml",
        ]
        self.assertEqual(
            module.classify(mixed),
            "qualification_infra_only",
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
                ]
            ),
            "runtime_affected",
        )


if __name__ == "__main__":
    unittest.main()
