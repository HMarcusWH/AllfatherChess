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


if __name__ == "__main__":
    unittest.main()
