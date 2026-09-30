#!/usr/bin/env python3
"""Fail-closed mutations for the M14-J J7 selection freeze."""
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
SCRIPT = ROOT / "scripts/qualify-resource-profile-selection.py"
spec = importlib.util.spec_from_file_location("j7_selection_qualifier", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

EVIDENCE = ROOT / "qualification/resource-profile-evidence-v1.json"
SELECTION = ROOT / "qualification/resource-profile-selection-v1.json"
CATALOG = ROOT / "qualification/resource-profile-catalog-v1.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "
", encoding="utf-8")


class J7SelectionTests(unittest.TestCase):
    def setUp(self):
        self.evidence = load(EVIDENCE)
        self.selection = load(SELECTION)
        self.catalog = load(CATALOG)

    def _validate(self, evidence=None, selection=None, catalog=None, *, rebind_evidence=True, rebind_catalog=True):
        evidence = copy.deepcopy(self.evidence if evidence is None else evidence)
        selection = copy.deepcopy(self.selection if selection is None else selection)
        catalog = copy.deepcopy(self.catalog if catalog is None else catalog)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ep, sp, cp = root / "evidence.json", root / "selection.json", root / "catalog.json"
            dump(ep, evidence)
            dump(cp, catalog)
            if rebind_evidence:
                selection["evidence_sha256"] = hashlib.sha256(ep.read_bytes()).hexdigest()
            if rebind_catalog:
                selection["catalog"]["sha256"] = hashlib.sha256(cp.read_bytes()).hexdigest()
            dump(sp, selection)
            return mod.validate(ep, sp, cp)

    def test_frozen_selection_qualifies(self):
        report = mod.validate(EVIDENCE, SELECTION, CATALOG)
        self.assertTrue(report["qualified"])
        self.assertEqual(report["groups"], 9)
        self.assertEqual(report["pareto_candidates"], 18)
        self.assertEqual(report["selections"]["stockfish/n64"], "t1-h32")
        self.assertEqual(report["selections"]["lc0/n32"], "prefetch0")

    def test_wrong_evidence_sha_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["stage_a"]["errors"] = 1
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(evidence=evidence, rebind_evidence=False)

    def test_stale_candidate_digest_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        target = next(x for x in evidence["pareto_candidates"] if x["id"] == self.selection["selections"][0]["candidate_id"])
        target["digest"] = "f" * 64
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(evidence=evidence)

    def test_non_pareto_selection_is_rejected(self):
        selection = copy.deepcopy(self.selection)
        row = next(x for x in selection["selections"] if x["family"] == "lc0" and x["nodes"] == 32)
        row["candidate_id"] = "resource-candidate/lc0/v2-current/n32/f5a2d1be7a1af59c"
        row["candidate_digest"] = "f5a2d1be7a1af59c77209ba4ed60c230a3a7de19355479a48afdd1dce0e5ec10"
        row["variant"] = "v2-current"
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(selection=selection)

    def test_unstable_candidate_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        cid = next(x for x in self.selection["selections"] if x["family"] == "stockfish" and x["nodes"] == 64)["candidate_id"]
        target = next(x for x in evidence["pareto_candidates"] if x["id"] == cid)
        target["flags"][1] = False
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(evidence=evidence)

    def test_cpu_failure_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        cid = next(x for x in self.selection["selections"] if x["family"] == "lc0" and x["nodes"] == 32)["candidate_id"]
        target = next(x for x in evidence["pareto_candidates"] if x["id"] == cid)
        target["flags"][3] = False
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(evidence=evidence)

    def test_duplicate_selection_is_rejected(self):
        selection = copy.deepcopy(self.selection)
        selection["selections"].append(copy.deepcopy(selection["selections"][0]))
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(selection=selection)

    def test_missing_group_is_rejected(self):
        selection = copy.deepcopy(self.selection)
        selection["selections"].pop()
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(selection=selection)

    def test_authority_mutation_is_rejected(self):
        for key in ("runtime_authority", "resource_authorization", "outward_move"):
            selection = copy.deepcopy(self.selection)
            selection["authority"][key] = True
            with self.subTest(key=key), self.assertRaises(mod.SelectionQualificationError):
                self._validate(selection=selection)

    def test_composition_overclaim_is_rejected(self):
        selection = copy.deepcopy(self.selection)
        selection["selections"][0]["composition_qualification"] = "qualified"
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(selection=selection)

    def test_catalog_selection_enablement_is_rejected(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["selection_enabled"] = True
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(catalog=catalog)

    def test_catalog_digest_mismatch_is_rejected(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["fallback_profile"] = "wrong"
        with self.assertRaises(mod.SelectionQualificationError):
            self._validate(catalog=catalog, rebind_catalog=False)


if __name__ == "__main__":
    unittest.main()
