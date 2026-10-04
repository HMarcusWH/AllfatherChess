#!/usr/bin/env python3
"""Fail-closed contracts for the frozen b4 resource-profile selection v2."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts/qualify-resource-profile-selection-v2.py"
SPEC = importlib.util.spec_from_file_location("resource_profile_selection_v2", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

EVIDENCE = ROOT / "qualification/resource-profile-evidence-v2.json"
SELECTION = ROOT / "qualification/resource-profile-selection-v2.json"
LAB_SPEC = ROOT / "qualification/resource-lab-v2.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class ResourceProfileSelectionV2Tests(unittest.TestCase):
    def setUp(self):
        self.evidence = load(EVIDENCE)
        self.selection = load(SELECTION)

    def _validate(self, *, evidence=None, selection=None, rebind_evidence=True):
        evidence = copy.deepcopy(self.evidence if evidence is None else evidence)
        selection = copy.deepcopy(self.selection if selection is None else selection)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ep = root / "evidence.json"
            sp = root / "selection.json"
            dump(ep, evidence)
            if rebind_evidence:
                selection["evidence_sha256"] = hashlib.sha256(ep.read_bytes()).hexdigest()
            dump(sp, selection)
            return mod.validate(ep, sp, LAB_SPEC)

    def test_frozen_v2_selection_qualifies(self):
        report = mod.validate(EVIDENCE, SELECTION, LAB_SPEC)
        self.assertTrue(report["qualified"])
        self.assertEqual(report["groups"], 9)
        self.assertEqual(report["pareto_candidates"], 22)
        self.assertEqual(report["selections"]["lc0/n16"], "cache0")
        self.assertEqual(report["selections"]["lc0/n32"], "cache0")
        self.assertEqual(report["selections"]["lc0/n64"], "cache2m")
        self.assertEqual(report["selections"]["stockfish/n64"], "t1-h64")

    def test_evidence_hash_mismatch_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["stage_a"]["errors"] = 1
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(evidence=evidence, rebind_evidence=False)

    def test_incomplete_stage_a_is_rejected_even_when_rebound(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["stage_a"]["completed_measurements"] -= 1
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(evidence=evidence)

    def test_stage_b_error_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["stage_b"]["errors"] = 1
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(evidence=evidence)

    def test_candidate_digest_tampering_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["pareto_candidates"][0]["digest"] = "f" * 64
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(evidence=evidence)

    def test_behavior_gate_tampering_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["pareto_candidates"][0]["flags"][1] = False
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(evidence=evidence)

    def test_non_deterministic_selection_is_rejected(self):
        selection = copy.deepcopy(self.selection)
        target = next(
            row for row in selection["selections"]
            if row["family"] == "stockfish" and row["nodes"] == 64
        )
        target["candidate_id"] = (
            "resource-candidate/stockfish/v2-current/n64/f05e2ab77f9c544c"
        )
        target["candidate_digest"] = (
            "f05e2ab77f9c544c461a863db827c77d62d8a20ad4b566ebf981b8e0617fe1b7"
        )
        target["variant"] = "v2-current"
        target["option_overrides"] = {}
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(selection=selection)

    def test_authority_overclaim_is_rejected(self):
        for key in ("runtime_authority", "resource_authorization", "outward_move"):
            selection = copy.deepcopy(self.selection)
            selection["authority"][key] = True
            with self.subTest(key=key), self.assertRaises(mod.SelectionV2Error):
                self._validate(selection=selection)

    def test_composition_overclaim_is_rejected(self):
        selection = copy.deepcopy(self.selection)
        selection["selections"][0]["composition_qualification"] = "qualified"
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(selection=selection)

    def test_measured_reference_contract_hash_drift_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["source"]["reference_contract"]["runtime_sha256"] = "0" * 64
        with self.assertRaises(mod.SelectionV2Error):
            self._validate(evidence=evidence)


if __name__ == "__main__":
    unittest.main()
