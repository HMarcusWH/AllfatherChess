#!/usr/bin/env python3
"""M14-G1 staged VERIFY runtime and integrity regressions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.runtime import RuntimeError, load_runtime_config
from controller.staged_verification import (
    load_staged_verification_manifest,
    verify_staged_verification_integrity,
)
from tests.controller.test_shadow_runtime import (
    load_shipped_config,
    run_shell,
    write_shadow_config,
)


def _active_extra() -> dict:
    return {
        "budget": {
            "wall_ms": 3000,
            "cpu_ms": 8000,
            "gpu_ms": 0,
            "verification_reserve_fraction": 0.35,
            "controller_overhead_reserve_ms": 100,
        },
        "routing": {
            "policy": "conservative_v1",
            "calibration": None,
            "min_observation_nodes": 16,
            "checkpoint_interval_ms": 50,
            "max_stages_per_owner": 1,
            "extend_nodes": 64,
            "stop_max_reversal_risk": 0.05,
            "stop_min_support": 5,
            "stop_min_stability_fraction": 0.5,
            "stage_cpu_ms_estimate": 100,
            "anchor_cpu_ms_estimate": 1500,
            "stage_gpu_ms_estimate": 0,
            "verify_stage_cpu_ms_estimate": 100,
            "verify_stage_gpu_ms_estimate": 0,
        },
        "resource_measurement": {
            "enabled": True,
            "provider": "linux-procfs-v1",
            "require_cpu_for_claim": True,
            "require_gpu_for_claim": False,
            "record_memory": True,
        },
    }


def _staged_verification_block(base: int = 32, extension: int = 64) -> dict:
    return {
        "enabled": True,
        "nomination_method": "owner_bestmove_union_v1",
        "dispatch_limit": {"nodes": base},
        "staged_extension": {
            "enabled": True,
            "intervention": "same_process_staged_verify_v1",
            "dispatch_limit": {"nodes": extension},
        },
    }


class StagedVerificationConfigTests(unittest.TestCase):
    def test_shipped_profile_loads_with_explicit_intervention(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_shipped_config(
                ROOT / "config" / "allfather.staged-verify.validation.json",
                Path(tmp),
            )
            self.assertEqual(config.mode, "active")
            self.assertIsNotNone(config.verification)
            assert config.verification is not None
            self.assertEqual(config.verification.dispatch_limit, {"nodes": 64})
            self.assertIsNotNone(config.verification.staged_extension)
            assert config.verification.staged_extension is not None
            self.assertEqual(
                config.verification.staged_extension.intervention,
                "same_process_staged_verify_v1",
            )
            self.assertEqual(
                config.verification.staged_extension.dispatch_limit,
                {"nodes": 128},
            )
            self.assertIsNone(config.hybrid_authority)

    def test_extension_budget_must_exceed_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_shadow_config(
                root,
                mode="active",
                verification=True,
                extra=_active_extra(),
            )
            document = json.loads(path.read_text(encoding="utf-8"))
            document["verification"] = _staged_verification_block(64, 64)
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

    def test_staged_verify_and_hybrid_authority_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = json.loads(
                (ROOT / "config" / "allfather.hybrid.validation.json").read_text(
                    encoding="utf-8"
                )
            )
            for spec in document["instances"].values():
                spec["binary"] = sys.executable
                spec.pop("fallback_glob", None)
            document["root"] = "."
            document["verification"]["staged_extension"] = {
                "enabled": True,
                "intervention": "same_process_staged_verify_v1",
                "dispatch_limit": {"nodes": 512},
            }
            path = root / "hybrid-staged.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn("staged VERIFY", str(ctx.exception))


class StagedVerificationEndToEndTests(unittest.TestCase):
    def test_fake_active_run_executes_two_resource_authorized_verify_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extra = _active_extra()
            extra["verification"] = _staged_verification_block(32, 64)
            config = write_shadow_config(
                root,
                mode="active",
                dispatch_nodes=64,
                verification=True,
                verification_nodes=32,
                instance_args={
                    "stockfish-shadow": ["--leader-schedule", "e2e4"],
                    "reckless-shadow": ["--leader-schedule", "d2d4"],
                    "lc0-shadow": ["--leader-schedule", "g1f3"],
                    "stockfish-anchor": [
                        "--info-lines",
                        "120",
                        "--info-delay-ms",
                        "10",
                        "--leader-schedule",
                        "e2e4",
                    ],
                },
                extra=extra,
            )
            output = run_shell(
                config,
                ["go movetime 1500", "await:bestmove "],
                timeout=40.0,
            )
            self.assertTrue(any(line.startswith("bestmove ") for line in output))

            runs = sorted(path for path in (root / "replays").iterdir() if path.is_dir())
            self.assertEqual(len(runs), 1)
            run_dir = runs[0]
            self.assertEqual(verify_staged_verification_integrity(run_dir), [])

            base = json.loads(
                (run_dir / "verification" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            staged = load_staged_verification_manifest(run_dir)
            self.assertEqual(
                staged["nomination"]["candidate_roots"],
                base["nomination"]["candidate_roots"],
            )
            self.assertEqual(staged["participants"], base["participants"])
            self.assertEqual(
                staged["intervention"],
                "same_process_staged_verify_v1",
            )
            self.assertEqual(len(staged["stages"]), 3)
            self.assertTrue(
                all(stage["disposition"] == "completed" for stage in staged["stages"])
            )

            route = json.loads((run_dir / "route.json").read_text(encoding="utf-8"))
            verify_grants = [
                row
                for row in route["specialist_actions"]
                if row.get("event") == "authorize"
                and row.get("phase") == "verify"
                and row.get("granted")
            ]
            self.assertEqual(len(verify_grants), 6)
            extension_grants = [
                row
                for row in verify_grants
                if row.get("target_id") == "staged_extension"
            ]
            self.assertEqual(len(extension_grants), 3)
            self.assertEqual(route["budget"]["open_reservations"], 0)
            self.assertTrue(
                route["envelope_claim"]["specialist_settlement_complete"]
            )

            resource = json.loads(
                (run_dir / "resource.json").read_text(encoding="utf-8")
            )
            phases = {
                row["phase"]
                for row in resource.get("stages", [])
                if isinstance(row, dict)
            }
            self.assertIn("VERIFY", phases)
            self.assertIn("VERIFY_EXTENSION", phases)


if __name__ == "__main__":
    unittest.main()
