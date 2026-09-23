#!/usr/bin/env python3
"""M14-C live hybrid-authority integration tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.final_decision import verify_final_decision_integrity
from tests.controller.test_shadow_runtime import (
    ANCHOR,
    run_shell,
    write_shadow_config,
)


def write_hybrid_config(directory: Path, *, enabled: bool = True) -> Path:
    extra = {
        "budget": {
            "wall_ms": 2500,
            "cpu_ms": 10000,
            "gpu_ms": 0,
            "verification_reserve_fraction": 0.20,
            "controller_overhead_reserve_ms": 300,
        },
        "routing": {
            "policy": "conservative_v1",
            "calibration": None,
            "min_observation_nodes": 1,
            "checkpoint_interval_ms": 25,
            "max_stages_per_owner": 1,
            "extend_nodes": 64,
            "stop_max_reversal_risk": 0.05,
            "stop_min_support": 5,
            "stop_min_stability_fraction": 0.6,
            "stage_cpu_ms_estimate": 100,
            "anchor_cpu_ms_estimate": 1200,
            "stage_gpu_ms_estimate": 0,
            "verify_stage_cpu_ms_estimate": 100,
            "verify_stage_gpu_ms_estimate": 0,
            "refine_stage_cpu_ms_estimate": 100,
            "refine_stage_gpu_ms_estimate": 0,
            "refine_oracle_cpu_ms_estimate": 50,
            "refine_oracle_gpu_ms_estimate": 0,
        },
        "resource_measurement": {
            "enabled": True,
            "provider": "linux-procfs-v1",
            "require_cpu_for_claim": True,
            "require_gpu_for_claim": False,
            "record_memory": True,
        },
    }
    if enabled:
        extra["hybrid_authority"] = {
            "enabled": True,
            "policy": "bounded_preanchor_v0",
            "request_class": "movetime_v0",
        }

    return write_shadow_config(
        directory,
        mode="active",
        roots="e2e4,d2d4,g1f3",
        dispatch_nodes=24,
        verification=True,
        verification_nodes=16,
        crossfeed=True,
        counterfactual=True,
        slow_anchor=False,
        instance_args={
            # EXPLORE produces three distinct owner nominees because each
            # shadow owns one root. VERIFY then gives all three engines the
            # common ordered set and the fake engines converge on e2e4.
            # Keep the anchor on d2d4 long enough for that proposal to freeze.
            ANCHOR: [
                "--leader-schedule",
                "d2d4",
                "--info-lines",
                "40",
                "--info-delay-ms",
                "25",
            ],
        },
        extra=extra,
        tag="m14c-hybrid-test",
    )


def final_artifact(replay_root: Path) -> tuple[Path, dict]:
    runs = sorted(path for path in replay_root.iterdir() if path.is_dir())
    if len(runs) != 1:
        raise AssertionError(f"expected one replay run, found {[p.name for p in runs]}")
    path = runs[0] / "decision" / "final.json"
    return runs[0], json.loads(path.read_text(encoding="utf-8"))


class HybridAuthorityIntegrationTests(unittest.TestCase):
    def test_bounded_movetime_can_transfer_outward_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config = write_hybrid_config(directory)
            lines = run_shell(
                config,
                ["go movetime 1500", "await:bestmove "],
                timeout=30.0,
            )
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(bestmoves, ["bestmove e2e4"])

            run_dir, artifact = final_artifact(directory / "replays")
            decision = artifact["decision"]
            self.assertEqual(decision["authority"], "HYBRID")
            self.assertEqual(decision["anchor_move"], "d2d4")
            self.assertEqual(decision["proposal_move"], "e2e4")
            self.assertEqual(decision["emitted_move"], "e2e4")
            self.assertTrue(decision["authorization"]["authorized"])
            self.assertEqual(verify_final_decision_integrity(run_dir), [])

    def test_unsupported_nodes_request_falls_back_to_exact_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config = write_hybrid_config(directory)
            lines = run_shell(
                config,
                ["go nodes 64", "await:bestmove "],
                timeout=30.0,
            )
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(bestmoves, ["bestmove d2d4"])

            _, artifact = final_artifact(directory / "replays")
            decision = artifact["decision"]
            self.assertEqual(decision["authority"], "ANCHOR_FALLBACK")
            self.assertEqual(decision["emitted_move"], "d2d4")
            self.assertFalse(decision["authorization"]["authorized"])
            self.assertIn(
                "unsupported request class",
                decision["authorization"]["reason"],
            )

    def test_hybrid_disabled_preserves_anchor_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config = write_hybrid_config(directory, enabled=False)
            lines = run_shell(
                config,
                ["go movetime 1500", "await:bestmove "],
                timeout=30.0,
            )
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(bestmoves, ["bestmove d2d4"])


if __name__ == "__main__":
    unittest.main()
