#!/usr/bin/env python3
"""Decision-change calibration tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision_calibration import (
    DecisionCalibrationError,
    DecisionChangeRow,
    MODEL_KIND,
    bucket_key,
    fit_decision_change_model,
    load_decision_calibration,
    rows_from_dataset,
    split_position_groups,
    write_decision_calibration,
)
from controller.value_of_compute import (
    VerifierFeatures,
    VerifyBudgetPoint,
    build_dataset,
)


OWNERS = ("stockfish", "reckless", "lc0")
CANDIDATES = ("e2e4", "d2d4", "g1f3")


def _point(
    group: str,
    nodes: int,
    *,
    changed: bool,
    replicate: int = 0,
) -> VerifyBudgetPoint:
    if nodes == 64 or not changed:
        disposition = "NO_PROPOSAL_NONUNANIMOUS"
        move = None
        terminals = CANDIDATES
    else:
        disposition = "PROPOSED"
        move = "g1f3"
        terminals = ("g1f3", "g1f3", "g1f3")
    features = tuple(
        VerifierFeatures(
            owner=owner,
            terminal_move=terminal,
            observation_count=8 + index,
            leader_flips=1 if changed else 0,
            stable_run_fraction=0.5 if changed else 0.9,
            pv_persistence=0.75,
            self_retained=True,
            stage_elapsed_ms=10.0,
            native_work_value=float(nodes),
            native_work_semantics=f"{owner}.uci_nodes",
        )
        for index, (owner, terminal) in enumerate(zip(OWNERS, terminals))
    )
    fingerprint = (group.encode("utf-8").hex() * 64)[:64].ljust(64, "0")
    return VerifyBudgetPoint(
        run_id=f"{group}-r{replicate}-n{nodes}",
        position_id=group,
        position_group=group,
        replicate=replicate,
        verify_nodes=nodes,
        upstream_fingerprint=fingerprint,
        candidate_roots=CANDIDATES,
        nominees_by_owner=tuple(zip(OWNERS, CANDIDATES)),
        terminal_by_owner=tuple(zip(OWNERS, terminals)),
        proposal_disposition=disposition,
        proposal_move=move,
        proposal_source_owner="lc0" if move else None,
        proposal_pre_anchor=True,
        verifier_features=features,
        evidence_complete=True,
        source_hashes=(
            ("counterfactual_artifact", "1" * 64),
            ("crossfeed_manifest", "2" * 64),
            ("parent_manifest", "3" * 64),
            ("verification_manifest", "4" * 64),
        ),
    )


def _dataset(groups: int = 10) -> dict:
    points = []
    for index in range(groups):
        group = f"position-{index:02d}"
        changed = index % 2 == 0
        points.extend([
            _point(group, 64, changed=changed),
            _point(group, 128, changed=changed),
        ])
    return build_dataset(points)


class SplitTests(unittest.TestCase):
    def test_ten_groups_split_six_two_two(self):
        split = split_position_groups(f"p{i}" for i in range(10))
        counts = {
            name: sum(1 for value in split.values() if value == name)
            for name in ("train", "calibration", "holdout")
        }
        self.assertEqual(counts, {"train": 6, "calibration": 2, "holdout": 2})

    def test_position_group_never_straddles_partitions(self):
        dataset = _dataset()
        rows = rows_from_dataset(dataset)
        split = split_position_groups(row.position_group for row in rows)
        for group in {row.position_group for row in rows}:
            partitions = {split[row.position_group] for row in rows if row.position_group == group}
            self.assertEqual(len(partitions), 1)


class CalibrationTests(unittest.TestCase):
    def test_model_kind_is_distinct_from_reversal_risk(self):
        dataset = _dataset()
        model = fit_decision_change_model(
            dataset,
            source_sha256="a" * 64,
            min_support=1,
        )
        self.assertEqual(model.model_kind, MODEL_KIND)
        self.assertNotEqual(model.model_kind, "bucketed_reversal_risk_v3")

    def test_unseen_bucket_is_explicitly_out_of_domain(self):
        model = fit_decision_change_model(
            _dataset(),
            source_sha256="a" * 64,
            min_support=1,
        )
        estimate = model.evaluate(
            {
                "transition": "n512->n1024",
                "proposal_disposition": "PROPOSED",
                "min_observation_count": 99,
                "max_leader_flips": 9,
                "min_stable_run_fraction": 0.1,
            }
        )
        self.assertFalse(estimate.in_domain)
        self.assertEqual(estimate.change_probability, 1.0)
        self.assertEqual(estimate.support, 0)

    def test_below_support_floor_fails_closed(self):
        model = fit_decision_change_model(
            _dataset(),
            source_sha256="a" * 64,
            min_support=99,
        )
        row = rows_from_dataset(_dataset())[0]
        estimate = model.evaluate(row.features())
        self.assertFalse(estimate.in_domain)
        self.assertEqual(estimate.change_probability, 1.0)

    def test_evaluation_reports_position_group_partitions(self):
        model = fit_decision_change_model(
            _dataset(),
            source_sha256="a" * 64,
            min_support=1,
        )
        groups = model.evaluation["position_groups"]
        self.assertEqual(len(groups["train"]), 6)
        self.assertEqual(len(groups["calibration"]), 2)
        self.assertEqual(len(groups["holdout"]), 2)
        self.assertEqual(model.evaluation["holdout"]["rows"], 2)

    def test_model_roundtrip_and_tamper_detection(self):
        model = fit_decision_change_model(
            _dataset(),
            source_sha256="a" * 64,
            min_support=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_decision_calibration(model, Path(tmp))
            loaded = load_decision_calibration(path)
            self.assertEqual(loaded.model_id, model.model_id)

            data = json.loads(path.read_text())
            data["evaluation"]["holdout"]["rows"] = 999
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(DecisionCalibrationError):
                load_decision_calibration(path)

    def test_old_reversal_model_cannot_load_as_decision_value_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model_kind": "bucketed_reversal_risk_v3",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(DecisionCalibrationError):
                load_decision_calibration(path)

    def test_bucket_is_auditable_and_transition_scoped(self):
        row = DecisionChangeRow(
            position_group="p",
            replicate=0,
            transition="n64->n128",
            proposal_disposition="PROPOSED",
            min_observation_count=8,
            max_leader_flips=1,
            min_stable_run_fraction=0.5,
            label=True,
            feature_digest="a" * 64,
            label_digest="b" * 64,
        )
        first = bucket_key(row.features())
        second = bucket_key({**row.features(), "transition": "n128->n256"})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
