#!/usr/bin/env python3
"""Fail-closed source and runtime tests for diagnostic-only canonical b4 G3 triage."""
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
script = ROOT / "scripts/diagnose-g3-canonical-b4.py"
spec = importlib.util.spec_from_file_location("g3_b4_triage", script)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class TriageContracts(unittest.TestCase):
    def setUp(self):
        self.canonical = load(module.CANONICAL)
        self.triage = load(module.TRIAGE)
        self.policy = load(module.POLICY)
        self.legacy = load(module.LEGACY)
        self.selection = load(module.SELECTION)

    def check(self):
        module.verify_overlay(self.canonical, self.triage,
                              self.policy, self.legacy, self.selection)

    def test_exact_real_source_overlay(self):
        self.check()
        self.assertEqual(self.triage["shadow"]["replay_root"], module.REPLAY)
        self.assertEqual(len(self.policy["positive_cases"]), 23)

    def test_no_change_to_budget_and_control_authority(self):
        for category in ("budget", "online_time", "routing", "instances",
                         "verification", "crossfeed", "counterfactual", "hybrid_authority"):
            with self.subTest(category=category):
                self.assertEqual(self.triage[category], self.canonical[category])

    def test_runtime_drift_fails_closed(self):
        for field, new in (("MaxPrefetch", 8), ("MinibatchSize", 32)):
            with self.subTest(field=field):
                triage = copy.deepcopy(self.triage)
                triage["instances"]["lc0-shadow"]["options"][field] = new
                with self.assertRaises(module.TriageError):
                    module.verify_overlay(self.canonical, triage, self.policy,
                                          self.legacy, self.selection)
        triage = copy.deepcopy(self.triage)
        triage["budget"]["wall_ms"] += 100
        with self.assertRaises(module.TriageError):
            module.verify_overlay(self.canonical, triage, self.policy,
                                  self.legacy, self.selection)

    def test_policy_migration_and_legacy_changes_fail_closed(self):
        altered = copy.deepcopy(self.policy)
        altered["candidate_witnesses"]["legacy_policy_path"] = "elsewhere.json"
        with self.assertRaises(module.TriageError):
            module.verify_overlay(self.canonical, self.triage, altered,
                                  self.legacy, self.selection)
        altered = copy.deepcopy(self.policy)
        altered["positive_cases"].pop()
        with self.assertRaises(module.TriageError):
            module.verify_overlay(self.canonical, self.triage, altered,
                                  self.legacy, self.selection)
        selection = copy.deepcopy(self.selection)
        selection["selected"]["lc0"]["matrix_profile"] = "b7-p8-c256k-warm64"
        with self.assertRaises(module.TriageError):
            module.verify_overlay(self.canonical, self.triage, self.policy,
                                  self.legacy, selection)


if __name__ == "__main__":
    unittest.main()
