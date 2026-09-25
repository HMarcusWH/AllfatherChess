#!/usr/bin/env python3
"""M14-G1 staged decision-change calibration regressions."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from controller.decision import DecisionDisposition, DecisionEvaluation
from controller.staged_decision_calibration import (
    fit_staged_decision_change_model,
    load_staged_decision_calibration,
    rows_from_dataset,
    write_staged_decision_calibration,
)
from controller.staged_value_of_compute import (
    StagedVerifyTransition,
    build_dataset,
)
from controller.value_of_compute import VerifierFeatures


OWNERS = ("stockfish", "reckless", "lc0")
CANDIDATES = ("e2e4", "d2d4", "g1f3")
NOMINEES = tuple(zip(OWNERS, CANDIDATES))


def _eval(code: str, move: str | None, owner: str | None, char: str):
    return DecisionEvaluation(
        policy="unanimous_verify_v1",
        disposition=DecisionDisposition(code, f"fixture {code}"),
        move=move,
        source_owner=owner,
        evidence_digest=char * 64,
    )


def _transition(group: str, replicate: int = 0, *, changed: bool = False):
    base = tuple((owner, move) for owner, move in NOMINEES)
    extension = (
        (
            ("stockfish", "e2e4"),
            ("reckless", "e2e4"),
            ("lc0", "e2e4"),
        )
        if changed
        else base
    )
    extension_eval = (
        _eval("PROPOSED", "e2e4", "stockfish", "b")
        if changed
        else _eval("NO_PROPOSAL_NONUNANIMOUS", None, None, "b")
    )
    verifier_features = tuple(
        VerifierFeatures(
            owner=owner,
            terminal_move=move,
            observation_count=8,
            leader_flips=1,
            stable_run_fraction=0.75,
            pv_persistence=0.75,
            self_retained=True,
            stage_elapsed_ms=10.0,
            native_work_value=64.0,
            native_work_semantics=(
                "lc0.uci_nodes" if owner == "lc0" else f"{owner}.uci_nodes"
            ),
        )
        for owner, move in NOMINEES
    )
    return StagedVerifyTransition(
        run_id=f"run-{group}-{replicate}",
        position_id=f"pos-{group}",
        position_group=group,
        replicate=replicate,
        intervention="same_process_staged_verify_v1",
        base_nodes=64,
        extension_nodes=128,
        candidate_roots=CANDIDATES,
        nominees_by_owner=NOMINEES,
        base_terminal_by_owner=base,
        extension_terminal_by_owner=extension,
        base_evaluation=_eval(
            "NO_PROPOSAL_NONUNANIMOUS", None, None, "a"
        ),
        extension_evaluation=extension_eval,
        verifier_features=verifier_features,
        source_hashes=(
            ("parent_manifest", "1" * 64),
            ("staged_verification_manifest", "2" * 64),
            ("verification_manifest", "3" * 64),
        ),
    )


def _source_sha(dataset: dict) -> str:
    return hashlib.sha256(
        json.dumps(dataset, sort_keys=True).encode("utf-8")
    ).hexdigest()


class StagedDecisionCalibrationTests(unittest.TestCase):
    def test_position_groups_never_split_by_replicate(self):
        dataset = build_dataset(
            [
                _transition("same", 0),
                _transition("same", 1, changed=True),
                _transition("other", 0),
            ]
        )
        rows = rows_from_dataset(dataset)
        model = fit_staged_decision_change_model(
            dataset,
            source_sha256=_source_sha(dataset),
            min_support=1,
            min_position_groups=1,
        )
        same_partitions = {
            model.split_by_position[row.position_group]
            for row in rows
            if row.position_group == "same"
        }
        self.assertEqual(len(same_partitions), 1)

    def test_repeats_of_one_position_do_not_create_independent_support(self):
        dataset = build_dataset(
            [_transition("same", replicate=i, changed=bool(i % 2)) for i in range(4)]
        )
        model = fit_staged_decision_change_model(
            dataset,
            source_sha256=_source_sha(dataset),
            min_support=1,
            min_position_groups=2,
        )
        row = rows_from_dataset(dataset)[0]
        estimate = model.evaluate(row.features())
        self.assertFalse(estimate.in_domain)
        self.assertEqual(estimate.position_group_support, 1)

    def test_unseen_bucket_fails_closed(self):
        dataset = build_dataset(
            [_transition(f"p{i}", changed=bool(i % 2)) for i in range(1, 7)]
        )
        model = fit_staged_decision_change_model(
            dataset,
            source_sha256=_source_sha(dataset),
            min_support=1,
            min_position_groups=1,
        )
        row = rows_from_dataset(dataset)[0]
        features = row.features()
        features["base_decision_disposition"] = "PROPOSED"
        estimate = model.evaluate(features)
        self.assertFalse(estimate.in_domain)
        self.assertEqual(estimate.support, 0)
        self.assertIn("unseen", estimate.reason or "")

    def test_model_round_trip(self):
        dataset = build_dataset(
            [_transition(f"p{i}", changed=bool(i % 2)) for i in range(1, 7)]
        )
        model = fit_staged_decision_change_model(
            dataset,
            source_sha256=_source_sha(dataset),
            min_support=1,
            min_position_groups=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_staged_decision_calibration(model, Path(tmp))
            loaded = load_staged_decision_calibration(path)
        self.assertEqual(loaded.model_id, model.model_id)
        self.assertEqual(loaded.buckets, model.buckets)
        self.assertEqual(loaded.split_by_position, model.split_by_position)


if __name__ == "__main__":
    unittest.main()
