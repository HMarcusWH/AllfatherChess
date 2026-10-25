"""Offline deterministic documentation regression and mutation checks."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "docs_consistency", ROOT/"scripts/check-docs-consistency.py")
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


class DocsConsistencyTests(unittest.TestCase):
    def test_repository_document_inventory_and_authority(self):
        report = docs.audit(ROOT)
        self.assertEqual(report["status"], "SOURCE_DOCUMENTS_CONSISTENT")
        self.assertGreaterEqual(report["docs"], 60)
        self.assertEqual(report["docs"] - report["indexed"], 1)
        self.assertGreater(report["links_checked"], 20)
        self.assertFalse(report["public_bot_release"])
        self.assertFalse(report["live_game_authorized"])

    def test_index_missing_or_duplicate_document_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root/"docs"
            folder.mkdir()
            (folder/"one.md").write_text("# One\n")
            (folder/"two.md").write_text("# Two\n")
            index = folder/"README.md"
            index.write_text("# Index\n\n- [One](one.md)\n")
            with self.assertRaisesRegex(docs.DocsDrift, "index coverage"):
                docs.indexed_docs(root)
            index.write_text("# Index\n\n- [One](one.md)\n- [Two](two.md)\n- [Two](two.md)\n")
            with self.assertRaisesRegex(docs.DocsDrift, "duplicate"):
                docs.indexed_docs(root)

    def test_fenced_examples_cannot_mask_real_broken_link(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            folder=root/"docs"
            folder.mkdir()
            (folder/"README.md").write_text("# Index\n- [One](one.md)\n")
            page=folder/"one.md"
            page.write_text("# Example\n```md\n[ignored](missing.md)\n```\n"
                            "[real](README.md)\n")
            self.assertEqual(docs.validate_local_links(root), 2)
            page.write_text(page.read_text()+"[broken](missing.md)\n")
            with self.assertRaisesRegex(docs.DocsDrift, "broken local"):
                docs.validate_local_links(root)

    def test_positive_release_policy_bit_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for relative in docs.DENIALS:
                dst=root/relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((ROOT/relative).read_bytes())
            self.assertGreater(docs.check_denials(root), 20)
            chosen=root/"qualification/online-live-canary-v1.json"
            content=json.loads(chosen.read_text())
            content["live_game_authorized"]=True
            chosen.write_text(json.dumps(content))
            with self.assertRaisesRegex(docs.DocsDrift, "denied authority"):
                docs.check_denials(root)

    def test_stale_active_status_or_roadmap_is_refused(self):
        source=(ROOT/"docs/CURRENT_STATUS.md").read_text()
        roadmap=(ROOT/"docs/ROADMAP.md").read_text()
        readme=(ROOT/"README.md").read_text()
        docs.check_active_status(source, roadmap, readme)
        with self.assertRaisesRegex(docs.DocsDrift, "missing active status"):
            docs.check_active_status(source.replace("DRY_RUN_PASS","DRY_RUN_PENDING",1),
                                     roadmap, readme)
        with self.assertRaisesRegex(docs.DocsDrift, "roadmap missing"):
            docs.check_active_status(source,
                                     roadmap.replace("RELEASE-2B-4","RELEASE-2B-X"),
                                     readme)
        with self.assertRaisesRegex(docs.DocsDrift, "root README"):
            docs.check_active_status(
                source, roadmap, readme.replace("b1ee33b19b6c","000000000000",1))
        with self.assertRaisesRegex(docs.DocsDrift, "full baseline SHA"):
            docs.check_active_status(
                source.replace("b1ee33b19b6cb9865d9c99d413041a8f6e8d468a",
                               "not-a-sha",1), roadmap, readme)


if __name__ == "__main__":
    unittest.main()
