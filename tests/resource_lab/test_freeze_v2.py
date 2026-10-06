#!/usr/bin/env python3
"""Fail-closed tests for the resource-lab-v2 artifact freezer."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts/freeze-resource-profile-evidence-v2.py"
SPEC = importlib.util.spec_from_file_location("freeze_resource_profile_evidence_v2", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

SOURCE = ROOT / "qualification/resource-profile-evidence-v2-source.json"


class FreezeV2Tests(unittest.TestCase):
    def test_frozen_source_metadata_is_well_formed(self):
        source = mod.load_source_metadata(SOURCE)
        self.assertEqual(source["artifact_name"], "resource-lab-v2")
        self.assertEqual(source["workflow_run"], 37204469303)
        self.assertEqual(source["artifact_id"], 11304204017)
        self.assertEqual(len(source["artifact_sha256"]), 64)

    def test_source_metadata_rejects_repository_drift(self):
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
        source["repository"] = "someone/else"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(mod.FreezeError):
                mod.load_source_metadata(path)

    def test_source_metadata_rejects_malformed_artifact_digest(self):
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
        source["artifact_sha256"] = "not-a-digest"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(mod.FreezeError):
                mod.load_source_metadata(path)

    def test_safe_extract_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.json", "{}")
            with self.assertRaises(mod.FreezeError):
                mod.safe_extract_zip(archive, Path(tmp) / "out")

    def test_build_rejects_artifact_digest_before_trusting_contents(self):
        source = mod.load_source_metadata(SOURCE)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "fake.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("resource-lab-v2/report.json", "{}")
            with self.assertRaises(mod.FreezeError) as ctx:
                mod.build_documents(
                    archive=archive,
                    workflow_run=source["workflow_run"],
                    artifact_id=source["artifact_id"],
                    evidence_path="qualification/resource-profile-evidence-v2.json",
                    source_metadata=source,
                )
            self.assertIn("SHA-256", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
