#!/usr/bin/env python3
"""Budget and routing tests: the envelope binds and evidence gates authority."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.budget import (
    BudgetError,
    BudgetExceeded,
    BudgetLedger,
    ResourceEnvelope,
)
from controller.calibration import ReversalRiskModel, TrainingRow
from controller.routing import (
    ConservativeRouter,
    OwnerObservation,
    RouteAction,
    RoutingError,
    RoutingPolicy,
    build_router,
    propose,
)
from tests.controller.test_shadow_runtime import (
    load_shipped_config,
    run_shell,
    write_shadow_config,
)


def envelope(**overrides) -> ResourceEnvelope:
    values = {
        "wall_ms": 2000.0,
        "cpu_ms": 4000.0,
        "gpu_ms": 0.0,
        "verification_reserve_fraction": 0.1,
        "controller_overhead_reserve_ms": 100.0,
    }
    values.update(overrides)
    return ResourceEnvelope(**values)


def policy(**overrides) -> RoutingPolicy:
    values = {
        "min_observation_nodes": 100,
        "checkpoint_interval_ms": 50.0,
        "max_stages_per_owner": 3,
        "extend_nodes": 500,
        "stop_max_reversal_risk": 0.05,
        "stop_min_support": 5,
        "stop_min_stability_fraction": 0.5,
        "stage_cpu_ms_estimate": 400.0,
        "anchor_cpu_ms_estimate": 1000.0,
    }
    values.update(overrides)
    return RoutingPolicy(**values)


def observation(**overrides) -> OwnerObservation:
    values = {
        "owner": "stockfish",
        "instance": "stockfish-shadow",
        "active": True,
        "stages_dispatched": 1,
        "leader": "e2e4",
        "leader_flips": 0,
        "observation_count": 8,
        "elapsed_fraction": 0.5,
        "stable_run_fraction": 0.9,
        "work_value": 20000.0,
        "work_semantics": "stockfish.uci_nodes",
    }
    values.update(overrides)
    return OwnerObservation(**values)


def confident_model(risk: float = 0.01, support: int = 500) -> ReversalRiskModel:
    """A model whose every bucket is well supported at the requested risk."""
    rows = [
        TrainingRow(
            run_id=f"run-{index % 16}",
            instance="stockfish-shadow",
            elapsed_fraction=(index % 10) / 10.0,
            stable_run_fraction=((index * 7) % 10) / 10.0,
            leader_flips=index % 3,
            label=False,
        )
        for index in range(support)
    ]
    model = ReversalRiskModel.fit(rows, min_support=1)
    for record in model.buckets.values():
        record["risk"] = risk
        record["support"] = support
    model.min_support = 1
    return model


class EnvelopeTests(unittest.TestCase):
    def test_envelope_rejects_incoherent_declarations(self):
        with self.assertRaises(BudgetError):
            ResourceEnvelope(wall_ms=-1, cpu_ms=10)
        with self.assertRaises(BudgetError):
            ResourceEnvelope(wall_ms=10, cpu_ms=10, verification_reserve_fraction=1.0)
        with self.assertRaises(BudgetError):
            ResourceEnvelope(wall_ms=10, cpu_ms=10, controller_overhead_reserve_ms=100)


class BudgetLedgerTests(unittest.TestCase):
    def test_concurrent_reservations_cannot_exceed_the_envelope(self):
        ledger = BudgetLedger(envelope(cpu_ms=1000.0, verification_reserve_fraction=0.0,
                                       controller_overhead_reserve_ms=0.0))
        granted: list[object] = []
        denied: list[int] = []
        barrier = threading.Barrier(24)

        def worker(index: int) -> None:
            barrier.wait()
            try:
                granted.append(ledger.reserve(f"w{index}", cpu_ms=100.0))
            except BudgetExceeded:
                denied.append(index)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(granted), 10)
        self.assertEqual(len(denied), 14)
        self.assertTrue(ledger.within_envelope())
        self.assertEqual(ledger.snapshot()["committed_cpu_ms"], 1000.0)

    def test_reserves_are_withheld_from_solver_work(self):
        ledger = BudgetLedger(
            envelope(cpu_ms=1000.0, verification_reserve_fraction=0.2,
                     controller_overhead_reserve_ms=100.0)
        )
        self.assertEqual(ledger.available_cpu_ms(), 700.0)
        self.assertEqual(ledger.available_cpu_ms(purpose="verify"), 1000.0)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("solver", cpu_ms=800.0)
        ledger.reserve("verification", cpu_ms=800.0, purpose="verify")

    def test_release_returns_capacity_and_settle_consumes_it(self):
        ledger = BudgetLedger(envelope(cpu_ms=1000.0, verification_reserve_fraction=0.0,
                                       controller_overhead_reserve_ms=0.0))
        first = ledger.reserve("a", cpu_ms=600.0)
        self.assertEqual(ledger.available_cpu_ms(), 400.0)
        ledger.release(first)
        self.assertEqual(ledger.available_cpu_ms(), 1000.0)

        second = ledger.reserve("a", cpu_ms=600.0)
        ledger.settle(second, actual_cpu_ms=120.0)
        self.assertEqual(ledger.available_cpu_ms(), 880.0)
        self.assertEqual(ledger.snapshot()["open_reservations"], 0)
        with self.assertRaises(BudgetError):
            ledger.settle(second)

    def test_controller_overhead_is_charged_to_the_same_envelope(self):
        ledger = BudgetLedger(envelope())
        before = ledger.snapshot()["lanes"].get("controller")
        self.assertIsNone(before)
        with ledger.controller_overhead("unit"):
            sum(range(20000))
        lane = ledger.snapshot()["lanes"]["controller"]
        self.assertGreater(lane["spent_cpu_ms"], 0.0)
        self.assertIn("controller.unit_ms", lane["native_work"])

    def test_native_work_is_never_summed_across_engine_semantics(self):
        ledger = BudgetLedger(envelope())
        ledger.record_native_work("shadow:stockfish", value=1000, semantics="stockfish.uci_nodes")
        ledger.record_native_work("shadow:lc0", value=40, semantics="lc0.uci_nodes")
        totals = ledger.native_work_by_semantics()
        self.assertEqual(totals["stockfish.uci_nodes"], 1000.0)
        self.assertEqual(totals["lc0.uci_nodes"], 40.0)
        self.assertNotIn("nodes", totals)
        with self.assertRaises(BudgetError):
            ledger.record_native_work("shadow:lc0", value=1, semantics="")

    def test_wall_clock_exhaustion_is_reported(self):
        clock = iter([0.0, 0.0, 3.0])
        ledger = BudgetLedger(envelope(wall_ms=2000.0), clock=lambda: next(clock))
        self.assertFalse(ledger.wall_exhausted())
        self.assertTrue(ledger.wall_exhausted())


class ProposalTests(unittest.TestCase):
    def test_stability_nominates_a_stop_but_only_nominates(self):
        proposal = propose(observation(stable_run_fraction=0.9), policy())
        self.assertIs(proposal.action, RouteAction.STOP_WORKER)

    def test_instability_nominates_more_observation(self):
        proposal = propose(observation(stable_run_fraction=0.1), policy())
        self.assertIs(proposal.action, RouteAction.CONTINUE)

    def test_finished_unstable_worker_nominates_buying_compute(self):
        proposal = propose(observation(active=False, stable_run_fraction=0.1), policy())
        self.assertIs(proposal.action, RouteAction.ABSTAIN_BUY_COMPUTE)

    def test_no_observation_never_nominates_a_stop(self):
        self.assertIs(propose(observation(leader=None), policy()).action, RouteAction.CONTINUE)
        self.assertIs(
            propose(observation(active=False, leader=None), policy()).action,
            RouteAction.ABSTAIN_BUY_COMPUTE,
        )

    def test_exhausted_stage_budget_holds(self):
        proposal = propose(observation(active=False, stages_dispatched=3), policy(max_stages_per_owner=3))
        self.assertIs(proposal.action, RouteAction.HOLD)


class AuthorizationTests(unittest.TestCase):
    def router(self, *, calibration=None, **policy_overrides) -> ConservativeRouter:
        # A frozen clock makes "deterministic under fixed evidence" testable:
        # budget readings are evidence, and evidence must be held fixed.
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(**policy_overrides),
            calibration=calibration,
            clock=lambda: 0.0,
        )
        router.on_run_start(_FakeContext())
        return router

    def test_missing_calibration_can_never_authorize_suppression(self):
        router = self.router()
        decision = router._authorize(
            propose(observation(), router.policy), observation(), 100.0
        )
        self.assertFalse(decision.granted)
        self.assertIs(decision.action, RouteAction.CONTINUE)
        self.assertIn("calibration_present", decision.reason)
        gate = next(g for g in decision.gates if g.name == "calibration_present")
        self.assertFalse(gate.passed)

    def test_unvalidated_calibration_cannot_authorize_suppression(self):
        model = confident_model(risk=0.01)
        # A deterministic split that left no held-out rows: the artifact itself
        # says it is not validated out of sample.
        model.evaluation = {
            "train_rows": 500,
            "test_rows": 0,
            "brier_score": None,
            "note": "no held-out rows; this model is not validated out of sample",
        }
        router = self.router(calibration=model)
        decision = router._authorize(propose(observation(), router.policy), observation(), 100.0)
        self.assertFalse(decision.granted)
        self.assertIs(decision.action, RouteAction.CONTINUE)
        self.assertIn("calibration_validated", decision.reason)
        gate = next(g for g in decision.gates if g.name == "calibration_validated")
        self.assertFalse(gate.passed)

    def test_validated_calibration_passes_the_out_of_sample_gate(self):
        router = self.router(calibration=confident_model(risk=0.01))
        subject = observation()
        decision = router._authorize(propose(subject, router.policy), subject, 100.0)
        gate = next(g for g in decision.gates if g.name == "calibration_validated")
        self.assertTrue(gate.passed)
        self.assertGreater(router.calibration.evaluation["test_rows"], 0)

    def test_out_of_domain_calibration_denies_suppression(self):
        model = confident_model()
        model.min_support = 10_000
        router = self.router(calibration=model)
        decision = router._authorize(propose(observation(), router.policy), observation(), 100.0)
        self.assertFalse(decision.granted)
        self.assertIs(decision.action, RouteAction.CONTINUE)
        self.assertIn("calibration_in_domain", decision.reason)

    def test_high_risk_denies_suppression_even_when_in_domain(self):
        router = self.router(calibration=confident_model(risk=0.9))
        decision = router._authorize(propose(observation(), router.policy), observation(), 100.0)
        self.assertFalse(decision.granted)
        self.assertIs(decision.action, RouteAction.CONTINUE)
        self.assertIn("reversal_risk", decision.reason)

    def test_insufficient_observation_denies_suppression(self):
        router = self.router(calibration=confident_model(), min_observation_nodes=10**9)
        decision = router._authorize(propose(observation(), router.policy), observation(), 100.0)
        self.assertFalse(decision.granted)
        self.assertIn("minimum_observation", decision.reason)

    def test_calibrated_low_risk_authorizes_a_stop(self):
        router = self.router(calibration=confident_model(risk=0.01))
        subject = observation()
        decision = router._authorize(propose(subject, router.policy), subject, 100.0)
        self.assertTrue(decision.granted)
        self.assertIs(decision.action, RouteAction.STOP_WORKER)
        self.assertTrue(all(gate.passed for gate in decision.gates))
        self.assertIsNotNone(decision.calibration)
        self.assertEqual(decision.thresholds["stop_max_reversal_risk"], 0.05)

    def test_decisions_are_deterministic_under_fixed_evidence(self):
        router = self.router(calibration=confident_model())
        subject = observation()
        first = router._authorize(propose(subject, router.policy), subject, 100.0).as_dict()
        second = router._authorize(propose(subject, router.policy), subject, 100.0).as_dict()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_exhausted_envelope_denies_buying_more_compute(self):
        router = self.router(stage_cpu_ms_estimate=10**6)
        subject = observation(active=False, stable_run_fraction=0.1)
        decision = router._authorize(propose(subject, router.policy), subject, 100.0)
        self.assertFalse(decision.granted)
        self.assertIs(decision.action, RouteAction.HOLD)
        self.assertIn("envelope", decision.reason)

    def test_initial_dispatch_is_refused_when_the_envelope_is_full(self):
        router = ConservativeRouter(
            envelope=envelope(cpu_ms=1000.0, verification_reserve_fraction=0.0,
                              controller_overhead_reserve_ms=0.0),
            policy=policy(stage_cpu_ms_estimate=400.0, anchor_cpu_ms_estimate=900.0),
        )
        context = _FakeContext()
        router.on_run_start(context)
        self.assertFalse(router.authorize_initial(context, "stockfish"))
        self.assertTrue(router.audit.notes)


class RouterConfigTests(unittest.TestCase):
    def test_active_config_builds_a_fail_closed_router(self):
        path = ROOT / "config" / "allfather.active.validation.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["mode"], "active")
        self.assertIsNone(
            document["routing"]["calibration"],
            "a shipped config must not assume a fitted calibration artifact exists",
        )

        with tempfile.TemporaryDirectory() as tmp:
            config = load_shipped_config(path, Path(tmp))
            router = build_router(config)
        self.assertIsNone(router.calibration, "the shipped config must not assume a fitted model")
        self.assertEqual(router.policy.as_dict()["policy"], "conservative_v1")

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config({"policy": "aggressive_v9"})
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(None)

    def test_declared_but_unloadable_calibration_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_shipped_config(
                ROOT / "config" / "allfather.active.validation.json", Path(tmp)
            )
            broken = dict(config.routing or {})
            broken["calibration"] = "does/not/exist.json"
            replaced = type(config)(
                path=config.path,
                root=config.root,
                mode=config.mode,
                anchor=config.anchor,
                backends=config.backends,
                shadow=config.shadow,
                budget=config.budget,
                routing=broken,
            )
            with self.assertRaises(RoutingError):
                build_router(replaced)


class ActiveModeEndToEndTests(unittest.TestCase):
    def run_active(self, tmp: Path, **routing_overrides) -> tuple[list[str], dict, dict]:
        routing = {
            "policy": "conservative_v1",
            "calibration": None,
            "min_observation_nodes": 100,
            "checkpoint_interval_ms": 40,
            "max_stages_per_owner": 2,
            "extend_nodes": 400,
            "stop_max_reversal_risk": 0.05,
            "stop_min_support": 5,
            "stop_min_stability_fraction": 0.5,
            "stage_cpu_ms_estimate": 400,
            "anchor_cpu_ms_estimate": 800,
        }
        routing.update(routing_overrides)
        config = write_shadow_config(
            tmp,
            mode="active",
            dispatch_nodes=400,
            extra={
                "budget": {
                    "wall_ms": 2500,
                    "cpu_ms": 5000,
                    "gpu_ms": 0,
                    "verification_reserve_fraction": 0.1,
                    "controller_overhead_reserve_ms": 100,
                },
                "routing": routing,
            },
        )
        lines = run_shell(config, ["go movetime 700", "await:bestmove "])
        run_dir = next(p for p in (tmp / "replays").iterdir() if p.is_dir())
        manifest = json.loads((run_dir / "manifest.json").read_text())
        route = json.loads((run_dir / "route.json").read_text())
        return lines, manifest, route

    def test_routing_never_touches_outward_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines, manifest, route = self.run_active(Path(tmp))
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(len(bestmoves), 1)
            anchor = [s for s in manifest["stages"] if s["role"] == "anchor"]
            self.assertEqual(len(anchor), 1)
            self.assertEqual(anchor[0]["dispatched_roots"], [])
            self.assertEqual(bestmoves[0], f"bestmove {anchor[0]['bestmove']}")
            self.assertIn("outward bestmove remained", route["authority"])

    def test_route_evidence_lives_beside_but_outside_the_raw_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest, route = self.run_active(Path(tmp))
            blob = json.dumps(manifest).lower()
            for needle in ("routing_decision", "reversal_risk", "proposal", "calibration"):
                self.assertNotIn(needle, blob)
            self.assertEqual(route["schema_version"], 1)
            self.assertTrue(route["decisions"])
            for decision in route["decisions"]:
                self.assertIn("gates", decision)
                self.assertIn("thresholds", decision)
                self.assertIn("budget", decision)

    def test_missing_calibration_yields_conservative_actions_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, route = self.run_active(Path(tmp))
            self.assertIsNone(route["calibration"])
            actions = {decision["action"] for decision in route["decisions"]}
            self.assertNotIn("stop_worker", actions)
            self.assertTrue(
                actions <= {"continue", "hold", "extend", "abstain_buy_compute"}, actions
            )
            self.assertTrue(any("no calibration is loaded" in note for note in route["notes"]))

    def test_total_budget_is_never_exceeded_and_nothing_stays_reserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, route = self.run_active(Path(tmp))
            budget = route["budget"]
            self.assertTrue(budget["within_envelope"])
            self.assertEqual(budget["open_reservations"], 0)
            self.assertLessEqual(budget["committed_cpu_ms"], budget["envelope"]["cpu_ms"])
            self.assertIn("controller", budget["lanes"])
            self.assertGreater(budget["lanes"]["controller"]["spent_cpu_ms"], 0.0)

    def test_stage_budget_per_owner_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, manifest, _ = self.run_active(Path(tmp), max_stages_per_owner=2)
            counts: dict[str, int] = {}
            for stage in manifest["stages"]:
                if stage["role"] == "shadow":
                    counts[stage["owner"]] = counts.get(stage["owner"], 0) + 1
            self.assertTrue(counts)
            for owner, count in counts.items():
                self.assertLessEqual(count, 2, owner)

    def test_a_disabled_owner_receives_no_further_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = write_shadow_config(
                tmp_path,
                mode="active",
                dispatch_nodes=400,
                instance_args={"lc0-shadow": ["--exit-on-go-number", "1"]},
                extra={
                    "budget": {
                        "wall_ms": 2500,
                        "cpu_ms": 5000,
                        "gpu_ms": 0,
                        "verification_reserve_fraction": 0.1,
                        "controller_overhead_reserve_ms": 100,
                    },
                    "routing": {
                        "policy": "conservative_v1",
                        "calibration": None,
                        "min_observation_nodes": 100,
                        "checkpoint_interval_ms": 40,
                        "max_stages_per_owner": 3,
                        "extend_nodes": 400,
                        "stop_max_reversal_risk": 0.05,
                        "stop_min_support": 5,
                        "stop_min_stability_fraction": 0.5,
                        "stage_cpu_ms_estimate": 400,
                        "anchor_cpu_ms_estimate": 800,
                    },
                },
            )
            lines = run_shell(config, ["go movetime 700", "await:bestmove "])
            self.assertEqual(len([l for l in lines if l.startswith("bestmove ")]), 1)
            run_dir = next(p for p in (tmp_path / "replays").iterdir() if p.is_dir())
            manifest = json.loads((run_dir / "manifest.json").read_text())
            lc0_stages = [s for s in manifest["stages"] if s["instance"] == "lc0-shadow"]
            self.assertEqual(len(lc0_stages), 1)
            self.assertEqual(lc0_stages[0]["disposition"], "failed")
            self.assertFalse(manifest["shadow_health"]["lc0-shadow"]["alive"])


class _FakeContext:
    run_id = "unit-test-run"
    run_dir = Path("/nonexistent")
    owners = ("stockfish", "reckless", "lc0")
    owner_roots: dict[str, tuple[str, ...]] = {}

    def elapsed_ms(self) -> float:
        return 0.0

    def active_owners(self) -> tuple[str, ...]:
        return ()

    def owner_instance(self, owner: str) -> str:
        return f"{owner}-shadow"

    def owner_family(self, owner: str) -> str:
        return owner

    def owner_events(self, owner: str) -> list[dict]:
        return []

    def owner_active(self, owner: str) -> bool:
        return False

    def owner_stages(self, owner: str) -> int:
        return 0

    def owner_last_stage_ms(self, owner: str) -> float | None:
        return None


if __name__ == "__main__":
    unittest.main()
