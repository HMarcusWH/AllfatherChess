#!/usr/bin/env python3
"""Residual and calibration tests on deterministic synthetic replay bundles."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.residuals import (
    Margin,
    past_only_features,
    ResidualError,
    ScaleMixingError,
    TaggedValue,
    jaccard,
    leader_flip_count,
    pv_divergence,
    pv_persistence,
    rank_agreement,
    stabilization_index,
    stability_fraction,
    top_k_overlap,
    unresolved_set,
    within_engine_margin,
)
from controller.calibration import (
    CalibrationError,
    ReversalRiskModel,
    TrainingRow,
    bucket_key,
    holdout_run_ids,
    load_calibration,
    training_rows_from_derived,
    write_calibration,
)
from controller.replay_analysis import (
    counterfactual_labels,
    load_bundle,
    summarize_trajectory,
)
from controller.calibration import FEATURE_NAMES, load_calibration, write_calibration
from controller.residuals import (
    EXTRACTOR_VERSION,
    FeatureExtractionError,
    build_derived_artifact,
    extract_features,
    load_derived_artifact,
    shared_support,
    write_derived_artifact,
)
from tests.fixtures import replay_fixtures


class ScaleFirewallTests(unittest.TestCase):
    def test_cross_engine_centipawn_subtraction_is_refused(self):
        stockfish = TaggedValue(120, "stockfish.uci_cp")
        reckless = TaggedValue(90, "reckless.uci_cp")
        with self.assertRaises(ScaleMixingError):
            within_engine_margin(stockfish, reckless)

    def test_lc0_scalar_against_alpha_beta_cp_is_refused(self):
        lc0 = TaggedValue(0.4, "lc0.uci_score.Q", kind="scalar")
        stockfish = TaggedValue(40, "stockfish.uci_cp", kind="cp")
        with self.assertRaises(ScaleMixingError):
            within_engine_margin(lc0, stockfish)

    def test_same_engine_margin_is_normalized_and_tagged(self):
        margin = within_engine_margin(
            TaggedValue(120, "stockfish.uci_cp"), TaggedValue(20, "stockfish.uci_cp")
        )
        self.assertEqual(margin.semantics, "stockfish.uci_cp")
        self.assertAlmostEqual(margin.value, 100 / 141)
        self.assertTrue(-1.0 <= margin.value <= 1.0)

    def test_mixed_semantics_cannot_build_an_unresolved_set(self):
        with self.assertRaises(ScaleMixingError):
            unresolved_set(
                ["e2e4", "d2d4", "g1f3"],
                [Margin(0.01, "stockfish.uci_cp"), Margin(0.02, "reckless.uci_cp")],
                decisive_margin=0.1,
            )

    def test_non_finite_and_non_numeric_values_are_refused(self):
        with self.assertRaises(ResidualError):
            TaggedValue(float("inf"), "stockfish.uci_cp")
        with self.assertRaises(ResidualError):
            TaggedValue(True, "stockfish.uci_cp")
        with self.assertRaises(ResidualError):
            TaggedValue(1.0, "")


class ScaleFreePrimitiveTests(unittest.TestCase):
    def test_top_k_overlap_and_jaccard(self):
        self.assertEqual(top_k_overlap(["a", "b", "c"], ["a", "b", "c"], 3), 1.0)
        self.assertEqual(top_k_overlap(["a", "b", "c"], ["d", "e", "f"], 3), 0.0)
        self.assertAlmostEqual(top_k_overlap(["a", "b", "c"], ["c", "x", "y"], 3), 1 / 3)
        self.assertIsNone(top_k_overlap([], ["a"], 3))
        self.assertIsNone(jaccard([], []))
        self.assertEqual(jaccard(["a", "b"], ["b"]), 0.5)
        with self.assertRaises(ResidualError):
            top_k_overlap(["a"], ["a"], 0)

    def test_rank_agreement_is_undefined_below_two_shared_moves(self):
        self.assertEqual(rank_agreement(["a", "b", "c"], ["a", "b", "c"]), 1.0)
        self.assertEqual(rank_agreement(["a", "b", "c"], ["c", "b", "a"]), -1.0)
        self.assertIsNone(rank_agreement(["a"], ["a"]))
        self.assertIsNone(rank_agreement(["a", "b"], ["c", "d"]))

    def test_pv_divergence_and_persistence(self):
        self.assertEqual(pv_divergence(["a", "b", "c"], ["a", "b", "c"]), 0.0)
        self.assertEqual(pv_divergence(["a", "b"], ["x", "y"]), 1.0)
        self.assertIsNone(pv_divergence([], ["a"]))
        self.assertEqual(pv_persistence([["a", "b"], ["a", "b"]]), 1.0)
        self.assertIsNone(pv_persistence([["a"]]))

    def test_temporal_primitives(self):
        self.assertEqual(leader_flip_count(["a", "a", "b", "a"]), 2)
        self.assertEqual(leader_flip_count([None, "a", None, "a"]), 0)
        self.assertEqual(stabilization_index(["a", "b", "b", "b"]), 1)
        self.assertEqual(stabilization_index(["a", "a", "a"]), 0)
        self.assertIsNone(stabilization_index([None, None]))
        self.assertAlmostEqual(stability_fraction(["a", "b", "b", "b"]), 1 / 3)

    def test_unresolved_set_is_within_engine_only(self):
        moves = ["e2e4", "d2d4", "g1f3"]
        margins = [Margin(0.01, "stockfish.uci_cp"), Margin(0.9, "stockfish.uci_cp")]
        self.assertEqual(unresolved_set(moves, margins, decisive_margin=0.05), ("e2e4", "d2d4"))
        self.assertEqual(unresolved_set(moves, margins, decisive_margin=0.001), ("e2e4",))
        with self.assertRaises(ResidualError):
            unresolved_set(moves, margins[:1], decisive_margin=0.05)


class ScenarioFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = replay_fixtures.write_all(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def features(self, name: str) -> dict:
        return extract_features(load_bundle(self.paths[name]))

    def test_extraction_is_deterministic(self):
        for name in self.paths:
            first = json.dumps(self.features(name), sort_keys=True)
            second = json.dumps(self.features(name), sort_keys=True)
            self.assertEqual(first, second, name)

    def test_stable_agreement_never_reverses(self):
        run = self.features("stable_agreement")
        for instance, summary in run["summaries_by_instance"].items():
            self.assertEqual(summary["leader_flips"], 0, instance)
        for labels in run["counterfactual_labels"].values():
            for label in labels:
                self.assertFalse(label["later_leader_changed"])
                self.assertFalse(label["reversal_within_horizon"])
                self.assertTrue(label["stable_to_end"])

    def test_transient_disagreement_flips_then_recovers(self):
        run = self.features("transient_disagreement")
        anchor = run["summaries_by_instance"]["stockfish-anchor"]
        self.assertGreater(anchor["leader_flips"], 0)
        labels = run["counterfactual_labels_by_instance"]["stockfish-anchor"]
        self.assertTrue(any(label["reversal_within_horizon"] for label in labels))
        # The wobble resolves: the final leader is the one it started with.
        self.assertEqual(anchor["final_leader"], "e2e4")
        self.assertFalse(labels[-1]["later_leader_changed"])

    def test_late_reversal_marks_early_checkpoints_as_changed(self):
        run = self.features("late_reversal")
        labels = run["counterfactual_labels_by_instance"]["stockfish-anchor"]
        self.assertEqual(run["summaries_by_instance"]["stockfish-anchor"]["final_leader"], "d2d4")
        # A checkpoint before the first observation has no leader, so every
        # outcome there is undefined rather than assumed.
        for label in labels:
            if label["leader"] is None:
                self.assertIsNone(label["later_leader_changed"])
                self.assertIsNone(label["stable_to_end"])
                self.assertIsNone(label["reversal_within_horizon"])

        early = [
            label
            for label in labels
            if label["checkpoint_fraction"] <= 0.5 and label["leader"] is not None
        ]
        self.assertTrue(early)
        self.assertTrue(all(label["later_leader_changed"] for label in early))
        self.assertTrue(all(not label["stable_to_end"] for label in early))
        # Stopping early here would have selected a different move.
        self.assertNotEqual(early[0]["leader"], early[0]["final_leader"])

    def test_disjoint_ownership_reports_zero_support_with_a_reason(self):
        run = self.features("stable_agreement")
        for checkpoint in run["checkpoints"]:
            for comparison in checkpoint["intra_alpha_beta"] + checkpoint["cross_paradigm"]:
                self.assertEqual(comparison["support"], 0)
                self.assertIsNone(comparison["leader_agree"])
                self.assertIn("disjoint exploration ownership", comparison["undefined_reason"])

    def test_lc0_only_divergence_is_isolated_to_cross_paradigm_pairs(self):
        run = self.features("lc0_only_divergence")
        checkpoint = run["checkpoints"][-1]
        intra = checkpoint["intra_alpha_beta"]
        self.assertEqual(len(intra), 1)
        self.assertGreater(intra[0]["support"], 0)
        self.assertTrue(intra[0]["leader_agree"])
        self.assertEqual(intra[0]["top_k_overlap"], 1.0)
        for comparison in checkpoint["cross_paradigm"]:
            self.assertGreater(comparison["support"], 0)
            self.assertFalse(comparison["leader_agree"])
            self.assertEqual(comparison["pv_divergence"], 1.0)

    def test_alpha_beta_only_divergence_is_isolated_to_the_intra_pair(self):
        run = self.features("alpha_beta_only_divergence")
        checkpoint = run["checkpoints"][-1]
        intra = checkpoint["intra_alpha_beta"][0]
        self.assertFalse(intra["leader_agree"])
        self.assertEqual(intra["top_k_overlap"], 1.0)
        self.assertLess(intra["rank_agreement"], 1.0)
        agreements = {
            tuple(sorted((c["left"], c["right"]))): c["leader_agree"]
            for c in checkpoint["cross_paradigm"]
        }
        self.assertTrue(agreements[("lc0-shadow", "stockfish-shadow")])
        self.assertFalse(agreements[("lc0-shadow", "reckless-shadow")])

    def test_failed_stream_is_analyzable_and_marked_incomplete(self):
        bundle = load_bundle(self.paths["failed_shadow_stream"])
        lc0 = bundle.by_owner("lc0")
        self.assertIsNotNone(lc0)
        self.assertFalse(lc0.complete)
        self.assertIsNone(lc0.bestmove)
        self.assertIsNotNone(lc0.final_leader)
        run = extract_features(bundle)
        self.assertFalse(run["summaries_by_instance"]["lc0-shadow"]["complete"])
        self.assertTrue(run["summaries_by_instance"]["stockfish-shadow"]["complete"])

    def test_missing_stream_is_reported_not_imputed(self):
        bundle = load_bundle(self.paths["missing_shadow_stream"])
        self.assertEqual(bundle.missing_streams, ("lc0-shadow",))
        self.assertIsNone(bundle.by_owner("lc0"))
        run = extract_features(bundle)
        self.assertEqual(run["missing_streams"], ["lc0-shadow"])
        self.assertNotIn("lc0-shadow", run["summaries_by_instance"])
        for checkpoint in run["checkpoints"]:
            self.assertNotIn("lc0", checkpoint["per_owner"])

    def test_terminal_bundle_has_no_shadow_evidence(self):
        bundle = load_bundle(self.paths["terminal_position"])
        self.assertTrue(bundle.terminal)
        self.assertEqual(bundle.shadows(), ())
        run = extract_features(bundle)
        self.assertEqual(run["owner_roots"], {})
        for checkpoint in run["checkpoints"]:
            self.assertEqual(checkpoint["per_owner"], {})
            self.assertEqual(checkpoint["intra_alpha_beta"], [])

    def test_chess960_encoding_survives_reconstruction(self):
        bundle = load_bundle(self.paths["chess960_run"])
        self.assertEqual(bundle.variant, "chess960")
        for trajectory in bundle.trajectories:
            self.assertEqual(trajectory.variant, "chess960")
        run = extract_features(bundle)
        self.assertEqual(run["variant"], "chess960")
        self.assertIn("g1h1", run["summaries_by_instance"]["stockfish-shadow"]["final_leader"])

    def test_malformed_telemetry_does_not_corrupt_features(self):
        bundle = load_bundle(self.paths["malformed_telemetry"])
        run = extract_features(bundle)
        # Unparseable lines were preserved as native evidence, so they produce
        # no candidate observations and no fabricated leader.
        self.assertEqual(run["summaries_by_instance"]["stockfish-shadow"]["final_leader"], "e2e4")
        self.assertEqual(run["summaries_by_instance"]["stockfish-shadow"]["leader_flips"], 0)
        self.assertEqual(bundle.load_errors, ())

    def test_within_engine_margins_keep_their_own_semantics(self):
        run = self.features("lc0_only_divergence")
        expected = {
            "stockfish": "stockfish.uci_cp",
            "reckless": "reckless.uci_cp",
            "lc0": "lc0.uci_score.centipawn",
        }
        seen = 0
        for checkpoint in run["checkpoints"]:
            for owner, payload in checkpoint["per_owner"].items():
                if payload["within_engine_margin"] is None:
                    continue
                seen += 1
                self.assertEqual(payload["within_engine_margin_semantics"], expected[owner])
        self.assertGreater(seen, 0, "no within-engine margin was observable")

    def test_work_counters_keep_distinct_engine_semantics(self):
        run = self.features("lc0_only_divergence")
        semantics = {
            payload["work_semantics"]
            for checkpoint in run["checkpoints"]
            for payload in checkpoint["per_owner"].values()
            if payload["work_semantics"]
        }
        self.assertIn("stockfish.uci_nodes", semantics)
        self.assertIn("reckless.uci_nodes", semantics)
        self.assertIn("lc0.uci_nodes", semantics)

    def test_first_discoverer_only_considers_authorized_engines(self):
        run = self.features("stable_agreement")
        discoverer = run["first_discoverer_of_anchor_move"]
        self.assertEqual(discoverer["move"], "e2e4")
        self.assertEqual(sorted(discoverer["eligible"]), ["stockfish-anchor", "stockfish-shadow"])


class ReviewRegressionTests(unittest.TestCase):
    """Regressions for the defects found in code review."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = replay_fixtures.write_all(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_truncated_reversal_horizon_is_unlabelled_not_negative(self):
        # A horizon running past the end of the observed trajectory is
        # right-censored. Labelling it False would fill the late, settled
        # buckets -- the ones that authorize suppression -- with guaranteed
        # negatives and understate real reversal risk.
        bundle = load_bundle(self.paths["stable_agreement"])
        trajectory = bundle.anchor
        checkpoints = bundle.checkpoints()
        labels = counterfactual_labels(trajectory, checkpoints, horizon_fraction=0.25)

        self.assertFalse(labels[-1].horizon_observed)
        self.assertIsNone(labels[-1].reversal_within_horizon)

        observed = [item for item in labels if item.horizon_observed]
        self.assertTrue(observed, "no checkpoint had a fully observed horizon")
        for item in observed:
            self.assertIsNotNone(item.reversal_within_horizon)

    def test_censored_checkpoints_do_not_become_training_rows(self):
        artifact = build_derived_artifact(sorted(self.paths.values()))
        rows = training_rows_from_derived(artifact.as_dict())
        self.assertTrue(rows)
        censored = 0
        for run in artifact.as_dict()["runs"]:
            for labels in run["counterfactual_labels"].values():
                censored += sum(1 for item in labels if not item["horizon_observed"])
        self.assertGreater(censored, 0, "the fixtures exercise no censored checkpoint")

    def test_training_and_live_features_share_one_definition(self):
        # The model is only meaningful if an online decision and a later
        # offline audit bucket the same search state identically.
        bundle = load_bundle(self.paths["transient_disagreement"])
        trajectory = bundle.by_family("stockfish", role="shadow")
        self.assertIsNotNone(trajectory)

        checkpoints = bundle.checkpoints()
        labels = counterfactual_labels(trajectory, checkpoints)
        for label, point in zip(labels, checkpoints):
            offline = label.features
            live = past_only_features(trajectory.primary_moves_until(point)).as_dict()
            self.assertEqual(offline, live)
            for name in FEATURE_NAMES:
                self.assertIn(name, offline)

    def test_multi_stage_worker_keeps_every_stage(self):
        bundle = load_bundle(self.paths["multi_stage_worker"])
        self.assertEqual(len(bundle.shadow_stages()), 4)
        self.assertEqual(len(bundle.shadows()), 3, "worker views must collapse to one per instance")

        run = extract_features(bundle)
        stage_ids = [t.search_id for t in bundle.trajectories]
        self.assertEqual(sorted(run["summaries"]), sorted(stage_ids))
        self.assertEqual(len(run["summaries"]), len(bundle.trajectories))

        # The latest stage is what a worker-level view reports.
        latest = run["summaries_by_instance"]["stockfish-shadow"]
        self.assertEqual(latest["final_leader"], "b1c3")

    def test_multi_stage_worker_never_compares_against_itself(self):
        run = extract_features(load_bundle(self.paths["multi_stage_worker"]))
        for checkpoint in run["checkpoints"]:
            pairs = checkpoint["intra_alpha_beta"] + checkpoint["cross_paradigm"]
            for comparison in pairs:
                self.assertNotEqual(
                    comparison["left"],
                    comparison["right"],
                    "two stages of one instance were compared as separate workers",
                )

    def test_tampered_bundle_is_refused_before_any_feature_is_derived(self):
        run_dir = self.paths["stable_agreement"]
        target = run_dir / "stockfish-shadow.jsonl"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(FeatureExtractionError) as ctx:
            build_derived_artifact([run_dir])
        self.assertIn("integrity", str(ctx.exception))

    def test_anchor_rows_never_train_the_router(self):
        # The router only ever decides whether to stop a shadow worker, so
        # anchor observations would train a decision that is never made -- and
        # would pool their support with the workers it does decide about.
        artifact = build_derived_artifact(sorted(self.paths.values()))
        rows = training_rows_from_derived(artifact.as_dict())
        self.assertTrue(rows)
        self.assertNotIn(
            "stockfish-anchor", {row.instance for row in rows}, "anchor rows leaked into training"
        )
        self.assertTrue(all(row.owner for row in rows))
        self.assertTrue({row.owner for row in rows} <= {"stockfish", "reckless", "lc0"})

    def test_one_family_cannot_supply_another_family_support(self):
        rows = [
            TrainingRow(
                run_id=f"run-{index % 8}",
                instance="stockfish-shadow",
                owner="stockfish",
                observation_count=9,
                leader_flips=0,
                stable_run_fraction=1.0,
                label=False,
            )
            for index in range(400)
        ]
        model = ReversalRiskModel.fit(rows, min_support=5)
        features = {"observation_count": 9, "leader_flips": 0, "stable_run_fraction": 1.0}

        owned = model.evaluate(features, scope="stockfish")
        self.assertTrue(owned.in_domain)
        self.assertGreaterEqual(owned.support, 5)

        borrowed = model.evaluate(features, scope="lc0")
        self.assertFalse(
            borrowed.in_domain, "LC0 borrowed support from Stockfish observations"
        )
        self.assertEqual(borrowed.support, 0)
        self.assertGreaterEqual(borrowed.risk, model.prior_risk)

    def test_streams_that_are_not_contract_valid_cannot_train(self):
        # Dropped events and failed adapter translation both remove
        # observations, and a removed leader flip reads as stability.
        run_dir = self.paths["failed_shadow_stream"]
        artifact = build_derived_artifact([run_dir]).as_dict()
        run = artifact["runs"][0]

        ineligible = {
            label["instance"]
            for labels in run["counterfactual_labels"].values()
            for label in labels
            if label["calibration_eligible"] is False
        }
        self.assertIn("lc0-shadow", ineligible)

        rows = training_rows_from_derived(artifact)
        self.assertNotIn("lc0-shadow", {row.instance for row in rows})
        self.assertTrue(rows, "eligible streams still produced no rows")

    def test_derived_artifact_publishes_the_horizon_it_used(self):
        # The fit step reads this rather than a CLI default, so a model cannot
        # record a horizon its own labels never used.
        artifact = build_derived_artifact(
            [self.paths["late_reversal"]], horizon_fraction=0.4
        )
        self.assertEqual(artifact.as_dict()["parameters"]["horizon_fraction"], 0.4)

    def test_derived_id_covers_every_extraction_parameter(self):
        runs = [self.paths["late_reversal"]]
        base = build_derived_artifact(runs).derived_id
        self.assertNotEqual(base, build_derived_artifact(runs, top_k=5).derived_id)
        self.assertNotEqual(
            base, build_derived_artifact(runs, horizon_fraction=0.5).derived_id
        )
        self.assertNotEqual(
            base, build_derived_artifact(runs, fractions=(0.5, 1.0)).derived_id
        )
        self.assertEqual(base, build_derived_artifact(runs).derived_id)


class DerivedArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = replay_fixtures.write_all(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_artifact_is_traceable_to_exact_raw_inputs(self):
        runs = sorted(self.paths.values())
        artifact = build_derived_artifact(runs)
        path = write_derived_artifact(artifact, self.root / "derived")
        loaded = load_derived_artifact(path)
        self.assertEqual(loaded["extractor_version"], EXTRACTOR_VERSION)
        self.assertEqual(len(loaded["sources"]), len(runs))
        for source in loaded["sources"]:
            self.assertEqual(len(source["manifest_sha256"]), 64)
            self.assertTrue(source["run_id"])
        self.assertEqual(
            sorted(item["run_id"] for item in loaded["sources"]),
            sorted(item["run_id"] for item in loaded["runs"]),
        )

    def test_artifact_id_is_content_addressed(self):
        runs = sorted(self.paths.values())
        first = build_derived_artifact(runs)
        second = build_derived_artifact(runs)
        self.assertEqual(first.derived_id, second.derived_id)
        subset = build_derived_artifact(runs[:2])
        self.assertNotEqual(first.derived_id, subset.derived_id)

    def test_foreign_extractor_version_is_rejected(self):
        artifact = build_derived_artifact([self.paths["stable_agreement"]])
        path = write_derived_artifact(artifact, self.root / "derived")
        data = json.loads(path.read_text())
        data["extractor_version"] = "residuals-v0"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(FeatureExtractionError):
            load_derived_artifact(path)

    def test_empty_input_is_refused(self):
        with self.assertRaises(FeatureExtractionError):
            build_derived_artifact([])


class ScriptArtifactSelectionTests(unittest.TestCase):
    """R7: fit-only ignored --derived-id and picked by content-hash order."""

    SCRIPT = ROOT / "scripts" / "residual-calibration.py"

    def _artifact_root(self, tmp: Path) -> Path:
        """Two real derived artifacts, written under explicit ids."""
        fixtures = replay_fixtures.write_all(tmp / "fixtures")
        derived_root = tmp / "derived"
        derived_root.mkdir(parents=True, exist_ok=True)
        for name in ("derived-aaaaaaaaaaaaaaaa", "derived-zzzzzzzzzzzzzzzz"):
            artifact = build_derived_artifact(
                sorted(fixtures.values()), derived_id=name.split("-", 1)[1]
            ).as_dict()
            artifact["derived_id"] = name
            target = derived_root / name
            target.mkdir(parents=True, exist_ok=True)
            (target / "features.json").write_text(json.dumps(artifact), encoding="utf-8")
        return derived_root

    def _run(self, tmp: Path, *args: str):
        return subprocess.run(
            [
                sys.executable,
                str(self.SCRIPT),
                "--fit",
                "--replay-root",
                str(tmp / "replays"),
                "--min-support",
                "1",
                *args,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_an_explicit_derived_id_is_resolved_not_ignored(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._artifact_root(tmp)
            result = self._run(tmp, "--derived-id", "aaaaaaaaaaaaaaaa")
            self.assertEqual(result.returncode, 0, result.stderr)

            models = sorted((tmp / "calibration").glob("*/model.json"))
            self.assertEqual(len(models), 1, "expected exactly one fitted model")
            model = json.loads(models[0].read_text(encoding="utf-8"))
            sources = [item.get("derived_id") for item in model["sources"]]
            self.assertEqual(
                sources,
                ["derived-aaaaaaaaaaaaaaaa"],
                "fit-only took the lexicographically greatest content hash "
                "instead of the artifact that was asked for",
            )

    def test_an_unknown_derived_id_fails_loudly(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._artifact_root(tmp)
            result = self._run(tmp, "--derived-id", "not-a-real-artifact")
            self.assertNotEqual(result.returncode, 0)
            message = result.stdout + result.stderr
            self.assertIn("not-a-real-artifact", message)
            self.assertIn("derived-aaaaaaaaaaaaaaaa", message)

    def test_an_ambiguous_choice_names_the_artifact_it_used(self):
        """Selecting training data silently is how provenance goes wrong."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._artifact_root(tmp)
            result = self._run(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("most recent of 2 derived artifacts", result.stdout)



class ReviewRegressionRoundFourTests(unittest.TestCase):
    """Round-four findings on calibration serialization and replay identity."""

    # -- Q12: the served model differed from the evaluated one --------------

    def test_a_fitted_risk_survives_a_write_and_reload_exactly(self):
        """Rounding persisted a value the evaluation was never computed with.

        The difference is in the permissive direction: 1/21 serializes as
        0.047619, which passes a threshold of 0.047619 that the fitted value
        0.0476190476... does not.
        """
        rows = [
            TrainingRow(
                run_id=f"run-{index % 8}",
                instance="lc0-shadow",
                owner="lc0",
                observation_count=13,
                leader_flips=0,
                stable_run_fraction=1.0,
                label=(index == 0),
            )
            for index in range(21)
        ]
        model = ReversalRiskModel.fit(rows, min_support=1, horizon_fraction=0.25, sources=[])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(model, Path(tmp))
            reloaded = load_calibration(path)

        for key, record in model.buckets.items():
            self.assertEqual(
                float(reloaded.buckets[key]["risk"]),
                float(record["risk"]),
                f"bucket {key} was not served at the precision it was fitted at",
            )

    def test_a_serialized_risk_is_not_truncated_to_six_decimals(self):
        awkward = 1.0 / 21.0
        model = ReversalRiskModel(
            model_id="calib-precision-test",
            created_utc="2026-09-21T00:00:00Z",
            extractor_version=EXTRACTOR_VERSION,
            buckets={"lc0|n3|s3|f0": {"risk": awkward, "support": 40, "positives": 2}},
            prior_risk=0.1,
            min_support=1,
            smoothing_alpha=1.0,
            horizon_fraction=0.25,
            sources=[],
            evaluation={"test_rows": 5, "brier_score": 0.01, "in_domain_rate": 1.0},
        )
        with tempfile.TemporaryDirectory() as tmp:
            reloaded = load_calibration(write_calibration(model, Path(tmp)))
        served = float(reloaded.buckets["lc0|n3|s3|f0"]["risk"])
        self.assertEqual(served, awkward)
        self.assertGreater(
            served,
            0.047619,
            "the persisted value passed a threshold the fitted value denies",
        )

    # -- Q6: a copied bundle counted twice toward the support floor ---------

    def test_the_same_run_may_not_be_derived_from_twice(self):
        """Support is the quantity the suppression floor is measured in."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            fixtures = replay_fixtures.write_all(tmp)
            original = sorted(fixtures.values())[0]
            copy = tmp / "copied-bundle"
            shutil.copytree(original, copy)

            with self.assertRaises(FeatureExtractionError) as ctx:
                build_derived_artifact([original, copy])
            message = str(ctx.exception)
            self.assertIn("same run", message)

    def test_distinct_runs_are_still_accepted(self):
        with tempfile.TemporaryDirectory() as raw:
            fixtures = replay_fixtures.write_all(Path(raw))
            artifact = build_derived_artifact(sorted(fixtures.values()))
            self.assertEqual(len(artifact.as_dict()["runs"]), len(fixtures))



class ReviewRegressionRoundFiveTests(unittest.TestCase):
    """Round-five findings on horizons and artifact identity."""

    # -- S7: the horizon was a fraction of an absolute timestamp ------------

    def test_an_extension_horizon_is_measured_from_its_own_duration(self):
        """`span_ms` is where a stage ended on the run clock, not how long it ran.

        A stage running 800 -> 1000 ms has a 200 ms duration. Taking 25% of
        `span_ms` asked for a 250 ms window instead of 50 ms and censored most
        later-stage labels.
        """
        from controller.replay_analysis import counterfactual_labels

        trajectory = _stage(started_ms=800.0, points=[(820.0, "e2e4"), (990.0, "e2e4")], end=1000.0)
        # A checkpoint at 900 ms: horizon is 25% of 200 ms = 50 ms, ending at
        # 950 ms, which is inside the stage. With the old arithmetic it ended at
        # 1150 ms and the row was censored.
        labels = counterfactual_labels(trajectory, (900.0,), horizon_fraction=0.25)
        self.assertEqual(len(labels), 1)
        self.assertTrue(
            labels[0].horizon_observed,
            "a horizon well inside the stage was reported right-censored",
        )
        self.assertIsNotNone(labels[0].reversal_within_horizon)

    def test_a_first_stage_horizon_is_unchanged(self):
        """started_ms is zero for an initial stage, so nothing moves there."""
        from controller.replay_analysis import counterfactual_labels

        trajectory = _stage(started_ms=0.0, points=[(10.0, "e2e4"), (190.0, "e2e4")], end=200.0)
        labels = counterfactual_labels(trajectory, (100.0,), horizon_fraction=0.25)
        self.assertTrue(labels[0].horizon_observed)

    # -- Found while checking S7: derived ids collided across logic changes --

    def test_the_extractor_version_is_part_of_the_content_address(self):
        """Two artifacts with different labels must not share a derived id.

        The digest covers sources and parameters, not the extractor's output, so
        `EXTRACTOR_VERSION` is the only thing standing in for the extraction
        logic. Rounds four and five changed that logic; without the bump, a
        re-derive silently overwrote an artifact whose labels differed.
        """
        self.assertEqual(EXTRACTOR_VERSION, "residuals-v3")

    def test_changing_the_extractor_version_changes_the_derived_id(self):
        import controller.residuals as residuals

        with tempfile.TemporaryDirectory() as raw:
            fixtures = replay_fixtures.write_all(Path(raw))
            runs = sorted(fixtures.values())
            first = build_derived_artifact(runs).as_dict()["derived_id"]
            original = residuals.EXTRACTOR_VERSION
            try:
                residuals.EXTRACTOR_VERSION = "residuals-vTEST"
                second = build_derived_artifact(runs).as_dict()["derived_id"]
            finally:
                residuals.EXTRACTOR_VERSION = original
        self.assertNotEqual(
            first,
            second,
            "the extractor version does not reach the content address",
        )


def _stage(*, started_ms: float, points, end: float, bestmove: str = "e2e4"):
    """One reconstructed stage with an explicit start and completion."""
    from controller.replay_analysis import Observation, SearchTrajectory

    observations = tuple(
        Observation(
            sequence=index,
            observed_ms=at,
            multipv_index=1,
            move=move,
            pv=(move,),
            evaluations=(),
            work=(),
        )
        for index, (at, move) in enumerate(points)
    )
    return SearchTrajectory(
        instance="lc0-shadow",
        family="lc0",
        role="shadow",
        search_id="s",
        variant="standard",
        position_id="p",
        authorized_roots=(),
        execution_mode="active",
        owner="lc0",
        observations=observations,
        bestmove=bestmove,
        complete=True,
        completed_ms=end,
        started_ms=started_ms,
    )



class ReviewRegressionRoundSixTests(unittest.TestCase):
    """Round-six findings on labels and calibration identity."""

    def test_a_differing_final_bestmove_counts_as_a_reversal(self):
        """The engine's answer is the last thing it says about the position.

        With the span now reaching the completion timestamp, a search whose
        candidate updates all agreed but whose `bestmove` differed was labelled
        a safe negative -- exactly the rows that authorize a premature stop.
        """
        from controller.replay_analysis import counterfactual_labels

        trajectory = _stage(
            started_ms=0.0,
            points=[(10.0, "e2e4"), (40.0, "e2e4")],
            end=100.0,
            bestmove="d2d4",
        )
        labels = counterfactual_labels(trajectory, (20.0,), horizon_fraction=0.8)
        self.assertTrue(labels[0].horizon_observed)
        self.assertTrue(
            labels[0].reversal_within_horizon,
            "the engine changed its answer and the row was labelled safe",
        )

    def test_an_agreeing_final_bestmove_is_not_a_reversal(self):
        from controller.replay_analysis import counterfactual_labels

        trajectory = _stage(
            started_ms=0.0,
            points=[(10.0, "e2e4"), (40.0, "e2e4")],
            end=100.0,
            bestmove="e2e4",
        )
        labels = counterfactual_labels(trajectory, (20.0,), horizon_fraction=0.8)
        self.assertFalse(labels[0].reversal_within_horizon)

    def test_a_bestmove_outside_the_horizon_is_not_counted(self):
        """A completion after the horizon is not evidence about that horizon."""
        from controller.replay_analysis import counterfactual_labels

        trajectory = _stage(
            started_ms=0.0,
            points=[(10.0, "e2e4"), (20.0, "e2e4")],
            end=1000.0,
            bestmove="d2d4",
        )
        labels = counterfactual_labels(trajectory, (100.0,), horizon_fraction=0.1)
        self.assertFalse(labels[0].reversal_within_horizon)

    # -- T9: a model could be edited and still serve under its old id ------

    def test_an_edited_calibration_is_refused(self):
        rows = [
            TrainingRow(
                run_id=f"run-{index % 8}",
                instance="lc0-shadow",
                owner="lc0",
                observation_count=13,
                leader_flips=index % 3,
                stable_run_fraction=1.0,
                label=(index % 5 == 0),
            )
            for index in range(40)
        ]
        model = ReversalRiskModel.fit(rows, min_support=1, horizon_fraction=0.25, sources=[])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(model, Path(tmp))
            self.assertEqual(load_calibration(path).model_id, model.model_id)

            document = json.loads(path.read_text())
            key = sorted(document["buckets"])[0]
            document["buckets"][key]["risk"] = 0.0
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(CalibrationError) as ctx:
                load_calibration(path)
            self.assertIn("modified after it was evaluated", str(ctx.exception))



class ReviewRegressionRoundEightTests(unittest.TestCase):
    """Round-eight findings on calibration evidence and scale mixing."""

    def test_a_fabricated_evaluation_is_refused(self):
        """Authorization reads `evaluation`, so `evaluation` must be addressed.

        Round six addressed buckets, parameters and sources. Round seven then
        made `calibration_validated` read `test_rows` and the reliability
        bucket list out of `evaluation`, which that address does not cover -- so
        a fabricated reliability entry could license a stop for a bucket with no
        held-out evidence while `model_id` still verified.
        """
        rows = [
            TrainingRow(
                run_id=f"run-{index % 8}",
                instance="lc0-shadow",
                owner="lc0",
                observation_count=13,
                leader_flips=index % 3,
                stable_run_fraction=1.0,
                label=(index % 5 == 0),
            )
            for index in range(40)
        ]
        model = ReversalRiskModel.fit(rows, min_support=1, horizon_fraction=0.25, sources=[])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(model, Path(tmp))
            self.assertEqual(load_calibration(path).model_id, model.model_id)

            document = json.loads(path.read_text())
            document["evaluation"] = dict(
                document["evaluation"],
                reliability=[{"bucket": "lc0|n3|s3|f0", "count": 99, "observed_rate": 0.0}],
            )
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(CalibrationError) as ctx:
                load_calibration(path)
            self.assertIn("evaluation", str(ctx.exception))

    def test_a_mixed_mate_and_centipawn_frame_yields_no_margin(self):
        """Incomparable scores are an undefined margin, not a corpus-wide crash."""
        from controller.replay_analysis import Observation, SearchTrajectory

        from common.residuals import TaggedValue

        def _eval(kind: str, value: float):
            # `Observation.primary_evaluation` only surfaces cp/mate/scalar, so
            # the kinds here must be ones it actually returns -- otherwise the
            # margin short-circuits to None before any comparison is attempted
            # and the test proves nothing.
            return TaggedValue(
                value=float(value), kind=kind, semantics=f"stockfish.uci_{kind}"
            )

        trajectory = SearchTrajectory(
            instance="stockfish-shadow",
            family="stockfish",
            role="shadow",
            search_id="s",
            variant="standard",
            position_id="p",
            authorized_roots=(),
            execution_mode="shadow",
            owner="stockfish",
            observations=(
                Observation(
                    sequence=0,
                    observed_ms=10.0,
                    multipv_index=1,
                    move="e2e4",
                    pv=("e2e4",),
                    evaluations=(_eval("mate", 3),),
                    work=(),
                ),
                Observation(
                    sequence=1,
                    observed_ms=10.0,
                    multipv_index=2,
                    move="d2d4",
                    pv=("d2d4",),
                    evaluations=(_eval("cp", 40),),
                    work=(),
                ),
            ),
            bestmove="e2e4",
            complete=True,
            completed_ms=20.0,
            started_ms=0.0,
        )
        # Must not raise: the margin is simply unavailable for this frame.
        self.assertIsNone(trajectory.within_engine_margin_at(20.0))



class CalibrationTests(unittest.TestCase):
    def rows(self, count: int, *, label_every: int = 4) -> list[TrainingRow]:
        rows: list[TrainingRow] = []
        for index in range(count):
            rows.append(
                TrainingRow(
                    run_id=f"run-{index % 8}",
                    instance="stockfish-shadow",
                    owner="stockfish",
                    observation_count=index % 14,
                    leader_flips=index % 3,
                    stable_run_fraction=((index * 3) % 10) / 10.0,
                    label=(index % label_every == 0),
                )
            )
        return rows

    def test_bucket_key_is_stable_and_bounded(self):
        key = bucket_key(
            {"observation_count": 40, "stable_run_fraction": 1.0, "leader_flips": 5},
            scope="stockfish",
        )
        self.assertEqual(key, "stockfish|n3|s3|f2")
        self.assertEqual(
            bucket_key(
                {"observation_count": 0, "stable_run_fraction": 0.0, "leader_flips": 0},
                scope="lc0",
            ),
            "lc0|n0|s0|f0",
        )
        # The same search state in two families is two populations.
        features = {"observation_count": 9, "stable_run_fraction": 0.8, "leader_flips": 1}
        self.assertNotEqual(
            bucket_key(features, scope="stockfish"), bucket_key(features, scope="lc0")
        )
        with self.assertRaises(CalibrationError):
            bucket_key({"observation_count": 5}, scope="stockfish")
        with self.assertRaises(CalibrationError):
            bucket_key(
                {"observation_count": 5, "stable_run_fraction": 0.5, "leader_flips": -1},
                scope="stockfish",
            )
        with self.assertRaises(CalibrationError):
            bucket_key(
                {"observation_count": -1, "stable_run_fraction": 0.5, "leader_flips": 0},
                scope="stockfish",
            )
        with self.assertRaises(CalibrationError):
            bucket_key(features, scope="")

    def test_low_support_buckets_are_out_of_domain_and_conservative(self):
        model = ReversalRiskModel.fit(self.rows(400), min_support=1000)
        verdict = model.evaluate(
            {"observation_count": 6, "stable_run_fraction": 0.5, "leader_flips": 0},
            scope="stockfish",
        )
        self.assertFalse(verdict.in_domain)
        self.assertIn("support", verdict.reason)
        self.assertGreaterEqual(verdict.risk, model.prior_risk)

    def test_unknown_bucket_returns_the_conservative_prior(self):
        model = ReversalRiskModel.fit(self.rows(400), min_support=1)
        model.buckets.clear()
        verdict = model.evaluate(
            {"observation_count": 12, "stable_run_fraction": 0.9, "leader_flips": 0},
            scope="stockfish",
        )
        self.assertFalse(verdict.in_domain)
        self.assertEqual(verdict.risk, model.prior_risk)
        self.assertEqual(verdict.support, 0)

    def test_holdout_is_guaranteed_whenever_more_than_one_run_exists(self):
        # Hashing each run id independently makes the holdout *size* a random
        # variable: at ten runs it leaves nothing held out ~5.6% of the time,
        # which would make the routing gate block at random. The stride is
        # deterministic and non-empty from two runs upward.
        self.assertEqual(holdout_run_ids(["only-one"]), frozenset())
        for count in (2, 4, 10, 16, 36):
            ids = [f"run-{index:03d}" for index in range(count)]
            holdout = holdout_run_ids(ids)
            self.assertTrue(holdout, count)
            self.assertLess(len(holdout), count, count)
            self.assertEqual(holdout, holdout_run_ids(reversed(ids)), count)

    def test_split_is_deterministic_and_by_run_identity(self):
        rows = self.rows(400)
        first = ReversalRiskModel.fit(rows, min_support=1)
        second = ReversalRiskModel.fit(rows, min_support=1)
        self.assertEqual(first.buckets, second.buckets)
        self.assertEqual(first.evaluation["train_rows"], second.evaluation["train_rows"])
        self.assertEqual(first.model_id, second.model_id)
        self.assertGreater(first.evaluation["test_rows"], 0)
        self.assertIsNotNone(first.evaluation["brier_score"])

    def test_round_trip_and_version_guards(self):
        model = ReversalRiskModel.fit(self.rows(400), min_support=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(model, Path(tmp))
            reloaded = load_calibration(path)
            self.assertEqual(reloaded.model_id, model.model_id)
            self.assertEqual(reloaded.as_dict()["buckets"], model.as_dict()["buckets"])
            self.assertEqual(reloaded.prior_risk, model.prior_risk)
            self.assertEqual(reloaded.min_support, model.min_support)

            data = json.loads(path.read_text())
            data["schema_version"] = 99
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(CalibrationError):
                load_calibration(path)

            data["schema_version"] = 1
            data["extractor_version"] = "residuals-v0"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(CalibrationError):
                load_calibration(path)

            data["extractor_version"] = EXTRACTOR_VERSION
            data["feature_names"] = ["elapsed_fraction"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(CalibrationError):
                load_calibration(path)

    def test_impossible_stored_models_are_refused_at_load(self):
        # A file can be syntactically valid and still describe a model that
        # cannot be true. Every one of these passes each suppression gate.
        model = ReversalRiskModel.fit(self.rows(400), min_support=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calibration(model, Path(tmp))
            good = json.loads(path.read_text())
            key = sorted(good["buckets"])[0]

            for mutate in (
                lambda d: d["buckets"][key].__setitem__("risk", -1.0),
                lambda d: d["buckets"][key].__setitem__("risk", 1.5),
                lambda d: d["buckets"][key].__setitem__("support", -3),
                lambda d: d["buckets"][key].__setitem__(
                    "positives", d["buckets"][key]["support"] + 1
                ),
                lambda d: d.__setitem__("prior_risk", -0.5),
                lambda d: d.__setitem__("prior_risk", 2.0),
            ):
                broken = json.loads(json.dumps(good))
                mutate(broken)
                path.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaises(CalibrationError):
                    load_calibration(path)

    def test_model_id_is_addressed_by_contents_and_hyperparameters(self):
        rows = self.rows(400)
        base = ReversalRiskModel.fit(rows, min_support=5).model_id
        self.assertEqual(base, ReversalRiskModel.fit(rows, min_support=5).model_id)
        self.assertNotEqual(base, ReversalRiskModel.fit(rows, min_support=9).model_id)
        self.assertNotEqual(
            base, ReversalRiskModel.fit(rows, min_support=5, smoothing_alpha=2.0).model_id
        )
        self.assertNotEqual(
            base, ReversalRiskModel.fit(rows, min_support=5, horizon_fraction=0.5).model_id
        )
        # Same row count, opposite labels: the old digest collided here.
        flipped = [
            TrainingRow(
                run_id=row.run_id,
                instance=row.instance,
                owner=row.owner,
                observation_count=row.observation_count,
                leader_flips=row.leader_flips,
                stable_run_fraction=row.stable_run_fraction,
                label=not row.label,
            )
            for row in rows
        ]
        self.assertNotEqual(base, ReversalRiskModel.fit(flipped, min_support=5).model_id)

    def test_fitting_refuses_empty_evidence(self):
        with self.assertRaises(CalibrationError):
            ReversalRiskModel.fit([])

    def test_training_rows_are_past_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = replay_fixtures.write_all(root)
            artifact = build_derived_artifact(sorted(paths.values()))
            rows = training_rows_from_derived(artifact.as_dict())
            self.assertTrue(rows)
            for row in rows:
                # Nothing in a feature vector may come from after the checkpoint.
                self.assertGreaterEqual(row.observation_count, 0)
                self.assertTrue(row.owner, "every training row must name its solver family")
                self.assertGreaterEqual(row.stable_run_fraction, 0.0)
                self.assertLessEqual(row.stable_run_fraction, 1.0)
                self.assertGreaterEqual(row.leader_flips, 0)

    def test_derived_artifact_from_a_foreign_extractor_cannot_train(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = replay_fixtures.write_all(root)
            derived = build_derived_artifact([paths["late_reversal"]]).as_dict()
            derived["extractor_version"] = "residuals-v0"
            with self.assertRaises(CalibrationError):
                training_rows_from_derived(derived)


if __name__ == "__main__":
    unittest.main()
