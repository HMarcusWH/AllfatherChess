#!/usr/bin/env python3
"""Replay bundle tests: completeness, traceability, integrity, honest failure."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.replay import (
    REPLAY_SCHEMA_VERSION,
    ReplayError,
    TelemetryStreamWriter,
    discover_replay_bundles,
    load_manifest,
    sha256_file,
    verify_bundle_integrity,
)
from tests.controller.test_shadow_runtime import run_shell, write_shadow_config


def _load_validator():
    path = ROOT / "scripts" / "validate-telemetry-contract.py"
    spec = importlib.util.spec_from_file_location("telemetry_contract_validator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()

REQUIRED_MANIFEST_KEYS = (
    "schema_version",
    "run_id",
    "generation",
    "created_utc",
    "controller",
    "position",
    "external_request",
    "engines",
    "legal_root_oracle",
    "ledger",
    "stages",
    "streams",
    "shadow_health",
    "disposition",
    "notes",
)

#: Derived and policy material that must never appear inside raw replay
#: evidence. Residuals belong to the derived layer; route decisions to route.json.
FORBIDDEN_MANIFEST_SUBSTRINGS = (
    "residual",
    "top_k_overlap",
    "rank_correlation",
    "pv_overlap",
    "routing_decision",
    "route_proposal",
    "leader_move",
    "runner_up",
    "reversal_risk",
    "calibration",
)


def single_run(tmp: Path, **kwargs) -> tuple[Path, dict]:
    config = write_shadow_config(tmp, **kwargs)
    run_shell(config, ["go nodes 64", "await:bestmove "])
    runs = sorted(p for p in (tmp / "replays").iterdir() if p.is_dir())
    assert len(runs) == 1, runs
    return runs[0], load_manifest(runs[0])


class ReplayManifestTests(unittest.TestCase):
    def test_manifest_is_complete_serializable_and_hashable(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))

            for key in REQUIRED_MANIFEST_KEYS:
                self.assertIn(key, manifest)
            self.assertEqual(manifest["schema_version"], REPLAY_SCHEMA_VERSION)

            # Round-trips through JSON without loss.
            self.assertEqual(json.loads(json.dumps(manifest, sort_keys=True)), manifest)

            # Every declared stream exists and hashes as recorded.
            self.assertEqual(verify_bundle_integrity(run_dir), [])
            for record in manifest["streams"]:
                path = run_dir / record["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256_file(path), record["sha256"])
                self.assertGreater(record["event_count"], 0)

    def test_manifest_reconstructs_the_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest = single_run(Path(tmp))

            position = manifest["position"]
            self.assertEqual(position["variant"], "standard")
            self.assertEqual(position["move_encoding"], "uci")
            self.assertTrue(position["base_fen"])
            self.assertEqual(position["command"], "position startpos")
            self.assertTrue(position["position_id"].startswith("pos-"))

            self.assertEqual(manifest["external_request"]["command"], "go nodes 64")
            self.assertEqual(manifest["controller"]["partition_method"], "root_index_modulo")
            self.assertTrue(manifest["controller"]["config_sha256"])

            # Process-role identity and solver-family identity are both present
            # and are never collapsed into one another.
            engines = manifest["engines"]
            self.assertEqual(
                sorted(engines),
                ["lc0-shadow", "reckless-shadow", "stockfish-anchor", "stockfish-shadow"],
            )
            self.assertEqual(engines["stockfish-anchor"]["engine"], "stockfish")
            self.assertEqual(engines["stockfish-shadow"]["engine"], "stockfish")
            self.assertEqual(engines["stockfish-anchor"]["role"], "anchor")
            self.assertEqual(engines["stockfish-shadow"]["role"], "shadow")
            for identity in engines.values():
                self.assertTrue(identity["binary"])
                self.assertIn("options", identity)

            # Ledger evidence brackets the dispatch.
            self.assertIsNotNone(manifest["ledger"]["pre_dispatch_snapshot"])
            self.assertIsNotNone(manifest["ledger"]["post_run_snapshot"])
            pre = manifest["ledger"]["pre_dispatch_snapshot"]
            post = manifest["ledger"]["post_run_snapshot"]
            self.assertEqual(pre["candidate_roots"], post["candidate_roots"])
            self.assertTrue(all(shard["state"] == "active" for shard in pre["shards"]))
            self.assertTrue(all(shard["state"] == "sealed" for shard in post["shards"]))

            # Ordering evidence is explicit.
            orders = [stage["dispatch_order"] for stage in manifest["stages"]]
            self.assertEqual(orders, sorted(orders))
            self.assertEqual(len(orders), len(set(orders)))
            completions = [
                stage["completion_order"]
                for stage in manifest["stages"]
                if stage["completion_order"] is not None
            ]
            self.assertEqual(len(completions), len(set(completions)))

    def test_manifest_is_detached_from_live_controller_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))
            manifest["ledger"]["owner_roots"]["stockfish"].append("h2h4")
            reloaded = load_manifest(run_dir)
            self.assertNotIn("h2h4", reloaded["ledger"]["owner_roots"]["stockfish"])

    def test_raw_manifest_contains_no_derived_or_policy_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest = single_run(Path(tmp))
            blob = json.dumps(manifest).lower()
            for needle in FORBIDDEN_MANIFEST_SUBSTRINGS:
                self.assertNotIn(needle, blob, f"raw replay evidence leaked {needle!r}")

    def test_unsupported_manifest_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))
            manifest["schema_version"] = 99
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ReplayError):
                load_manifest(run_dir)

    def test_tampered_stream_fails_integrity_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))
            target = run_dir / manifest["streams"][0]["path"]
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            problems = verify_bundle_integrity(run_dir)
            self.assertTrue(problems)
            self.assertTrue(any("hash mismatch" in problem for problem in problems))


class ReplayDiscoveryTests(unittest.TestCase):
    def test_manifestless_directory_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = single_run(root / "valid")
            replay_root = run_dir.parent
            orphan = replay_root / "orphan-run"
            orphan.mkdir()

            discovery = discover_replay_bundles(replay_root)

            self.assertEqual(discovery.bundles, (run_dir,))
            self.assertEqual(len(discovery.skipped), 1)
            self.assertEqual(discovery.skipped[0].path, orphan)
            self.assertEqual(discovery.skipped[0].reason, "missing manifest.json")

    def test_invalid_manifest_is_reported_without_poisoning_valid_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = single_run(root / "valid")
            replay_root = run_dir.parent
            broken = replay_root / "broken-run"
            broken.mkdir()
            (broken / "manifest.json").write_text("{not json", encoding="utf-8")

            discovery = discover_replay_bundles(replay_root)

            self.assertEqual(discovery.bundles, (run_dir,))
            self.assertEqual(len(discovery.skipped), 1)
            self.assertEqual(discovery.skipped[0].path, broken)
            self.assertIn("invalid manifest", discovery.skipped[0].reason)

    def test_missing_replay_root_is_an_empty_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            discovery = discover_replay_bundles(Path(tmp) / "does-not-exist")
            self.assertEqual(discovery.bundles, ())
            self.assertEqual(discovery.skipped, ())


class ReviewRegressionRoundFourTests(unittest.TestCase):
    """Round-four findings on telemetry capture and trajectory reconstruction."""

    def _writer(self, directory: Path):
        from controller.replay import TelemetryStreamWriter

        return TelemetryStreamWriter(
            path=directory / "stream.jsonl",
            instance="stockfish-shadow",
            family="stockfish",
            role="shadow",
            adapter_factory=lambda search_id: _SlowAdapter(search_id),
            track_events=True,
        )

    # -- Q1: the watermark was bumped after the item was already visible ----

    def test_a_queued_line_is_counted_before_it_is_published(self):
        """`pending_events()` must never read zero while a line is in flight.

        `put_nowait` publishes to the writer thread immediately. Incrementing
        the counter afterwards left a window in which the item was queued -- or
        already applied, making the subtraction negative and clamping to zero --
        while a routing checkpoint read "drained".
        """
        from controller.replay import TelemetryStreamWriter

        counted: list[int] = []

        class _Probe(TelemetryStreamWriter):
            def _enqueue(self, item):  # type: ignore[override]
                super()._enqueue(item)
                counted.append(self._enqueued)

        with tempfile.TemporaryDirectory() as tmp:
            writer = _Probe(
                path=Path(tmp) / "stream.jsonl",
                instance="i",
                family="stockfish",
                role="shadow",
                adapter_factory=lambda search_id: _SlowAdapter(search_id),
                track_events=True,
            )
            try:
                writer.submit("info depth 1", 1.0)
                # Whatever the writer thread has done by now, the item was
                # counted at publication time, not afterwards.
                self.assertEqual(counted, [1])
                self.assertGreaterEqual(writer._applied + writer.pending_events(), 1)
            finally:
                writer.close()

    def test_a_dropped_line_does_not_leave_a_phantom_in_flight_count(self):
        """A rejected enqueue must roll the reservation back, not strand it."""
        from controller.replay import TelemetryStreamWriter

        with tempfile.TemporaryDirectory() as tmp:
            writer = TelemetryStreamWriter(
                path=Path(tmp) / "stream.jsonl",
                instance="i",
                family="stockfish",
                role="shadow",
                adapter_factory=lambda search_id: _SlowAdapter(search_id),
                track_events=True,
            )
            try:
                with writer._lock:
                    before = writer._enqueued
                # Simulate the queue refusing the item.
                writer._queue.put_nowait = _raise_full  # type: ignore[assignment]
                writer.submit("info depth 1", 1.0)
                with writer._lock:
                    self.assertEqual(writer._enqueued, before)
                    self.assertEqual(writer._dropped, 1)
            finally:
                writer._queue.put_nowait = type(writer._queue).put_nowait.__get__(writer._queue)
                writer.close()

    # -- Q2: dropped events and adapter errors were invisible while live ----

    def test_a_dropped_event_marks_the_stream_lossy_for_the_live_gate(self):
        """Once the queue drains, a backlog gate cannot see what was lost."""
        from controller.replay import TelemetryStreamWriter

        with tempfile.TemporaryDirectory() as tmp:
            writer = TelemetryStreamWriter(
                path=Path(tmp) / "stream.jsonl",
                instance="i",
                family="stockfish",
                role="shadow",
                adapter_factory=lambda search_id: _SlowAdapter(search_id),
                track_events=True,
            )
            try:
                self.assertFalse(writer.evidence_lossy)
                with writer._lock:
                    writer._dropped += 1
                self.assertTrue(writer.evidence_lossy)
                self.assertEqual(writer.pending_events(), 0)
                faults = writer.live_evidence_faults()
                self.assertEqual(faults["dropped_events"], 1)
            finally:
                writer.close()

    def test_an_adapter_error_also_marks_the_stream_lossy(self):
        from controller.replay import TelemetryStreamWriter

        with tempfile.TemporaryDirectory() as tmp:
            writer = TelemetryStreamWriter(
                path=Path(tmp) / "stream.jsonl",
                instance="i",
                family="stockfish",
                role="shadow",
                adapter_factory=lambda search_id: _SlowAdapter(search_id),
                track_events=True,
            )
            try:
                with writer._lock:
                    writer._errors.append("TelemetryParseError: bad line")
                self.assertTrue(writer.evidence_lossy)
                self.assertEqual(writer.live_evidence_faults()["adapter_errors"], 1)
            finally:
                writer.close()

    # -- Q8: span_ms discarded the completion timestamp ---------------------

    def test_a_trajectory_span_reaches_its_completion_not_its_last_update(self):
        events = [
            _started("s1", 10.0),
            _update("s1", "e2e4", 20.0),
            _complete("s1", "e2e4", 900.0),
        ]
        _ = None
        trajectories = _rebuild(events)
        self.assertEqual(len(trajectories), 1)
        trajectory = trajectories[0]
        self.assertEqual(trajectory.completed_ms, 900.0)
        self.assertEqual(
            trajectory.span_ms,
            900.0,
            "the span stopped at the last candidate update, shortening every horizon",
        )

    def test_a_completed_search_with_no_update_is_not_zero_length(self):
        events = [_started("s1", 5.0), _complete("s1", "e2e4", 400.0)]
        trajectory = _rebuild(events)[0]
        self.assertEqual(trajectory.observations, ())
        self.assertEqual(trajectory.span_ms, 400.0)

    def test_an_unfinished_search_still_spans_its_last_update(self):
        """No completion event means the last update is all there is."""
        events = [_started("s1", 0.0), _update("s1", "e2e4", 50.0)]
        trajectory = _rebuild(events)[0]
        self.assertIsNone(trajectory.completed_ms)
        self.assertEqual(trajectory.span_ms, 50.0)

    # -- Q7: search.started always claimed the run had just begun -----------

    def test_a_later_stage_records_when_it_actually_started(self):
        events = [
            _started("s1", 0.0),
            _update("s1", "e2e4", 10.0),
            _complete("s1", "e2e4", 20.0),
            _started("s2", 500.0),
            _update("s2", "d2d4", 510.0),
            _complete("s2", "d2d4", 520.0),
        ]
        first, second = _rebuild(events)
        self.assertEqual(first.started_ms, 0.0)
        self.assertEqual(
            second.started_ms,
            500.0,
            "an extension claimed it began at run start, contradicting its own events",
        )

    # -- Q3: the run's final stage was reused for every checkpoint ----------

    def test_stage_selection_follows_the_checkpoint_not_the_end_of_the_run(self):
        from controller.replay_analysis import ReplayBundle

        events = [
            _started("s1", 0.0),
            _update("s1", "e2e4", 10.0),
            _complete("s1", "e2e4", 20.0),
            _started("s2", 500.0),
            _update("s2", "d2d4", 510.0),
            _complete("s2", "d2d4", 520.0),
        ]
        trajectories = _rebuild(events)
        bundle = ReplayBundle(
            run_dir=Path("."),
            manifest={"streams": []},
            trajectories=tuple(trajectories),
        )

        early = bundle.shadows_at(100.0)
        self.assertEqual([item.search_id for item in early], ["s1"])
        self.assertEqual(
            early[0].leader_at(100.0),
            "e2e4",
            "the not-yet-started later stage replaced a stage that had reported a leader",
        )

        late = bundle.shadows_at(600.0)
        self.assertEqual([item.search_id for item in late], ["s2"])
        self.assertEqual(
            [item.search_id for item in bundle.shadows()],
            ["s2"],
            "the whole-run view must still collapse to one stage per instance",
        )


def _rebuild(events):
    from controller.replay_analysis import reconstruct_stream

    return reconstruct_stream(
        events, instance="stockfish-shadow", family="stockfish", role="shadow"
    )


def _raise_full(*_args, **_kwargs):
    import queue as _queue

    raise _queue.Full()


def _started(search_id: str, observed_ms: float) -> dict:
    return {
        "event_type": "search.started",
        "search_id": search_id,
        "position_id": "p",
        "variant": "standard",
        "observed_ms": observed_ms,
        "request": {},
        "controller": {"owner": "stockfish", "execution_mode": "shadow"},
        "engine": "stockfish",
        "engine_instance": "stockfish-shadow",
        "role": "shadow",
    }


def _update(search_id: str, move: str, observed_ms: float) -> dict:
    return {
        "event_type": "candidate.update",
        "search_id": search_id,
        "sequence": int(observed_ms),
        "observed_ms": observed_ms,
        "candidate": {"move": move, "multipv_index": 1, "pv": [move]},
    }


def _complete(search_id: str, bestmove: str, observed_ms: float) -> dict:
    return {
        "event_type": "search.complete",
        "search_id": search_id,
        "observed_ms": observed_ms,
        "bestmove": bestmove,
    }


class _SlowAdapter:
    """Minimal adapter stand-in for writer-level tests."""

    def __init__(self, search_id: str):
        self.search_id = search_id
        self.stream = self

    completed = False

    def start(self, *, position, request, observed_ms=0, controller=None):
        return {"event": "search.started", "search_id": self.search_id, "observed_ms": observed_ms}

    def consume(self, line, *, observed_ms):
        return []



class ReplayFailureEvidenceTests(unittest.TestCase):
    def test_failed_shadow_stream_is_recorded_and_not_claimed_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(
                Path(tmp),
                instance_args={"lc0-shadow": ["--exit-on-go-number", "1"]},
            )
            lc0_stage = next(s for s in manifest["stages"] if s["instance"] == "lc0-shadow")
            self.assertEqual(lc0_stage["disposition"], "failed")
            self.assertIn("exited unexpectedly", lc0_stage["failure"])

            lc0_stream = next(s for s in manifest["streams"] if s["instance"] == "lc0-shadow")
            self.assertFalse(lc0_stream["complete"])
            # An incomplete stream is real evidence, but it is not a valid
            # telemetry v1 stream and must not be advertised as one.
            self.assertFalse(lc0_stream["contract_validatable"])

            # The surviving evidence still verifies.
            self.assertEqual(verify_bundle_integrity(run_dir), [])
            self.assertEqual(manifest["disposition"]["run"], "completed")
            self.assertFalse(manifest["shadow_health"]["lc0-shadow"]["alive"])

    def test_terminal_run_records_an_empty_universe_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest = single_run(Path(tmp), roots="")
            self.assertEqual(manifest["disposition"]["run"], "terminal_no_dispatch")
            self.assertEqual(manifest["legal_root_oracle"]["root_count"], 0)
            self.assertEqual(manifest["ledger"]["owner_roots"], {})
            self.assertIsNone(manifest["ledger"]["pre_dispatch_snapshot"])
            self.assertEqual([s for s in manifest["stages"] if s["role"] == "shadow"], [])
            # The authority stream is still captured.
            self.assertEqual(len(manifest["streams"]), 1)
            self.assertEqual(manifest["streams"][0]["instance"], "stockfish-anchor")


class ShadowTelemetryContractTests(unittest.TestCase):
    def test_generated_streams_satisfy_telemetry_v1_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))
            contract = VALIDATOR.load_contract()
            for record in manifest["streams"]:
                self.assertTrue(record["contract_validatable"], record["instance"])
                VALIDATOR.validate_stream(run_dir / record["path"], contract)

    def test_streams_preserve_family_instance_and_native_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))
            expected_semantics = {
                "stockfish-anchor": "stockfish.uci_cp",
                "stockfish-shadow": "stockfish.uci_cp",
                "reckless-shadow": "reckless.uci_cp",
                "lc0-shadow": "lc0.uci_score.centipawn",
            }
            expected_units = {
                "stockfish-anchor": "nodes",
                "stockfish-shadow": "nodes",
                "reckless-shadow": "nodes",
                "lc0-shadow": "count",
            }
            seen_semantics: dict[str, set[str]] = {}
            seen_units: dict[str, set[str]] = {}

            for record in manifest["streams"]:
                instance = record["instance"]
                events = [
                    json.loads(line)
                    for line in (run_dir / record["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertTrue(events)
                started = events[0]
                self.assertEqual(started["event_type"], "search.started")
                self.assertEqual(started["engine_instance"], instance)
                self.assertEqual(started["engine"], manifest["engines"][instance]["engine"])

                controller = started["controller"]
                if instance == "stockfish-anchor":
                    self.assertEqual(controller["execution_mode"], "baseline")
                    self.assertTrue(controller["decision_authority"])
                    self.assertNotIn("root_moves", started["request"])
                else:
                    self.assertEqual(controller["execution_mode"], "shadow")
                    self.assertFalse(controller["decision_authority"])
                    self.assertEqual(
                        started["request"]["root_moves"],
                        manifest["ledger"]["owner_roots"][controller["owner"]],
                    )

                sequences = [event["sequence"] for event in events]
                self.assertEqual(sequences, sorted(sequences))
                self.assertEqual(len(sequences), len(set(sequences)))
                observed = [event["observed_ms"] for event in events]
                self.assertEqual(observed, sorted(observed))

                for event in events:
                    self.assertEqual(event["engine_instance"], instance)
                    self.assertEqual(event["position_id"], started["position_id"])
                    if event["event_type"] == "candidate.update":
                        for evaluation in event["candidate"].get("evaluations", []):
                            seen_semantics.setdefault(instance, set()).add(evaluation["semantics"])
                        for work in event.get("work", []):
                            seen_units.setdefault(instance, set()).add(work["unit"])
                    if "native" in event:
                        self.assertTrue(
                            event["native"]["schema"].startswith(event["engine"] + ".")
                        )

            for instance, semantics in expected_semantics.items():
                self.assertIn(semantics, seen_semantics.get(instance, set()), instance)
            for instance, unit in expected_units.items():
                self.assertIn(unit, seen_units.get(instance, set()), instance)

            # Alpha-beta nodes and LC0 visit counts must not share a unit.
            self.assertNotEqual(
                seen_units["stockfish-shadow"],
                seen_units["lc0-shadow"],
                "LC0 work units were conflated with alpha-beta node units",
            )

    def test_raw_streams_contain_no_derived_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, manifest = single_run(Path(tmp))
            for record in manifest["streams"]:
                blob = (run_dir / record["path"]).read_text(encoding="utf-8").lower()
                for needle in ("residual", "top_k_overlap", "rank_correlation", "routing_decision"):
                    self.assertNotIn(needle, blob)


class ReviewRegressionRoundTenTests(unittest.TestCase):
    """Round-ten finding: shutdown may not depend on room in a bounded queue."""

    def test_closing_does_not_block_on_a_full_queue(self):
        """The advertised close timeout has to bound finalization.

        `close()` enqueued its sentinel with a blocking `put()`. On a full
        queue with the writer stalled in adapter or filesystem work that waited
        without bound, BEFORE either timed `join()` was reached, so the timeout
        bounded nothing and a replay run could stay active forever.
        """
        import threading

        import controller.replay as replay_module

        real_max = replay_module._STREAM_QUEUE_MAXSIZE
        blocked = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            # A small queue so it can actually be filled; the defect is in
            # `close()`, which does not care how large the queue is.
            replay_module._STREAM_QUEUE_MAXSIZE = 4
            try:
                writer = TelemetryStreamWriter(
                    instance="stockfish-shadow",
                    family="stockfish",
                    role="shadow",
                    path=Path(tmp) / "stream.jsonl",
                    adapter_factory=_SlowAdapter,
                )

                def _stall(item):
                    blocked.set()
                    release.wait(timeout=10.0)

                writer._apply = _stall  # type: ignore[assignment]
                writer.begin_stage(
                    search_id="run:stockfish-shadow:0",
                    position={"variant": "standard"},
                    request={"limits": {"nodes": 64}},
                    controller=None,
                )
                self.assertTrue(
                    blocked.wait(timeout=5.0), "the writer never reached the stall"
                )
                for _ in range(32):
                    writer.submit("info depth 1 score cp 10 nodes 1 pv e2e4", 1.0)
                self.assertTrue(writer._queue.full(), "the queue was not filled")

                def _close() -> None:
                    writer.close(timeout=0.2)
                    closed.set()

                closer = threading.Thread(target=_close, daemon=True)
                closer.start()
                finished = closed.wait(timeout=5.0)
            finally:
                release.set()
                replay_module._STREAM_QUEUE_MAXSIZE = real_max

        self.assertTrue(
            finished,
            "close() blocked enqueueing its sentinel instead of honouring its timeout",
        )


if __name__ == "__main__":
    unittest.main()
