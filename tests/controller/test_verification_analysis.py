#!/usr/bin/env python3
"""COMPARE / RELOCK derived-analysis regressions."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.verification_analysis import (
    EXTRACTOR_VERSION,
    RELOCK_DEFINITION,
    VerificationAnalysisError,
    analyze_verification_bundle,
    build_verification_analysis_artifact,
    derive_relock,
    load_verification_bundle,
    write_verification_analysis_artifact,
)
from tests.fixtures.verification_fixtures import (
    all_different,
    anchor_outside_candidates,
    bestmove_only_relock,
    incomplete_verifier,
    late_relock,
    temporary_unanimity_then_diverge,
    two_one_split,
    unanimous_on_reckless,
)


class VerificationAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_common_support_is_exactly_three_for_every_pair(self):
        run = unanimous_on_reckless(self.root)
        bundle = load_verification_bundle(run)
        analysis = analyze_verification_bundle(bundle)
        self.assertTrue(analysis["analysis_eligible"])
        self.assertEqual(len(analysis["final_pairwise"]), 3)
        self.assertTrue(
            all(item["support"] == 3 for item in analysis["final_pairwise"])
        )
        self.assertEqual(
            [(item["left"], item["right"]) for item in analysis["final_pairwise"]],
            [
                ("stockfish-shadow", "reckless-shadow"),
                ("stockfish-shadow", "lc0-shadow"),
                ("reckless-shadow", "lc0-shadow"),
            ],
        )

    def test_explore_to_verify_adoption_is_attributed_to_original_nominator(self):
        run = unanimous_on_reckless(self.root)
        analysis = analyze_verification_bundle(load_verification_bundle(run))
        rows = {item["owner"]: item for item in analysis["explore_to_verify"]}

        self.assertTrue(rows["stockfish"]["changed"])
        self.assertEqual(rows["stockfish"]["selected_nominee_owner"], "reckless")
        self.assertFalse(rows["stockfish"]["self_retained"])

        self.assertFalse(rows["reckless"]["changed"])
        self.assertEqual(rows["reckless"]["selected_nominee_owner"], "reckless")
        self.assertTrue(rows["reckless"]["self_retained"])

        self.assertTrue(rows["lc0"]["changed"])
        self.assertEqual(rows["lc0"]["selected_nominee_owner"], "reckless")

    def test_unanimous_terminal_suffix_is_descriptive_relock(self):
        run = late_relock(self.root)
        bundle = load_verification_bundle(run)
        relock = derive_relock(bundle)
        self.assertEqual(relock.status, "RELOCK_OBSERVED")
        self.assertEqual(relock.move, "d2d4")
        self.assertIsNotNone(relock.relock_at_ms)
        self.assertIsNotNone(relock.relock_fraction)
        self.assertGreaterEqual(relock.relock_fraction, 0.0)
        self.assertLessEqual(relock.relock_fraction, 1.0)
        self.assertTrue(
            all(
                relock.terminal_lock_start_by_owner[owner] is not None
                for owner in ("stockfish", "reckless", "lc0")
            )
        )

    def test_bestmove_only_terminal_lock_starts_at_completion(self):
        run = bestmove_only_relock(self.root)
        relock = derive_relock(load_verification_bundle(run))
        self.assertEqual(relock.status, "RELOCK_OBSERVED")
        self.assertEqual(relock.move, "d2d4")
        self.assertTrue(
            all(
                value == 0.0
                for value in relock.post_lock_observed_ms_by_owner.values()
            )
        )

    def test_complete_non_unanimous_runs_fail_relock_without_becoming_undefined(self):
        for builder, pattern in (
            (two_one_split, "two_one"),
            (all_different, "all_different"),
            (temporary_unanimity_then_diverge, "all_different"),
        ):
            run = builder(self.root)
            analysis = analyze_verification_bundle(load_verification_bundle(run))
            self.assertEqual(analysis["final_pattern"], pattern)
            self.assertEqual(analysis["relock"]["status"], "RELOCK_FAILED")

    def test_incomplete_verifier_is_preserved_but_excluded_from_three_way_analysis(self):
        run = incomplete_verifier(self.root)
        bundle = load_verification_bundle(run)
        analysis = analyze_verification_bundle(bundle)
        self.assertFalse(analysis["analysis_eligible"])
        self.assertEqual(analysis["checkpoints"], [])
        self.assertEqual(analysis["final_pairwise"], [])
        self.assertEqual(analysis["relock"]["status"], "RELOCK_UNDEFINED")

    def test_anchor_relation_is_descriptive_and_can_be_outside_verify_set(self):
        run = anchor_outside_candidates(self.root)
        analysis = analyze_verification_bundle(load_verification_bundle(run))
        relation = analysis["anchor_relation"]
        self.assertEqual(relation["anchor_final_move"], "a2a3")
        self.assertFalse(relation["anchor_in_verify_candidate_set"])
        self.assertEqual(relation["verifiers_matching_anchor_final"], 0)
        self.assertIsNone(relation["unanimous_matches_anchor"])

    def test_content_address_is_stable_for_same_inputs_and_sensitive_to_parameters(self):
        run = unanimous_on_reckless(self.root)
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        first = build_verification_analysis_artifact([run], now=now)
        second = build_verification_analysis_artifact([run], now=now)
        changed = build_verification_analysis_artifact(
            [run],
            top_k=2,
            now=now,
        )
        self.assertEqual(first.analysis_id, second.analysis_id)
        self.assertNotEqual(first.analysis_id, changed.analysis_id)
        self.assertEqual(first.as_dict()["extractor_version"], EXTRACTOR_VERSION)
        self.assertEqual(first.as_dict()["relock_definition"], RELOCK_DEFINITION)

    def test_write_is_idempotent_only_for_same_derived_contents(self):
        run = unanimous_on_reckless(self.root)
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        artifact = build_verification_analysis_artifact([run], now=now)
        derived = self.root / "derived"
        first = write_verification_analysis_artifact(artifact, derived)
        second = write_verification_analysis_artifact(artifact, derived)
        self.assertEqual(first, second)
        data = json.loads(first.read_text(encoding="utf-8"))
        self.assertEqual(data["analysis_id"], artifact.analysis_id)

    def test_tampered_child_stream_is_refused_before_analysis(self):
        run = unanimous_on_reckless(self.root)
        manifest = json.loads(
            (run / "verification" / "manifest.json").read_text(encoding="utf-8")
        )
        stream = run / "verification" / manifest["streams"][0]["path"]
        stream.write_text(stream.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(VerificationAnalysisError) as ctx:
            load_verification_bundle(run)
        self.assertIn("verification integrity failed", str(ctx.exception))

    def test_parent_child_nominee_mismatch_is_refused(self):
        run = unanimous_on_reckless(self.root)
        path = run / "verification" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["nomination"]["nominees_by_owner"]["stockfish"] = "d2d4"
        # Re-hashing the child manifest is irrelevant: the semantic cross-check
        # must reject a child that contradicts the parent EXPLORE result.
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(VerificationAnalysisError):
            load_verification_bundle(run)


class ScaleFirewallRegressionTests(unittest.TestCase):
    def test_compare_relock_artifact_contains_no_cross_engine_numeric_score_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = unanimous_on_reckless(Path(tmp))
            analysis = analyze_verification_bundle(load_verification_bundle(run))
            blob = json.dumps(analysis, sort_keys=True).lower()
            for forbidden in (
                "score_delta",
                "centipawn_delta",
                "cross_engine_margin",
                "correct_move",
                "winner",
            ):
                self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
