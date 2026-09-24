#!/usr/bin/env python3
"""M14-F regime-support calibration regressions."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.regime_calibration import (
    RegimeCalibrationError,
    build_regime_dataset,
    fit_regime_support_model,
    load_regime_support_model,
    regime_bucket_key,
    rows_from_dataset,
    split_position_groups,
    write_regime_dataset,
    write_regime_support_model,
)
from controller.regimes import (
    RegimeObservation,
    RefinementRegimeFeatures,
    TimingRegimeFeatures,
    VerifierRegimeFeatures,
)


OWNERS = ("stockfish", "reckless", "lc0")


def observation(
    position_id: str,
    *,
    pattern: str = "all_different",
    relock: str = "RELOCK_FAILED",
    mate: tuple[str, ...] = (),
    request_mode: str = "nodes",
) -> RegimeObservation:
    terminals = (
        ("e2e4", "e2e4", "e2e4")
        if pattern == "unanimous"
        else ("e2e4", "d2d4", "g1f3")
    )
    return RegimeObservation(
        run_id=f"run-{position_id}",
        generation=1,
        position_id=position_id,
        candidate_roots=("e2e4", "d2d4", "g1f3"),
        adapter_evidence_digest="a" * 64,
        source_hashes=(
            ("parent_manifest", "1" * 64),
            ("verification_manifest", "2" * 64),
            ("crossfeed_manifest", "3" * 64),
        ),
        verify_terminal_by_owner=tuple(zip(OWNERS, terminals)),
        verify_pattern=pattern,
        relock_status=relock,
        relock_fraction=0.5 if relock == "RELOCK_OBSERVED" else None,
        verifiers=tuple(
            VerifierRegimeFeatures(
                owner=owner,
                terminal_move=move,
                observation_count=6,
                leader_flips=1,
                stable_run_fraction=0.7,
                pv_persistence=0.6,
            )
            for owner, move in zip(OWNERS, terminals)
        ),
        native_mate_alarm_families=mate,
        refinement=RefinementRegimeFeatures(
            present=False,
            run_disposition=None,
            max_depth=None,
            max_expansions=None,
            target_count=0,
            completed_nonterminal_targets=0,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=(),
        ),
        timing=TimingRegimeFeatures(
            request_mode=request_mode,
            limits=(("nodes", 64),) if request_mode == "nodes" else (("movetime", 100),),
        ),
    )


class RegimeCalibrationTests(unittest.TestCase):
    def test_same_position_group_never_crosses_partitions(self):
        split = split_position_groups(
            ["p1", "p1", "p2", "p3", "p4", "p5", "p6"]
        )
        self.assertEqual(set(split), {"p1", "p2", "p3", "p4", "p5", "p6"})
        self.assertIn(split["p1"], {"train", "calibration", "holdout"})

    def test_dataset_binds_observation_digest_and_bucket(self):
        obs = observation("p1")
        dataset = build_regime_dataset([obs])
        rows = rows_from_dataset(dataset)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].observation.digest, obs.digest)
        self.assertEqual(rows[0].bucket, regime_bucket_key(obs))

        tampered = json.loads(json.dumps(dataset))
        tampered["rows"][0]["bucket"] = "forged"
        with self.assertRaises(RegimeCalibrationError):
            rows_from_dataset(tampered)

    def test_support_model_fails_closed_for_unseen_bucket(self):
        observations = [observation(f"p{i}") for i in range(1, 7)]
        dataset = build_regime_dataset(observations)
        source_sha = hashlib.sha256(
            json.dumps(dataset, sort_keys=True).encode("utf-8")
        ).hexdigest()
        model = fit_regime_support_model(
            dataset,
            source_sha256=source_sha,
            min_support=1,
            min_position_groups=1,
        )

        unseen = observation(
            "p-new",
            pattern="unanimous",
            relock="RELOCK_OBSERVED",
            mate=("stockfish",),
            request_mode="movetime",
        )
        result = model.evaluate(unseen)
        self.assertFalse(result.in_domain)
        self.assertEqual(result.support, 0)
        self.assertIn("unseen", result.reason or "")

    def test_support_requires_independent_position_groups(self):
        base = observation("same")
        repeats = []
        for index in range(4):
            payload = base.as_dict()
            payload["run_id"] = f"run-repeat-{index}"
            repeats.append(RegimeObservation.from_dict(payload))
        dataset = build_regime_dataset(
            repeats,
            position_groups={item.run_id: "same-position" for item in repeats},
        )
        source_sha = hashlib.sha256(
            json.dumps(dataset, sort_keys=True).encode("utf-8")
        ).hexdigest()
        model = fit_regime_support_model(
            dataset,
            source_sha256=source_sha,
            min_support=1,
            min_position_groups=2,
        )
        result = model.evaluate(base)
        self.assertFalse(result.in_domain)
        self.assertEqual(result.position_group_support, 1)

    def test_model_round_trip_and_content_identity(self):
        observations = [observation(f"p{i}") for i in range(1, 7)]
        dataset = build_regime_dataset(observations)
        source_sha = hashlib.sha256(
            json.dumps(dataset, sort_keys=True).encode("utf-8")
        ).hexdigest()
        model = fit_regime_support_model(
            dataset,
            source_sha256=source_sha,
            min_support=1,
            min_position_groups=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_path = write_regime_dataset(dataset, root / "datasets")
            self.assertTrue(dataset_path.is_file())
            model_path = write_regime_support_model(model, root / "models")
            loaded = load_regime_support_model(model_path)
            self.assertEqual(loaded.model_id, model.model_id)
            self.assertEqual(loaded.buckets, model.buckets)

    def test_calibration_feature_schema_has_no_decision_or_numeric_engine_score(self):
        key = regime_bucket_key(observation("p1", mate=("stockfish",)))
        lowered = key.lower()
        for forbidden in (
            "counterfactual",
            "decision",
            "anchor",
            "centipawn",
            "uci_score.q",
            "elo",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
