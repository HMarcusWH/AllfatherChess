#!/usr/bin/env python3
"""Typed cross-feed evidence regressions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.crossfeed import (
    CROSSFEED_POLICY,
    CrossFeedError,
    build_crossfeed_view,
    build_crossfeed_view_from_run,
    load_crossfeed_manifest,
    verify_crossfeed_integrity,
)
from controller.runtime import RuntimeError, load_runtime_config
from controller.verification import VerificationPlan, VerificationRun
from tests.controller.test_shadow_runtime import (
    ANCHOR,
    run_shell,
    write_shadow_config,
)


OWNERS = ("stockfish", "reckless", "lc0")
INSTANCES = {
    "stockfish": "stockfish-shadow",
    "reckless": "reckless-shadow",
    "lc0": "lc0-shadow",
}
CANDIDATES = ("e2e4", "d2d4", "g1f3")


class _FakeStream:
    def __init__(
        self,
        instance: str,
        events: list[dict],
        *,
        lossy: bool = False,
        truncated: bool = False,
    ) -> None:
        self.instance = instance
        self._events = list(events)
        self.evidence_lossy = lossy
        self.tracked_events_truncated = truncated

    def tracked_events(self) -> list[dict]:
        return list(self._events)


def _candidate_event(
    *,
    owner: str,
    instance: str,
    move: str,
    sequence: int,
    value: int,
    search_id: str,
) -> dict:
    semantics = (
        f"{owner}.uci_score.Q"
        if owner == "lc0"
        else f"{owner}.uci_cp"
    )
    unit = "count" if owner == "lc0" else "nodes"
    return {
        "schema_version": 1,
        "event_type": "candidate.update",
        "search_id": search_id,
        "sequence": sequence,
        "observed_ms": float(sequence * 10),
        "engine": owner,
        "engine_instance": instance,
        "position_id": "pos-1",
        "candidate": {
            "multipv_index": 1,
            "move": move,
            "pv": [move, "e7e5"],
            "evaluations": [
                {
                    "kind": "scalar" if owner == "lc0" else "cp",
                    "value": value,
                    "bound": "none",
                    "perspective": "unknown",
                    "semantics": semantics,
                }
            ],
        },
        "work": [
            {
                "value": 100 + sequence,
                "unit": unit,
                "semantics": f"{owner}.uci_nodes",
            }
        ],
    }


def _verification(*, reverse_events: bool = False) -> VerificationRun:
    plan = VerificationPlan(
        verification_id="run-1:verify-v1",
        generation=1,
        source_run_id="run-1",
        position_id="pos-1",
        owners=OWNERS,
        candidate_roots=CANDIDATES,
        nominees_by_owner=dict(zip(OWNERS, CANDIDATES)),
        participants=dict(INSTANCES),
        dispatch_limit={"nodes": 64},
        nomination_method="owner_bestmove_union_v1",
    )
    run = VerificationRun(plan=plan, run_dir=Path("."))
    for index, owner in enumerate(OWNERS):
        instance = INSTANCES[owner]
        search_id = f"run-1:verify:{instance}:0"
        events = [
            _candidate_event(
                owner=owner,
                instance=instance,
                move=CANDIDATES[index],
                sequence=1,
                value=10 + index,
                search_id=search_id,
            ),
            _candidate_event(
                owner=owner,
                instance=instance,
                move=CANDIDATES[index],
                sequence=2,
                value=20 + index,
                search_id=search_id,
            ),
        ]
        if reverse_events:
            events.reverse()
        run.register_stream(_FakeStream(instance, events))
        stage = run.record_dispatch(
            owner=owner,
            instance=instance,
            family=owner,
            search_id=search_id,
            command="go nodes 64 searchmoves " + " ".join(CANDIDATES),
            dispatched_ms=float(index),
        )
        run.record_completion(
            stage,
            completed_ms=float(index + 1),
            disposition="completed",
            bestmove=CANDIDATES[index],
        )
    run.set_disposition("completed")
    return run


class CrossFeedViewTests(unittest.TestCase):
    def test_view_is_deterministic_and_collapses_repeated_updates(self):
        first = build_crossfeed_view(
            run_id="run-1",
            generation=1,
            position_id="pos-1",
            verification=_verification(reverse_events=False),
        )
        second = build_crossfeed_view(
            run_id="run-1",
            generation=1,
            position_id="pos-1",
            verification=_verification(reverse_events=True),
        )
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertTrue(first.verification_complete)
        self.assertEqual([item.move for item in first.candidates], list(CANDIDATES))
        for candidate in first.candidates:
            self.assertEqual(len(candidate.hints), 1)
            self.assertEqual(candidate.hints[0].sequence, 2)

    def test_native_semantics_are_preserved_without_cross_engine_score(self):
        view = build_crossfeed_view(
            run_id="run-1",
            generation=1,
            position_id="pos-1",
            verification=_verification(),
        )
        payload = view.as_dict()
        blob = json.dumps(payload, sort_keys=True)
        self.assertIn("stockfish.uci_cp", blob)
        self.assertIn("reckless.uci_cp", blob)
        self.assertIn("lc0.uci_score.Q", blob)
        for forbidden in (
            "score_delta",
            "centipawn_delta",
            "cross_engine_margin",
            "combined_score",
            "weighted_score",
            "winner",
            "correct_move",
        ):
            self.assertNotIn(forbidden, blob)

    def test_incomplete_or_lossy_verify_remains_incomplete(self):
        verification = _verification()
        verification.set_disposition("incomplete", "unit-test incomplete")
        stream = verification.stream("lc0-shadow")
        assert stream is not None
        stream.evidence_lossy = True
        view = build_crossfeed_view(
            run_id="run-1",
            generation=1,
            position_id="pos-1",
            verification=verification,
        )
        self.assertFalse(view.verification_complete)
        self.assertTrue(any("lossy" in item for item in view.evidence_faults))

    def test_source_family_owner_mismatch_fails_closed(self):
        verification = _verification()
        stage = verification.stage_for_owner("stockfish")
        assert stage is not None
        stage.family = "lc0"
        with self.assertRaises(CrossFeedError):
            build_crossfeed_view(
                run_id="run-1",
                generation=1,
                position_id="pos-1",
                verification=verification,
            )


class CrossFeedConfigTests(unittest.TestCase):
    def test_crossfeed_requires_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp), crossfeed=True)
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn("crossfeed requires verification.enabled", str(ctx.exception))

    def test_crossfeed_rejects_unknown_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(
                Path(tmp),
                verification=True,
                crossfeed=True,
            )
            doc = json.loads(path.read_text())
            doc["crossfeed"]["policy"] = "vote-the-scores"
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)


class CrossFeedEndToEndTests(unittest.TestCase):
    def test_fake_run_seals_crossfeed_without_new_search_or_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_shadow_config(
                root,
                verification=True,
                refinement=True,
                crossfeed=True,
                dispatch_nodes=80,
                verification_nodes=64,
                refinement_nodes=32,
                instance_args={
                    ANCHOR: ["--info-lines", "40", "--info-delay-ms", "10"],
                },
            )
            lines = run_shell(
                config,
                ["go nodes 64", "await:bestmove "],
                timeout=40.0,
            )
            outward = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(len(outward), 1)

            run_dir = next(path for path in (root / "replays").iterdir() if path.is_dir())
            parent = json.loads((run_dir / "manifest.json").read_text())
            crossfeed = load_crossfeed_manifest(run_dir)

            anchor = next(stage for stage in parent["stages"] if stage["role"] == "anchor")
            self.assertEqual(outward[0], f"bestmove {anchor['bestmove']}")
            self.assertEqual(crossfeed["policy"], CROSSFEED_POLICY)
            self.assertEqual(crossfeed["source"]["run_id"], parent["run_id"])
            self.assertEqual(len(crossfeed["view"]["candidates"]), 3)
            replayed = build_crossfeed_view_from_run(run_dir)
            self.assertEqual(replayed.as_dict(), crossfeed["view"])
            self.assertEqual(verify_crossfeed_integrity(run_dir), [])

            # Cross-feed v1 is composition only. Parent replay may contain the
            # anchor + EXPLORE stages, but no cross-feed search stage.
            self.assertFalse(
                any("crossfeed" in stage["search_id"] for stage in parent["stages"])
            )

    def test_source_tamper_invalidates_crossfeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_shadow_config(
                root,
                verification=True,
                crossfeed=True,
                dispatch_nodes=80,
                verification_nodes=64,
                instance_args={
                    ANCHOR: ["--info-lines", "40", "--info-delay-ms", "10"],
                },
            )
            run_shell(config, ["go nodes 64", "await:bestmove "], timeout=40.0)
            run_dir = next(path for path in (root / "replays").iterdir() if path.is_dir())
            self.assertEqual(verify_crossfeed_integrity(run_dir), [])

            verification = json.loads(
                (run_dir / "verification" / "manifest.json").read_text()
            )
            stream = verification["streams"][0]["path"]
            target = run_dir / "verification" / stream
            target.write_text(target.read_text() + "\n", encoding="utf-8")
            problems = verify_crossfeed_integrity(run_dir)
            self.assertTrue(problems)
            self.assertTrue(
                any("stream hash mismatch" in item or "VERIFY:" in item for item in problems)
            )


if __name__ == "__main__":
    unittest.main()
