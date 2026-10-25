"""Adversarial participation and denial-evidence reconstruction for the pilot."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.g3_pilot_health import (
    aggregate_backend_coverage, observe_replay_backend,
)
from tools.g3_pilot_preflight import check_linker_output
from tools.local_game.common import QualificationError


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True) + "\n")


class BackendHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)/"replay-0001"
        self.run.mkdir()
        self.manifest = {
            "shadow_health": {
                name: {"instance": name, "alive": True, "failure": None}
                for name in ("stockfish-shadow", "reckless-shadow", "lc0-shadow")
            },
            "stages": [
                {"owner": owner, "phase": phase,
                 "disposition": "completed", "failure": None}
                for phase in ("EXPLORE", "VERIFY")
                for owner in ("stockfish", "reckless", "lc0")
            ],
        }
        self.final = {
            "sources": {},
            "decision": {
                "authority": "ANCHOR_FALLBACK",
                "anchor_move": "e2e4",
                "emitted_move": "e2e4",
                "proposal_move": None,
                "proposal_evidence_digest": None,
                "authorization": {
                    "authorized": False, "reason": "no verified proposal",
                },
                "authorization_snapshot": {
                    "authority_evidence_frozen_before_soft_deadline": False,
                },
            }
        }
        self.persist()

    def tearDown(self):
        self.tmp.cleanup()

    def persist(self):
        write(self.run/"manifest.json", self.manifest)
        write(self.run/"decision/final.json", self.final)

    def test_completed_three_family_search_not_equal_hybrid_move(self):
        row = observe_replay_backend(self.run)
        aggregate = aggregate_backend_coverage([row])
        self.assertTrue(aggregate["three_family_evidence_complete"])
        self.assertEqual(aggregate["lc0_explore_stages_observed"], 1)
        self.assertEqual(aggregate["three_family_explore_completions"], 1)
        # Top-level synthetic EXPLORE stages are not nested VERIFY evidence.
        self.assertEqual(aggregate["verify_stages_completed"], 0)
        self.assertEqual(aggregate["verify_attempted"], 0)
        self.assertEqual(aggregate["staged_verify_stages_completed"], 0)
        self.assertEqual(aggregate["hybrid_authorizations"], 0)
        self.assertEqual(aggregate["proposals_frozen_before_deadline"], 0)
        self.assertEqual(aggregate["authorization_denial_reasons"]["no verified proposal"], 1)

    def test_lc0_crash_witness_cannot_be_interpreted_as_three_engines(self):
        self.manifest["shadow_health"]["lc0-shadow"] = {
            "instance": "lc0-shadow", "alive": False,
            "failure": "engine exited unexpectedly; rc=127",
        }
        self.persist()
        aggregate = aggregate_backend_coverage([observe_replay_backend(self.run)])
        self.assertFalse(aggregate["three_family_evidence_complete"])
        self.assertEqual(aggregate["backend_failure_count"], 1)
        self.assertIn("rc=127", aggregate["backend_startup_failures"][0]["failure"])

    def test_missing_lc0_liveness_is_explicit_negative_evidence(self):
        del self.manifest["shadow_health"]["lc0-shadow"]
        self.persist()
        aggregate = aggregate_backend_coverage([observe_replay_backend(self.run)])
        self.assertEqual(aggregate["backend_failure_count"], 1)
        self.assertFalse(aggregate["three_family_evidence_complete"])

    def test_proposal_only_counts_with_predeadline_evidence(self):
        d = self.final["decision"]
        d["proposal_move"] = "d2d4"
        d["proposal_evidence_digest"] = "f"*64
        self.persist()
        row = observe_replay_backend(self.run)
        self.assertEqual(row["proposals_frozen_before_deadline"], 0)
        d["authorization_snapshot"]["authority_evidence_frozen_before_soft_deadline"] = True
        self.persist()
        row = observe_replay_backend(self.run)
        self.assertEqual(row["proposals_frozen_before_deadline"], 1)
        self.assertEqual(row["actual_anchor_changing_overrides"], 0)

    def test_authorization_and_move_change_separately_counted(self):
        d = self.final["decision"]
        d["authority"] = "HYBRID"
        d["emitted_move"] = "d2d4"
        d["authorization"] = {"authorized": True, "reason": "authorized"}
        d["proposal_move"] = "d2d4"
        d["proposal_evidence_digest"] = "f"*64
        d["authorization_snapshot"]["authority_evidence_frozen_before_soft_deadline"] = True
        self.persist()
        a = aggregate_backend_coverage([observe_replay_backend(self.run)])
        self.assertEqual(a["hybrid_authorizations"], 1)
        self.assertEqual(a["anchor_changing_overrides"], 1)

    def test_missing_stage_set_and_empty_campaign_are_denied(self):
        self.manifest.pop("stages")
        self.persist()
        with self.assertRaisesRegex(QualificationError, "stage"):
            observe_replay_backend(self.run)
        with self.assertRaises(QualificationError):
            aggregate_backend_coverage([])


class NativeLinkerTests(unittest.TestCase):
    def test_native_dependencies_must_resolve(self):
        check_linker_output(0, "libprotobuf.so => /usr/lib/libprotobuf.so\n"
                              "libopenblas.so => /usr/lib/libopenblas.so\n")
        for rc, out in (
            (127, "error while loading shared libraries: libprotobuf.so"),
            (0, "libprotobuf.so => not found"),
            (0, ""),
        ):
            with self.subTest(rc=rc, out=out):
                with self.assertRaises(QualificationError):
                    check_linker_output(rc, out)


if __name__ == "__main__":
    unittest.main()
