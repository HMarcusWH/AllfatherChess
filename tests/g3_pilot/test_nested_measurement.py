"""S0: source-locked nested VERIFY evidence and destructive measurement tests.

Mocking the pre-existing heavy stream-integrity functions only here allows the
unit tests to exercise the new source-index, hash and stage classification.
The old verifiers retain their own real-stream mutation tests; the archived
campaign integration job exercises both together.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.g3_pilot_health import (
    observe_replay_backend, aggregate_backend_coverage,
)
from tools.local_game.common import QualificationError, sha


OWNERS = ("stockfish", "reckless", "lc0")


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


class NestedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "replay-unique-01"
        self.run.mkdir()
        write(self.run / "manifest.json", {
            "schema_version": 1,
            "run_id": "replay-unique-01",
            "shadow_health": {
                f"{owner}-shadow": {"alive": True, "failure": None}
                for owner in OWNERS
            },
            "stages": [{"owner": owner, "phase": "EXPLORE",
                        "disposition": "completed", "failure": None}
                       for owner in OWNERS],
        })
        self.final = {
            "sources": {},
            "decision": {
                "authority": "ANCHOR_FALLBACK",
                "anchor_move": "e2e4",
                "emitted_move": "e2e4",
                "authorization": {"authorized": False,
                                  "reason": "no frozen hybrid proposal"},
                "authorization_snapshot": {
                    "authority_evidence_frozen_before_soft_deadline": False,
                },
            },
        }
        self.flush()

    def tearDown(self):
        self.tmp.cleanup()

    def flush(self):
        write(self.run / "decision/final.json", self.final)

    def nested(self, name: str, *, complete: bool = True):
        stages = [
            {"owner": owner, "family": owner,
             "disposition": "completed" if complete or owner != "lc0" else "stopped",
             "failure": None, "bestmove": "e2e4" if complete or owner != "lc0" else None}
            for owner in OWNERS
        ]
        manifest = {
            "schema_version": 1,
            "disposition": {"run": "completed" if complete else "incomplete"},
            "stages": stages,
        }
        if name == "staged_verification":
            manifest["intervention"] = "same_process_staged_verify_v1"
        path = self.run / name / "manifest.json"
        write(path, manifest)
        self.final["sources"][name+"/manifest.json"] = sha(path)
        self.flush()
        return path

    def observed(self):
        with patch("controller.verification.verify_verification_integrity",
                   return_value=[]), patch(
                "controller.staged_verification.verify_staged_verification_integrity",
                return_value=[]):
            return observe_replay_backend(self.run)

    def test_verified_complete_verify_and_staged_sets(self):
        self.nested("verification")
        self.nested("staged_verification")
        result = aggregate_backend_coverage([self.observed()])
        self.assertEqual(result["verify_stages_completed"], 1)
        self.assertEqual(result["verify_attempted"], 1)
        self.assertEqual(result["staged_verify_stages_completed"], 1)
        self.assertEqual(result["staged_verify_attempted"], 1)
        self.assertEqual(result["staged_verify_incomplete"], 0)
        self.assertEqual(result["verify_owner_stage_completions"], 3)
        self.assertEqual(result["staged_verify_owner_stage_completions"], 3)

    def test_incomplete_staged_evidence_not_laundered_into_pass(self):
        self.nested("verification")
        self.nested("staged_verification", complete=False)
        result = aggregate_backend_coverage([self.observed()])
        self.assertEqual(result["verify_stages_completed"], 1)
        self.assertEqual(result["staged_verify_stages_completed"], 0)
        self.assertEqual(result["staged_verify_incomplete"], 1)
        self.assertEqual(result["staged_verify_owner_stage_completions"], 2)

    def test_missing_declared_nested_file_is_rejected(self):
        path = self.nested("verification")
        path.unlink()
        with self.assertRaisesRegex(QualificationError, "presence"):
            self.observed()

    def test_undeclared_orphan_nested_directory_is_rejected(self):
        self.nested("verification")
        self.final["sources"].pop("verification/manifest.json")
        self.flush()
        with self.assertRaisesRegex(QualificationError, "presence"):
            self.observed()

    def test_nested_manifest_tamper_is_rejected(self):
        path = self.nested("verification")
        data = json.loads(path.read_text())
        data["stages"][0]["bestmove"] = "d2d4"
        write(path, data)
        with self.assertRaisesRegex(QualificationError, "hash"):
            self.observed()

    def test_stream_integrity_failure_is_not_a_positive_count(self):
        self.nested("verification")
        with patch("controller.verification.verify_verification_integrity",
                   return_value=["tampered verification stream SHA256"]):
            with self.assertRaisesRegex(QualificationError, "integrity"):
                observe_replay_backend(self.run)

    def test_bad_completed_owner_triple_rejected_even_if_integrity_mock_is_clean(self):
        path = self.nested("verification")
        data = json.loads(path.read_text())
        data["stages"][2]["owner"] = "reckless"
        write(path, data)
        self.final["sources"]["verification/manifest.json"] = sha(path)
        self.flush()
        with self.assertRaisesRegex(QualificationError, "duplicated"):
            self.observed()

    def test_staged_without_completed_base_rejected(self):
        self.nested("verification", complete=False)
        self.nested("staged_verification")
        with self.assertRaisesRegex(QualificationError, "without completed base"):
            self.observed()


if __name__ == "__main__":
    unittest.main()
