#!/usr/bin/env python3
"""Active VERIFY/REFINE specialist budget and integration regressions."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.budget import BudgetExceeded, BudgetLedger, ResourceEnvelope
from controller.routing import ConservativeRouter, RoutingPolicy
from controller.runtime import RuntimeError, load_runtime_config
from tests.controller.test_shadow_runtime import run_shell, write_shadow_config


class _Context:
    run_id = "active-specialist-test"
    run_dir = Path(".")
    external_go_command = "go movetime 1000"
    started_monotonic = 0.0
    owners = ("stockfish", "reckless", "lc0")

    def elapsed_ms(self) -> float:
        return 0.0

    def anchor_threads(self) -> int:
        return 1

    def dispatchable_owners(self) -> tuple[str, ...]:
        # Router-only unit fixtures do not create EXPLORE streams; specialist
        # authorization is the subject under test.
        return ()


def _policy(**overrides) -> RoutingPolicy:
    values = dict(
        min_observation_nodes=100,
        checkpoint_interval_ms=50.0,
        max_stages_per_owner=1,
        extend_nodes=100,
        stop_max_reversal_risk=0.05,
        stop_min_support=5,
        stop_min_stability_fraction=0.5,
        stage_cpu_ms_estimate=100.0,
        anchor_cpu_ms_estimate=500.0,
        stage_gpu_ms_estimate=0.0,
        verify_stage_cpu_ms_estimate=100.0,
        verify_stage_gpu_ms_estimate=0.0,
        refine_stage_cpu_ms_estimate=100.0,
        refine_stage_gpu_ms_estimate=0.0,
        refine_oracle_cpu_ms_estimate=50.0,
        refine_oracle_gpu_ms_estimate=0.0,
    )
    values.update(overrides)
    return RoutingPolicy(**values)


class SpecialistEnvelopeTests(unittest.TestCase):
    def test_solver_verify_and_refine_have_separate_cpu_caps(self):
        envelope = ResourceEnvelope(
            wall_ms=1000,
            cpu_ms=1000,
            verification_reserve_fraction=0.2,
            refinement_reserve_fraction=0.3,
            controller_overhead_reserve_ms=100,
        )
        ledger = BudgetLedger(envelope, clock=lambda: 0.0)
        self.assertEqual(ledger.available_cpu_ms(), 400.0)
        self.assertEqual(ledger.available_cpu_ms(purpose="verify"), 200.0)
        self.assertEqual(ledger.available_cpu_ms(purpose="refine"), 300.0)

        solver = ledger.reserve("solver", cpu_ms=400)
        verify = ledger.reserve("verify:a", cpu_ms=200, purpose="verify")
        refine = ledger.reserve("refine:a", cpu_ms=300, purpose="refine")
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("verify:b", cpu_ms=1, purpose="verify")
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("refine:b", cpu_ms=1, purpose="refine")

        ledger.settle(solver)
        ledger.settle(verify)
        ledger.settle(refine)
        snap = ledger.snapshot()
        self.assertEqual(snap["purpose_totals"]["solver"]["spent_cpu_ms"], 400.0)
        self.assertEqual(snap["purpose_totals"]["verify"]["spent_cpu_ms"], 200.0)
        self.assertEqual(snap["purpose_totals"]["refine"]["spent_cpu_ms"], 300.0)

    def test_specialist_reserves_partition_gpu_too(self):
        envelope = ResourceEnvelope(
            wall_ms=1000,
            cpu_ms=1000,
            gpu_ms=1000,
            verification_reserve_fraction=0.2,
            refinement_reserve_fraction=0.3,
            controller_overhead_reserve_ms=0,
        )
        ledger = BudgetLedger(envelope, clock=lambda: 0.0)
        self.assertEqual(ledger.available_gpu_ms(), 500.0)
        self.assertEqual(ledger.available_gpu_ms(purpose="verify"), 200.0)
        self.assertEqual(ledger.available_gpu_ms(purpose="refine"), 300.0)
        ledger.reserve("verify", cpu_ms=1, gpu_ms=200, purpose="verify")
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("verify2", cpu_ms=1, gpu_ms=1, purpose="verify")

    def test_reserve_fractions_cannot_overbook_the_envelope(self):
        with self.assertRaises(Exception):
            ResourceEnvelope(
                wall_ms=1000,
                cpu_ms=1000,
                verification_reserve_fraction=0.6,
                refinement_reserve_fraction=0.4,
            )


class SpecialistRouterTests(unittest.TestCase):
    def test_verify_and_refine_require_reservations_before_work(self):
        router = ConservativeRouter(
            envelope=ResourceEnvelope(
                wall_ms=2000,
                cpu_ms=5000,
                verification_reserve_fraction=0.2,
                refinement_reserve_fraction=0.3,
                controller_overhead_reserve_ms=100,
            ),
            policy=_policy(),
            verify_enabled=True,
            refine_enabled=True,
            clock=lambda: 0.0,
        )
        context = _Context()
        router.on_run_start(context)

        verify = router.authorize_specialist(
            context, phase="verify", owner="stockfish"
        )
        oracle = router.authorize_specialist(
            context, phase="refine_oracle", target_id="target-000-e2e4"
        )
        refine = router.authorize_specialist(
            context,
            phase="refine",
            owner="reckless",
            target_id="target-000-e2e4",
        )
        self.assertIsNotNone(verify)
        self.assertIsNotNone(oracle)
        self.assertIsNotNone(refine)
        self.assertEqual(router.ledger.snapshot()["open_reservations"], 4)  # + anchor

        router.settle_specialist(verify, actual_wall_ms=20, threads=1)
        router.settle_specialist(oracle, actual_wall_ms=5, threads=1)
        router.release_specialist(refine, reason="unit test did not dispatch")
        router.on_run_end(context)

        snap = router.ledger.snapshot()
        self.assertEqual(snap["open_reservations"], 0)
        self.assertGreater(snap["purpose_totals"]["verify"]["spent_cpu_ms"], 0)
        self.assertGreater(snap["purpose_totals"]["refine"]["spent_cpu_ms"], 0)
        events = router.audit.specialist_actions
        self.assertTrue(any(e["event"] == "authorize" and e["phase"] == "verify" for e in events))
        self.assertTrue(any(e["event"] == "release" for e in events))

    def test_verify_cannot_borrow_refine_capacity(self):
        router = ConservativeRouter(
            envelope=ResourceEnvelope(
                wall_ms=2000,
                cpu_ms=5000,
                verification_reserve_fraction=0.02,
                refinement_reserve_fraction=0.5,
                controller_overhead_reserve_ms=0,
            ),
            policy=_policy(verify_stage_cpu_ms_estimate=101.0),
            verify_enabled=True,
            refine_enabled=True,
            clock=lambda: 0.0,
        )
        context = _Context()
        router.on_run_start(context)
        token = router.authorize_specialist(
            context, phase="verify", owner="stockfish"
        )
        self.assertIsNone(token)
        self.assertTrue(
            any(
                item.get("phase") == "verify" and not item.get("granted")
                for item in router.audit.specialist_actions
            )
        )
        router.on_run_end(context)


class ActiveSpecialistConfigTests(unittest.TestCase):
    def _active_doc(self, directory: Path, *, verify=True, refine=True) -> Path:
        extra = {
            "budget": {
                "wall_ms": 2000,
                "cpu_ms": 5000,
                "gpu_ms": 0,
                "verification_reserve_fraction": 0.2,
                "refinement_reserve_fraction": 0.3,
                "controller_overhead_reserve_ms": 100,
            },
            "routing": {
                "policy": "conservative_v1",
                "calibration": None,
                "min_observation_nodes": 100,
                "checkpoint_interval_ms": 50,
                "max_stages_per_owner": 1,
                "extend_nodes": 100,
                "stop_max_reversal_risk": 0.05,
                "stop_min_support": 5,
                "stop_min_stability_fraction": 0.5,
                "stage_cpu_ms_estimate": 100,
                "anchor_cpu_ms_estimate": 500,
                "stage_gpu_ms_estimate": 0,
                "verify_stage_cpu_ms_estimate": 100,
                "verify_stage_gpu_ms_estimate": 0,
                "refine_stage_cpu_ms_estimate": 100,
                "refine_stage_gpu_ms_estimate": 0,
                "refine_oracle_cpu_ms_estimate": 50,
                "refine_oracle_gpu_ms_estimate": 0,
            },
        }
        return write_shadow_config(
            directory,
            mode="active",
            verification=verify,
            refinement=refine,
            dispatch_nodes=64,
            verification_nodes=32,
            refinement_nodes=32,
            extra=extra,
        )

    def test_active_verify_and_refine_profile_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_runtime_config(self._active_doc(Path(tmp)))
            self.assertEqual(config.mode, "active")
            self.assertIsNotNone(config.verification)
            self.assertIsNotNone(config.refinement)

    def test_active_verify_requires_nonzero_verify_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._active_doc(Path(tmp), refine=False)
            doc = json.loads(path.read_text())
            doc["budget"]["verification_reserve_fraction"] = 0
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

    def test_active_refine_requires_nonzero_refine_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._active_doc(Path(tmp))
            doc = json.loads(path.read_text())
            doc["budget"]["refinement_reserve_fraction"] = 0
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)


class ActiveSpecialistEndToEndTests(unittest.TestCase):
    def test_fake_active_run_accounts_verify_oracle_and_refine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = {
                "stockfish-shadow": ["--leader-schedule", "e2e4"],
                "reckless-shadow": ["--leader-schedule", "d2d4"],
                "lc0-shadow": ["--leader-schedule", "g1f3"],
                "stockfish-anchor": [
                    "--info-lines", "40",
                    "--info-delay-ms", "20",
                    "--leader-schedule", "e2e4",
                ],
            }
            config = write_shadow_config(
                root,
                mode="active",
                dispatch_nodes=64,
                verification=True,
                verification_nodes=32,
                refinement=True,
                refinement_nodes=32,
                instance_args=args,
                extra={
                    "budget": {
                        "wall_ms": 2000,
                        "cpu_ms": 5000,
                        "gpu_ms": 0,
                        "verification_reserve_fraction": 0.2,
                        "refinement_reserve_fraction": 0.3,
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
                        "anchor_cpu_ms_estimate": 500,
                        "stage_gpu_ms_estimate": 0,
                        "verify_stage_cpu_ms_estimate": 100,
                        "verify_stage_gpu_ms_estimate": 0,
                        "refine_stage_cpu_ms_estimate": 100,
                        "refine_stage_gpu_ms_estimate": 0,
                        "refine_oracle_cpu_ms_estimate": 50,
                        "refine_oracle_gpu_ms_estimate": 0,
                    },
                },
            )
            output = run_shell(
                config,
                ["go movetime 1000", "await:bestmove "],
                timeout=30.0,
            )
            self.assertTrue(any(line.startswith("bestmove ") for line in output))

            runs = sorted((root / "replays").glob("*"))
            self.assertEqual(len(runs), 1)
            run = runs[0]
            route = json.loads((run / "route.json").read_text())
            self.assertTrue((run / "verification" / "manifest.json").is_file())
            self.assertTrue((run / "refinement" / "manifest.json").is_file())
            self.assertEqual(route["budget"]["open_reservations"], 0)
            self.assertGreater(
                route["budget"]["purpose_totals"]["verify"]["spent_cpu_ms"], 0
            )
            self.assertGreater(
                route["budget"]["purpose_totals"]["refine"]["spent_cpu_ms"], 0
            )
            phases = {
                row["phase"]
                for row in route["specialist_actions"]
                if row.get("event") == "authorize" and row.get("granted")
            }
            self.assertIn("verify", phases)
            self.assertIn("refine_oracle", phases)
            self.assertIn("refine", phases)
            self.assertTrue(route["envelope_claim"]["claimed"], route["envelope_claim"])


if __name__ == "__main__":
    unittest.main()
