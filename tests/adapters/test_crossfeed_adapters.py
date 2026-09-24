#!/usr/bin/env python3
"""M14-E engine-specific cross-feed adapter regressions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.crossfeed import (
    CrossFeedAdapterError,
    CrossFeedAdapterEvidenceError,
    Lc0CrossFeedAdapter,
    RecklessCrossFeedAdapter,
    StockfishCrossFeedAdapter,
    build_adapter_evidence,
    build_adapter_evidence_from_run,
)
from common.search_request import parse_position_command
from controller.crossfeed import (
    CROSSFEED_POLICY,
    CandidateHint,
    CrossFeedCandidate,
    CrossFeedView,
    NativeEvaluation,
    NativeWork,
)
from tests.controller.test_shadow_runtime import ANCHOR, run_shell, write_shadow_config


ROOTS = ("e2e4", "d2d4", "g1f3")
OWNERS = ("stockfish", "reckless", "lc0")


def _evaluation(owner: str, value: int, *, mate: bool = False) -> NativeEvaluation:
    if mate:
        return NativeEvaluation(
            kind="mate",
            semantics=f"{owner}.uci_mate",
            value=value,
            bound="none",
            perspective="unknown",
        )
    if owner == "lc0":
        return NativeEvaluation(
            kind="scalar",
            semantics="lc0.uci_score.Q",
            value=float(value) / 100.0,
            bound="none",
            perspective="unknown",
        )
    return NativeEvaluation(
        kind="cp",
        semantics=f"{owner}.uci_cp",
        value=value,
        bound="none",
        perspective="unknown",
    )


def _verify_hint(owner: str, move: str, rank: int, *, mate: bool = False) -> CandidateHint:
    return CandidateHint(
        move=move,
        observed_move=move,
        source_owner=owner,
        source_instance=f"{owner}-shadow",
        source_family=owner,
        source_phase="VERIFY",
        source_rank=rank,
        pv_prefix=(move, "e7e5"),
        evaluations=(_evaluation(owner, 10 + rank, mate=mate),),
        work=(
            NativeWork(
                value=100 + rank,
                unit="count" if owner == "lc0" else "nodes",
                semantics=f"{owner}.uci_nodes",
            ),
        ),
        search_id=f"run-1:verify:{owner}",
        source_run_id="run-1",
        sequence=rank,
        observed_ms=float(rank * 10),
        stage_disposition="completed",
    )


def _view(*, faults: tuple[str, ...] = ()) -> CrossFeedView:
    candidates = []
    for index, move in enumerate(ROOTS):
        hints = tuple(
            _verify_hint(
                owner,
                move,
                index + 1,
                mate=(owner == "stockfish" and move == "e2e4"),
            )
            for owner in OWNERS
        )
        candidates.append(
            CrossFeedCandidate(
                move=move,
                original_owner=OWNERS[index],
                hints=hints,
            )
        )
    return CrossFeedView(
        run_id="run-1",
        generation=1,
        position_id="pos-1",
        policy=CROSSFEED_POLICY,
        verification_id="run-1:verify-v1",
        verification_disposition="completed",
        verification_complete=True,
        refinement_id="run-1:refine-v1",
        refinement_disposition="completed",
        candidates=tuple(candidates),
        evidence_faults=faults,
    )


class _FakeStream:
    evidence_lossy = False
    tracked_events_truncated = False

    def __init__(self, events):
        self._events = list(events)

    def tracked_events(self):
        return list(self._events)


class _FakeRefinement:
    def __init__(self):
        stage = SimpleNamespace(
            owner="stockfish",
            instance="stockfish-shadow",
            family="stockfish",
            search_id="run-1:refine:exp-d002-e2e4.e7e5:stockfish",
            child_moves=("g1f3", "f1c4"),
            disposition="completed",
        )
        self._expansion = SimpleNamespace(
            expansion_id="exp-d002-e2e4.e7e5",
            prefix=("e2e4", "e7e5"),
            stages={"stockfish": stage},
        )
        self._stream = _FakeStream(
            [
                {
                    "schema_version": 1,
                    "event_type": "candidate.update",
                    "search_id": stage.search_id,
                    "sequence": 1,
                    "observed_ms": 12.0,
                    "engine": "stockfish",
                    "engine_instance": "stockfish-shadow",
                    "position_id": "pos-1",
                    "candidate": {
                        "multipv_index": 1,
                        "move": "g1f3",
                        "pv": ["g1f3", "b8c6"],
                        "evaluations": [
                            {
                                "kind": "cp",
                                "value": 21,
                                "bound": "none",
                                "perspective": "unknown",
                                "semantics": "stockfish.uci_cp",
                            }
                        ],
                    },
                    "work": [
                        {
                            "value": 150,
                            "unit": "nodes",
                            "semantics": "stockfish.uci_nodes",
                        }
                    ],
                }
            ]
        )

    def expansions(self):
        return (self._expansion,)

    def expansion_stream(self, expansion_id, instance):
        if (
            expansion_id == self._expansion.expansion_id
            and instance == "stockfish-shadow"
        ):
            return self._stream
        return None


class CrossFeedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.position = parse_position_command("position startpos")
        self.evidence = build_adapter_evidence(
            _view(),
            refinement=_FakeRefinement(),
        )
        self.adapters = (
            StockfishCrossFeedAdapter(),
            RecklessCrossFeedAdapter(),
            Lc0CrossFeedAdapter(),
        )

    def test_verify_set_compiles_same_safe_uci_shape_for_all_families(self):
        for adapter in self.adapters:
            operation = adapter.compile_verify_set(
                self.evidence,
                self.position,
                ("e2e4", "g1f3"),
                limit={"nodes": 64},
            )
            self.assertEqual(operation.operation_kind, "VERIFY_SET")
            self.assertEqual(operation.phase, "VERIFY")
            self.assertEqual(operation.position_command, "position startpos")
            self.assertEqual(
                operation.go_command,
                "go nodes 64 searchmoves e2e4 g1f3",
            )
            self.assertEqual(operation.searchmoves, ("e2e4", "g1f3"))
            self.assertEqual(operation.target_family, adapter.family)

    def test_verify_set_cannot_introduce_candidate_or_duplicate(self):
        adapter = StockfishCrossFeedAdapter()
        with self.assertRaises(CrossFeedAdapterError):
            adapter.compile_verify_set(
                self.evidence,
                self.position,
                ("e2e4", "a2a3"),
                limit={"nodes": 64},
            )
        with self.assertRaises(CrossFeedAdapterError):
            adapter.compile_verify_set(
                self.evidence,
                self.position,
                ("e2e4", "e2e4"),
                limit={"nodes": 64},
            )

    def test_recursive_refine_prefix_preserves_exact_descendant_geometry(self):
        for adapter in self.adapters:
            operation = adapter.compile_refine_prefix(
                self.evidence,
                self.position,
                ("e2e4", "e7e5", "g1f3"),
                limit={"nodes": 32},
            )
            self.assertEqual(operation.operation_kind, "REFINE_PREFIX")
            self.assertEqual(operation.phase, "REFINE")
            self.assertEqual(
                operation.position_command,
                "position startpos moves e2e4 e7e5",
            )
            self.assertEqual(
                operation.go_command,
                "go nodes 32 searchmoves g1f3",
            )
            self.assertEqual(operation.searchmoves, ("g1f3",))

    def test_refine_prefix_requires_typed_support(self):
        with self.assertRaises(CrossFeedAdapterError):
            StockfishCrossFeedAdapter().compile_refine_prefix(
                self.evidence,
                self.position,
                ("e2e4", "e7e5", "f1c4"),
                limit={"nodes": 32},
            )

    def test_priority_ranks_never_compare_across_contexts(self):
        hints = StockfishCrossFeedAdapter().priority_hints(self.evidence)
        verify = [hint for hint in hints if hint.source_phase == "VERIFY"]
        recursive = [
            hint
            for hint in hints
            if hint.source_phase == "REFINE"
            and hint.source_prefix == ("e2e4", "e7e5")
        ]
        self.assertEqual(len(verify), 3)
        self.assertEqual(len(recursive), 1)
        self.assertEqual(len({hint.context_key for hint in verify}), 1)
        self.assertNotEqual(verify[0].context_key, recursive[0].context_key)
        self.assertEqual(recursive[0].candidate_universe, ("g1f3", "f1c4"))

    def test_tactical_alarm_is_native_and_categorical(self):
        stockfish = StockfishCrossFeedAdapter().tactical_alarms(self.evidence)
        reckless = RecklessCrossFeedAdapter().tactical_alarms(self.evidence)
        lc0 = Lc0CrossFeedAdapter().tactical_alarms(self.evidence)
        self.assertEqual(len(stockfish), 1)
        self.assertEqual(stockfish[0].kind, "mate")
        self.assertEqual(stockfish[0].semantics, "stockfish.uci_mate")
        self.assertEqual(reckless, ())
        self.assertEqual(lc0, ())

    def test_operation_identity_binds_source_evidence(self):
        adapter = StockfishCrossFeedAdapter()
        first = adapter.compile_verify_set(
            self.evidence,
            self.position,
            ROOTS,
            limit={"nodes": 64},
        )
        second = adapter.compile_verify_set(
            self.evidence,
            self.position,
            ROOTS,
            limit={"nodes": 64},
        )
        self.assertEqual(first, second)
        changed = build_adapter_evidence(
            _view(faults=("unit-test fault",)),
            refinement=_FakeRefinement(),
        )
        self.assertNotEqual(self.evidence.digest, changed.digest)

    def test_faulted_evidence_fails_closed(self):
        evidence = build_adapter_evidence(_view(faults=("lossy source",)))
        with self.assertRaises(CrossFeedAdapterError):
            StockfishCrossFeedAdapter().compile_verify_set(
                evidence,
                self.position,
                ROOTS,
                limit={"nodes": 64},
            )


class CrossFeedAdapterReplayTests(unittest.TestCase):
    def test_sealed_projection_includes_recursive_refine_without_changing_crossfeed_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_shadow_config(
                root,
                verification=True,
                refinement=True,
                crossfeed=True,
                dispatch_nodes=64,
                verification_nodes=32,
                refinement_nodes=32,
                instance_args={
                    ANCHOR: ["--info-lines", "400", "--info-delay-ms", "10"],
                    "stockfish-shadow": ["--leader-schedule", "e2e4"],
                    "reckless-shadow": ["--leader-schedule", "d2d4"],
                    "lc0-shadow": ["--leader-schedule", "g1f3"],
                },
            )
            document = json.loads(config.read_text(encoding="utf-8"))
            document["refinement"]["max_depth"] = 3
            document["refinement"]["max_expansions"] = 1
            config.write_text(json.dumps(document), encoding="utf-8")

            run_shell(config, ["go nodes 64", "await:bestmove "], timeout=45.0)
            run_dir = next(
                path for path in (root / "replays").iterdir() if path.is_dir()
            )

            evidence = build_adapter_evidence_from_run(run_dir)
            recursive = [
                hint
                for hint in evidence.hints
                if hint.source_phase == "REFINE" and hint.source_depth == 2
            ]
            self.assertTrue(recursive)
            self.assertTrue(
                all(hint.candidate_universe_complete for hint in recursive)
            )

            crossfeed = json.loads(
                (run_dir / "crossfeed" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(crossfeed["schema_version"], 1)
            self.assertNotIn("adapter_evidence", crossfeed)
            self.assertNotIn("recursive_expansions", crossfeed["view"])

            refinement = json.loads(
                (run_dir / "refinement" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            expansion = refinement["expansions"][0]
            stream = expansion["streams"][0]["path"]
            target = run_dir / "refinement" / stream
            target.write_text(
                target.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(CrossFeedAdapterEvidenceError):
                build_adapter_evidence_from_run(run_dir)


if __name__ == "__main__":
    unittest.main()
