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
from adapters.telemetry import Lc0TelemetryAdapter
from common.telemetry import STARTPOS_FEN
from controller.calibration import ReversalRiskModel, TrainingRow, bucket_key
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
        "stage_gpu_ms_estimate": 0.0,
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
            owner="stockfish",
            observation_count=index % 14,
            leader_flips=index % 3,
            stable_run_fraction=((index * 7) % 10) / 10.0,
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


class ReviewRegressionTests(unittest.TestCase):
    """Regressions for the defects found in code review."""

    def test_truncated_live_view_cannot_authorize_suppression(self):
        # Once the live event view stops tracking, the observation is a stale
        # prefix. A stale prefix always looks maximally stable.
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(),
            calibration=confident_model(risk=0.01),
            clock=lambda: 0.0,
        )
        router.on_run_start(_FakeContext())

        fresh = observation(observation_truncated=False)
        allowed = router._authorize(propose(fresh, router.policy), fresh, 100.0)
        self.assertTrue(allowed.granted)

        stale = observation(observation_truncated=True)
        denied = router._authorize(propose(stale, router.policy), stale, 100.0)
        self.assertFalse(denied.granted)
        self.assertIs(denied.action, RouteAction.CONTINUE)
        self.assertIn("observation_current", denied.reason)

    def test_stop_settles_consumed_work_instead_of_releasing_all_of_it(self):
        # The worker burned CPU producing the observations that authorized the
        # stop. Releasing the whole reservation would free capacity that was in
        # fact consumed and let route.json under-report the run.
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(stage_cpu_ms_estimate=400.0),
            calibration=confident_model(risk=0.01),
            clock=lambda: 0.0,
        )
        context = _FakeContext()
        context.stage_elapsed_ms = 250.0
        router.on_run_start(context)
        self.assertTrue(router.authorize_initial(context, "stockfish"))

        subject = observation()
        decision = router._authorize(propose(subject, router.policy), subject, 100.0)
        self.assertTrue(decision.granted)
        command = router._to_command(decision, context)
        self.assertIsNotNone(command)
        self.assertEqual(command.action, "stop_worker")

        lane = router.ledger.snapshot()["lanes"]["shadow:stockfish"]
        self.assertEqual(lane["spent_cpu_ms"], 250.0, "consumed work was not charged")
        self.assertEqual(lane["reserved_cpu_ms"], 0.0, "unspent capacity was not returned")

        # The anchor reservation is still open here; it settles at run end.
        router.on_run_end(context)
        self.assertEqual(router.ledger.snapshot()["open_reservations"], 0)

    def test_unbounded_outward_request_cannot_claim_envelope_compliance(self):
        router = ConservativeRouter(
            envelope=envelope(wall_ms=2000.0), policy=policy(), clock=lambda: 0.0
        )
        bounded, reason = router._classify_anchor_request("go movetime 1500")
        self.assertTrue(bounded, reason)

        for command in (
            "go infinite",
            "go ponder",
            "go movetime 9999",
            "go nodes 100000",
            "go wtime 60000 btime 60000",
            "go",
        ):
            bounded, reason = router._classify_anchor_request(command)
            self.assertFalse(bounded, f"{command!r} was treated as envelope-bounded")
            self.assertTrue(reason)

    def test_unbounded_request_is_recorded_on_the_run(self):
        router = ConservativeRouter(
            envelope=envelope(wall_ms=2000.0), policy=policy(), clock=lambda: 0.0
        )
        context = _FakeContext()
        context.external_go_command = "go infinite"
        router.on_run_start(context)
        self.assertTrue(
            any("not bounded by the declared envelope" in note for note in router.audit.notes),
            router.audit.notes,
        )


class ReviewRegressionRoundTwoTests(unittest.TestCase):
    """Regressions for the second round of code-review findings."""

    def test_envelope_rejects_infinite_and_nan_dimensions(self):
        for bad in (
            {"wall_ms": float("inf"), "cpu_ms": 10.0},
            {"wall_ms": 10.0, "cpu_ms": float("nan")},
            {"wall_ms": 10.0, "cpu_ms": 10.0, "gpu_ms": float("inf")},
            {"wall_ms": 10.0, "cpu_ms": 10.0, "verification_reserve_fraction": float("nan")},
        ):
            with self.assertRaises(BudgetError, msg=bad):
                ResourceEnvelope(**bad)
        # JSON has no infinity literal, but 1e309 decodes to one.
        with self.assertRaises(BudgetError):
            ResourceEnvelope.from_config({"wall_ms": 1e309, "cpu_ms": 10.0})

    def test_ledger_measures_from_the_external_go_not_from_router_start(self):
        # Legal-root qualification happens before the router exists. A
        # self-started clock would hand a slow oracle a second full envelope.
        clock = _Clock(100.0)
        router = ConservativeRouter(
            envelope=envelope(wall_ms=2000.0), policy=policy(), clock=clock
        )
        context = _FakeContext()
        context.started_monotonic = 100.0  # the external `go`
        context.elapsed = 1900.0           # spent qualifying before the router ran
        router.on_run_start(context)

        clock.now = 100.0 + 2.5  # 2500ms after the `go`, past the 2000ms envelope
        self.assertTrue(
            router.ledger.wall_exhausted(),
            "the wall deadline ignored time spent before the router started",
        )

        # Without the seed the same elapsed time reads as no time at all.
        unseeded = BudgetLedger(envelope(wall_ms=2000.0), clock=clock)
        self.assertFalse(unseeded.wall_exhausted())

    def test_qualification_time_is_charged_to_the_envelope(self):
        router = ConservativeRouter(
            envelope=envelope(), policy=policy(), clock=lambda: 0.0
        )
        context = _FakeContext()
        context.elapsed = 250.0
        router.on_run_start(context)
        lane = router.ledger.snapshot()["lanes"]["qualification"]
        self.assertEqual(lane["spent_cpu_ms"], 250.0)
        self.assertIn("controller.qualification_ms", lane["native_work"])

    def test_a_stage_that_outruns_its_estimate_is_charged_in_full(self):
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(stage_cpu_ms_estimate=400.0),
            calibration=confident_model(risk=0.01),
            clock=lambda: 0.0,
        )
        context = _FakeContext()
        context.stage_elapsed_ms = 900.0  # more than the 400ms reservation
        router.on_run_start(context)
        self.assertTrue(router.authorize_initial(context, "stockfish"))

        subject = observation()
        decision = router._authorize(propose(subject, router.policy), subject, 100.0)
        self.assertTrue(decision.granted)
        router._to_command(decision, context)

        lane = router.ledger.snapshot()["lanes"]["shadow:stockfish"]
        self.assertEqual(lane["spent_cpu_ms"], 900.0, "the overrun was clamped away")

    def test_a_failed_anchor_reservation_forbids_an_envelope_claim(self):
        router = ConservativeRouter(
            envelope=envelope(
                wall_ms=2000.0,
                cpu_ms=500.0,
                verification_reserve_fraction=0.0,
                controller_overhead_reserve_ms=0.0,
            ),
            policy=policy(anchor_cpu_ms_estimate=5000.0),
            clock=lambda: 0.0,
        )
        context = _FakeContext()
        router.on_run_start(context)
        self.assertFalse(router._anchor_reserved)
        self.assertTrue(
            any("cannot claim envelope compliance" in n for n in router.audit.notes),
            router.audit.notes,
        )
        self.assertFalse(
            router._anchor_bound[0] and router._anchor_reserved,
            "a run with an unrecorded anchor obligation must not claim compliance",
        )

    def test_a_gpu_envelope_without_a_stage_estimate_accounts_nothing(self):
        unaccounted = ConservativeRouter(
            envelope=envelope(gpu_ms=5000.0),
            policy=policy(stage_gpu_ms_estimate=0.0),
            clock=lambda: 0.0,
        )
        self.assertFalse(unaccounted._gpu_accounted())

        accounted = ConservativeRouter(
            envelope=envelope(gpu_ms=5000.0),
            policy=policy(stage_gpu_ms_estimate=100.0),
            clock=lambda: 0.0,
        )
        self.assertTrue(accounted._gpu_accounted())
        # No GPU declared is trivially accounted.
        self.assertTrue(
            ConservativeRouter(
                envelope=envelope(gpu_ms=0.0), policy=policy(), clock=lambda: 0.0
            )._gpu_accounted()
        )

    def test_gpu_estimates_are_reserved_and_bind(self):
        router = ConservativeRouter(
            envelope=envelope(gpu_ms=150.0),
            policy=policy(stage_gpu_ms_estimate=100.0, anchor_cpu_ms_estimate=1.0),
            clock=lambda: 0.0,
        )
        context = _FakeContext()
        router.on_run_start(context)
        self.assertTrue(router.authorize_initial(context, "lc0"))
        self.assertEqual(router.ledger.snapshot()["committed_gpu_ms"], 100.0)
        # The second worker cannot fit in the remaining 50ms of GPU envelope.
        self.assertFalse(router.authorize_initial(context, "stockfish"))

    def test_checkpoints_skip_owners_with_no_dispatch_state(self):
        # An owner excluded for an unhealthy process, or holding an empty root
        # region, has no state. It would look merely idle and could collect a
        # reservation the coordinator silently drops.
        router = ConservativeRouter(
            envelope=envelope(), policy=policy(), clock=lambda: 0.0
        )
        context = _FakeContext()
        context.dispatchable = ("stockfish",)
        router.on_run_start(context)
        router.on_checkpoint(context)
        owners = {d["observation"]["owner"] for d in router.audit.decisions}
        self.assertEqual(owners, {"stockfish"})

    def test_engine_native_work_reaches_the_budget_snapshot(self):
        router = ConservativeRouter(
            envelope=envelope(), policy=policy(), clock=lambda: 0.0
        )
        context = _WorkContext()
        router.on_run_start(context)
        router.on_checkpoint(context)
        totals = router.ledger.native_work_by_semantics()
        self.assertEqual(totals.get("lc0.uci_nodes"), 4242.0)
        self.assertNotIn("nodes", totals)

    def test_routing_and_budget_errors_are_not_controller_runtime_errors(self):
        # This is why controller/__main__.py has to name them explicitly: they
        # are siblings of controller.runtime.RuntimeError, not subclasses.
        from controller.runtime import RuntimeError as ControllerRuntimeError

        self.assertFalse(issubclass(RoutingError, ControllerRuntimeError))
        self.assertFalse(issubclass(BudgetError, ControllerRuntimeError))
        self.assertTrue(issubclass(RoutingError, RuntimeError))
        self.assertTrue(issubclass(BudgetError, RuntimeError))


class ReviewRegressionRoundThreeTests(unittest.TestCase):
    """Round-three review findings, each reproduced before it was fixed."""

    # -- R5: suppression thresholds were never range-checked ----------------

    def test_a_risk_threshold_above_one_is_refused(self):
        """`stop_max_reversal_risk: 2` does not misconfigure the gate; it deletes it."""
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config({"policy": "conservative_v1", "stop_max_reversal_risk": 2.0})

    def test_a_negative_stability_floor_is_refused(self):
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(
                {"policy": "conservative_v1", "stop_min_stability_fraction": -1.0}
            )

    def test_a_support_floor_below_one_is_refused(self):
        """A floor of zero authorizes suppression from a bucket with no evidence."""
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config({"policy": "conservative_v1", "stop_min_support": 0})

    def test_a_non_finite_threshold_is_refused(self):
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(
                {"policy": "conservative_v1", "stop_max_reversal_risk": float("nan")}
            )

    def test_a_zero_checkpoint_interval_is_refused(self):
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config({"policy": "conservative_v1", "checkpoint_interval_ms": 0})

    def test_a_shipped_active_profile_is_still_accepted(self):
        """The validation is worthless if it rejects the profile it ships with."""
        document = json.loads((ROOT / "config" / "allfather.active.validation.json").read_text())
        parsed = RoutingPolicy.from_config(document["routing"])
        self.assertEqual(parsed.stop_min_support, 25)
        self.assertEqual(parsed.stop_max_reversal_risk, 0.05)

    # -- R1: a telemetry backlog hid events from the live decision ----------

    def test_c_a_backlogged_observation_may_not_authorize_a_stop(self):
        """Events already reported but not yet translated are events not seen."""
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(),
            calibration=confident_model(risk=0.0001, support=900),
        )
        router.on_run_start(_FakeContext())
        settled = observation(active=True, leader_flips=0, stable_run_fraction=1.0)
        allowed = router._authorize(propose(settled, router.policy), settled, 10.0)

        stale = observation(
            active=True,
            leader_flips=0,
            stable_run_fraction=1.0,
            observation_backlog=3,
        )
        denied = router._authorize(propose(stale, router.policy), stale, 10.0)

        self.assertTrue(allowed.granted, "the fixture must otherwise authorize")
        self.assertFalse(denied.granted)
        failed = [gate.name for gate in denied.gates if not gate.passed]
        self.assertIn("observation_drained", failed)

    def test_c_the_backlog_is_recorded_on_the_decision(self):
        """The audit has to state how far behind the observation was."""
        stale = observation(observation_backlog=7)
        self.assertEqual(stale.as_dict()["observation_backlog"], 7)

    def test_c_a_drained_observation_reports_no_backlog(self):
        self.assertEqual(observation().as_dict()["observation_backlog"], 0)

    # -- R6: max() across stages understated multi-stage native work --------

    def test_c_native_work_sums_across_stages_and_maxes_within_one(self):
        """A UCI node counter restarts on each `go`; two stages are not one."""
        ledger = BudgetLedger(envelope())
        # stage one reports a rising cumulative counter
        ledger.record_native_work(
            "shadow:stockfish", value=5000, semantics="stockfish.uci_nodes", stage="s1"
        )
        ledger.record_native_work(
            "shadow:stockfish", value=8000, semantics="stockfish.uci_nodes", stage="s1"
        )
        # stage two restarts from zero
        ledger.record_native_work(
            "shadow:stockfish", value=3000, semantics="stockfish.uci_nodes", stage="s2"
        )
        ledger.record_native_work(
            "shadow:stockfish", value=8000, semantics="stockfish.uci_nodes", stage="s2"
        )
        totals = ledger.native_work_by_semantics()
        self.assertEqual(totals["stockfish.uci_nodes"], 16000.0)

    def test_c_per_stage_decomposition_is_auditable(self):
        ledger = BudgetLedger(envelope())
        ledger.record_native_work("shadow:lc0", value=400, semantics="lc0.uci_nodes", stage="s1")
        ledger.record_native_work("shadow:lc0", value=900, semantics="lc0.uci_nodes", stage="s2")
        lane = ledger.snapshot()["lanes"]["shadow:lc0"]
        self.assertEqual(
            lane["native_work_by_stage"],
            {"lc0.uci_nodes@s1": 400.0, "lc0.uci_nodes@s2": 900.0},
        )
        self.assertEqual(lane["native_work"], {"lc0.uci_nodes": 1300.0})

    def test_c_different_semantics_are_still_never_summed_together(self):
        ledger = BudgetLedger(envelope())
        ledger.record_native_work("shadow:a", value=1000, semantics="stockfish.uci_nodes", stage="s1")
        ledger.record_native_work("shadow:b", value=40, semantics="lc0.uci_nodes", stage="s1")
        totals = ledger.native_work_by_semantics()
        self.assertEqual(totals, {"stockfish.uci_nodes": 1000.0, "lc0.uci_nodes": 40.0})
        self.assertNotIn("total", totals)


class ReviewRegressionRoundFourTests(unittest.TestCase):
    """Round-four findings on the live gate, the ledger, and settings."""

    # -- Q2: lossy telemetry was invisible to the live gate -----------------

    def test_a_lossy_stream_may_not_authorize_a_stop(self):
        """A dropped event never comes back, and a lost flip reads as stability."""
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(),
            calibration=confident_model(risk=0.0001, support=900),
        )
        router.on_run_start(_FakeContext())
        settled = observation(active=True, leader_flips=0, stable_run_fraction=1.0)
        allowed = router._authorize(propose(settled, router.policy), settled, 10.0)

        lost = observation(
            active=True,
            leader_flips=0,
            stable_run_fraction=1.0,
            observation_lossy=True,
        )
        denied = router._authorize(propose(lost, router.policy), lost, 10.0)

        self.assertTrue(allowed.granted, "the fixture must otherwise authorize")
        self.assertFalse(denied.granted)
        self.assertIn("observation_intact", [g.name for g in denied.gates if not g.passed])

    def test_the_lossy_flag_is_recorded_on_the_decision(self):
        self.assertTrue(observation(observation_lossy=True).as_dict()["observation_lossy"])
        self.assertFalse(observation().as_dict()["observation_lossy"])

    # -- Q5: an extension the coordinator could not run leaked its reservation

    def test_an_undispatched_extension_returns_its_reservation(self):
        router = ConservativeRouter(envelope=envelope(cpu_ms=5000), policy=policy())
        router.on_run_start(_FakeContext())
        before = router.ledger.available_cpu_ms()
        router._reservations.setdefault("stockfish", []).append(
            router.ledger.reserve("shadow:stockfish", cpu_ms=400.0)
        )
        self.assertLess(router.ledger.available_cpu_ms(), before)

        router.release_undispatched("stockfish")
        self.assertEqual(router.ledger.available_cpu_ms(), before)
        self.assertNotIn("stockfish", router._reservations)

    def test_releasing_an_owner_with_no_reservation_is_harmless(self):
        router = ConservativeRouter(envelope=envelope(), policy=policy())
        router.on_run_start(_FakeContext())
        before = router.ledger.available_cpu_ms()
        router.release_undispatched("lc0")
        self.assertEqual(router.ledger.available_cpu_ms(), before)

    def test_only_the_latest_extension_is_released(self):
        """A rollback must not return an earlier stage's committed capacity."""
        router = ConservativeRouter(envelope=envelope(cpu_ms=5000), policy=policy())
        router.on_run_start(_FakeContext())
        first = router.ledger.reserve("shadow:stockfish", cpu_ms=400.0)
        second = router.ledger.reserve("shadow:stockfish", cpu_ms=400.0)
        router._reservations["stockfish"] = [first, second]
        router.release_undispatched("stockfish")
        self.assertEqual(router._reservations["stockfish"], [first])



def _end_and_read_claim(router, context) -> dict:
    """Finish the run into a real directory and read back its certificate."""
    with tempfile.TemporaryDirectory() as tmp:
        context.run_dir = Path(tmp)
        router.on_run_end(context)
        document = json.loads((Path(tmp) / "route.json").read_text())
    return document["envelope_claim"]


class ReviewRegressionRoundFiveTests(unittest.TestCase):
    """Round-five findings on the envelope claim and CPU accounting."""

    # -- S1: the claim ignored the clock entirely ---------------------------

    def test_a_run_past_its_wall_envelope_cannot_claim_compliance(self):
        """CPU and GPU ceilings are not the whole envelope.

        A slow legal-root oracle alone can carry a run past `wall_ms` while
        every reservation stays inside its ceiling.
        """
        clock = _Clock(0.0)
        router = ConservativeRouter(
            envelope=envelope(wall_ms=100.0, cpu_ms=100000.0),
            policy=policy(anchor_cpu_ms_estimate=10.0),
            clock=clock,
        )
        context = _FakeContext()
        context.external_go_command = "go movetime 10"
        router.on_run_start(context)
        clock.now += 5.0  # 5 s against a declared 100 ms wall envelope
        claim = _end_and_read_claim(router, context)
        self.assertTrue(claim["reservations_within_envelope"])
        self.assertFalse(claim["wall_within_envelope"])
        self.assertFalse(
            claim["claimed"],
            "a run that outlasted its wall envelope reported itself compliant",
        )

    def test_a_run_inside_its_wall_envelope_still_claims(self):
        router = ConservativeRouter(
            envelope=envelope(wall_ms=100000.0, cpu_ms=100000.0),
            policy=policy(anchor_cpu_ms_estimate=10.0),
        )
        context = _FakeContext()
        context.external_go_command = "go movetime 10"
        router.on_run_start(context)
        claim = _end_and_read_claim(router, context)
        self.assertTrue(claim["wall_within_envelope"])
        self.assertTrue(claim["claimed"])

    # -- S5: CPU was settled from wall time, ignoring Threads ---------------

    def test_cpu_spend_scales_with_the_configured_thread_count(self):
        """A four-thread stage running 400 ms did not consume 400 CPU-ms."""
        spend = {}
        for threads in (1, 4):
            router = ConservativeRouter(envelope=envelope(cpu_ms=100000.0), policy=policy())
            context = _FakeContext()
            context.threads = threads
            context.stage_ms = 400.0
            router.on_run_start(context)
            router._reservations["stockfish"] = [
                router.ledger.reserve("shadow:stockfish", cpu_ms=400.0)
            ]
            router._settle_owner(context, "stockfish")
            spend[threads] = router.ledger.snapshot()["lanes"]["shadow:stockfish"]["spent_cpu_ms"]
        self.assertEqual(spend[1], 400.0)
        self.assertEqual(
            spend[4], 1600.0, "four threads of 400 ms wall were charged as 400 CPU-ms"
        )

    def test_the_claim_says_cpu_is_an_estimate_not_a_measurement(self):
        router = ConservativeRouter(envelope=envelope(), policy=policy())
        context = _FakeContext()
        router.on_run_start(context)
        claim = _end_and_read_claim(router, context)
        self.assertEqual(claim["cpu_measurement"], "stage_wall_ms_x_configured_threads")


class ReviewRegressionRoundSixTests(unittest.TestCase):
    """Round-six findings on CPU accounting and the observation floor."""

    # -- T2: thread scaling reached _settle_owner but not the stop path ----

    def test_a_stopped_stage_is_also_charged_by_thread_count(self):
        """The early-stop path bypassed the scaling added to `_settle_owner`."""
        charged = {}
        for threads in (1, 4):
            router = ConservativeRouter(
                envelope=envelope(cpu_ms=100000.0),
                policy=policy(),
                calibration=confident_model(risk=0.0001, support=900),
            )
            context = _FakeContext()
            context.threads = threads
            context.stage_elapsed_ms = 400.0
            router.on_run_start(context)
            router._reservations["stockfish"] = [
                router.ledger.reserve("shadow:stockfish", cpu_ms=400.0)
            ]
            settled = observation(active=True, leader_flips=0, stable_run_fraction=1.0)
            decision = router._authorize(propose(settled, router.policy), settled, 10.0)
            self.assertTrue(decision.granted, "the fixture must authorize a stop")
            command = router._to_command(decision, context)
            self.assertIsNotNone(command)
            self.assertEqual(command.action, "stop_worker")
            lane = router.ledger.snapshot()["lanes"]["shadow:stockfish"]
            charged[threads] = lane["spent_cpu_ms"]
        self.assertEqual(charged[1], 400.0)
        self.assertEqual(
            charged[4],
            1600.0,
            "a four-thread worker stopped after 400 ms was charged 400 CPU-ms",
        )

    # -- T6: one node floor was applied across incomparable semantics ------

    def test_the_observation_floor_is_declared_per_semantics(self):
        """An alpha-beta node count and an LC0 visit-derived count differ."""
        shaped = policy(min_observation_nodes=4000)
        self.assertEqual(shaped.observation_floor_for("stockfish.uci_nodes"), 4000.0)
        self.assertEqual(shaped.observation_floor_for("reckless.uci_nodes"), 4000.0)
        self.assertIsNone(
            shaped.observation_floor_for("lc0.uci_nodes"),
            "LC0 borrowed the alpha-beta floor it is declared incomparable with",
        )
        self.assertIsNone(shaped.observation_floor_for(None))

    def test_an_undeclared_semantics_cannot_satisfy_the_floor(self):
        router = ConservativeRouter(
            envelope=envelope(),
            policy=policy(min_observation_nodes=10),
            calibration=confident_model(risk=0.0001, support=900),
        )
        router.on_run_start(_FakeContext())
        lc0 = observation(
            owner="lc0",
            instance="lc0-shadow",
            active=True,
            leader_flips=0,
            stable_run_fraction=1.0,
            work_value=1_000_000.0,
            work_semantics="lc0.uci_nodes",
        )
        decision = router._authorize(propose(lc0, router.policy), lc0, 10.0)
        self.assertFalse(decision.granted)
        self.assertIn("minimum_observation", [g.name for g in decision.gates if not g.passed])

    def test_a_declared_lc0_floor_is_honoured(self):
        """The fix refuses an undeclared quantity; it does not ban LC0."""
        shaped = policy(observation_floors={"lc0.uci_nodes": 500.0})
        self.assertEqual(shaped.observation_floor_for("lc0.uci_nodes"), 500.0)



class ReviewRegressionRoundSevenTests(unittest.TestCase):
    """Round-seven findings on anchor CPU, floors, and out-of-sample scope."""

    # -- U1: the anchor fallback reservation ignored its own threads -------

    def test_the_anchor_wall_fallback_is_scaled_by_its_thread_count(self):
        """`wall_ms` is a duration. A four-thread anchor spends four times it."""
        reserved = {}
        for threads in (1, 4):
            router = ConservativeRouter(
                envelope=envelope(wall_ms=1000.0, cpu_ms=100000.0),
                policy=policy(anchor_cpu_ms_estimate=0.0),
            )
            context = _FakeContext()
            context.anchor_thread_count = threads
            router.on_run_start(context)
            reserved[threads] = router.ledger.snapshot()["lanes"]["anchor"]["reserved_cpu_ms"]
        self.assertEqual(reserved[1], 1000.0)
        self.assertEqual(
            reserved[4],
            4000.0,
            "a four-thread anchor reserved one thread's worth of CPU",
        )

    def test_an_explicit_anchor_estimate_is_not_rescaled(self):
        """A declared total-CPU figure is already a total."""
        router = ConservativeRouter(
            envelope=envelope(wall_ms=1000.0, cpu_ms=100000.0),
            policy=policy(anchor_cpu_ms_estimate=2500.0),
        )
        context = _FakeContext()
        context.anchor_thread_count = 4
        router.on_run_start(context)
        self.assertEqual(
            router.ledger.snapshot()["lanes"]["anchor"]["reserved_cpu_ms"], 2500.0
        )

    # -- U4: the floors added in round six were never range-checked --------

    def test_a_negative_observation_floor_is_refused(self):
        """Round three range-checked every threshold for exactly this reason."""
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(
                {"policy": "conservative_v1", "observation_floors": {"lc0.uci_nodes": -1}}
            )

    def test_a_non_finite_observation_floor_is_refused(self):
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(
                {
                    "policy": "conservative_v1",
                    "observation_floors": {"lc0.uci_nodes": float("inf")},
                }
            )

    def test_observation_floors_must_be_a_mapping(self):
        with self.assertRaises(RoutingError):
            RoutingPolicy.from_config(
                {"policy": "conservative_v1", "observation_floors": [1, 2, 3]}
            )

    def test_a_valid_observation_floor_is_accepted(self):
        parsed = RoutingPolicy.from_config(
            {"policy": "conservative_v1", "observation_floors": {"lc0.uci_nodes": 500}}
        )
        self.assertEqual(parsed.observation_floor_for("lc0.uci_nodes"), 500.0)


    # -- U3: "validated" did not mean validated for the bucket served ------

    def test_a_bucket_with_no_held_out_rows_may_not_authorize(self):
        """`test_rows > 0` says the model saw some held-out data, not this bucket's.

        A bucket can be well supported in training while every held-out row
        landed elsewhere, so the risk estimate actually being served was never
        evaluated out of sample at all.
        """
        model = confident_model(risk=0.0001, support=900)
        served = observation(active=True, leader_flips=0, stable_run_fraction=1.0)
        key = bucket_key(served.calibration_features(), scope=served.owner)

        # The model evaluated *some* rows, but none in the bucket being served.
        model.evaluation = dict(
            model.evaluation,
            test_rows=40,
            reliability=[{"bucket": "lc0|n0|s0|f0", "count": 40, "observed_rate": 0.0}],
        )
        router = ConservativeRouter(envelope=envelope(), policy=policy(), calibration=model)
        router.on_run_start(_FakeContext())
        denied = router._authorize(propose(served, router.policy), served, 10.0)
        self.assertFalse(denied.granted)
        self.assertIn("calibration_validated", [g.name for g in denied.gates if not g.passed])

        # With held-out evidence in that bucket, the gate passes again.
        model.evaluation = dict(
            model.evaluation,
            reliability=[{"bucket": key, "count": 12, "observed_rate": 0.0}],
        )
        allowed = router._authorize(propose(served, router.policy), served, 10.0)
        self.assertTrue(allowed.granted)

    # -- U5: a cancelled run lost the work reported after the last checkpoint

    def test_final_native_work_reaches_a_cancelled_run_s_certificate(self):
        """`_await_completion` returns with no final checkpoint once cancelled."""
        router = ConservativeRouter(envelope=envelope(), policy=policy())
        context = _WorkContext()
        router.on_run_start(context)
        # No checkpoint ever ran: this is the cancelled-before-first-checkpoint
        # case, which reported no native work at all.
        self.assertEqual(router.ledger.native_work_by_semantics(), {})
        with tempfile.TemporaryDirectory() as tmp:
            context.run_dir = Path(tmp)
            router.on_run_end(context)
            document = json.loads((Path(tmp) / "route.json").read_text())
        totals = document["budget"]["native_work_by_semantics"]
        self.assertIn(
            "lc0.uci_nodes",
            totals,
            "work reported after the last checkpoint never reached route.json",
        )


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
    def run_active(
        self,
        tmp: Path,
        *,
        roots: str = "e2e4,d2d4,g1f3,b1c3,c2c4,g2g3",
        **routing_overrides,
    ) -> tuple[list[str], dict, dict]:
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
            roots=roots,
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

    def test_a_terminal_position_still_writes_a_route_audit(self):
        """A run that dispatched no shadow work still spent the anchor's compute.

        Every qualification exit -- a terminal position, a dead oracle, an
        external `searchmoves` leaving no shadow root -- returned from `_execute`
        before the router's run was opened, so the anchor's cost was never
        charged, `on_run_end` never ran, and no audit certificate was written
        for a search that really happened.
        """
        with tempfile.TemporaryDirectory() as tmp:
            lines, manifest, route = self.run_active(Path(tmp), roots="")
            bestmoves = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(len(bestmoves), 1, "the outward search still answered")
            self.assertTrue(manifest["legal_root_oracle"]["terminal_universe"])

            # The audit exists and describes the envelope the anchor ran inside.
            self.assertEqual(route["schema_version"], 1)
            self.assertIn("envelope_claim", route)
            self.assertIn("budget", route)
            self.assertTrue(route["budget"]["envelope"]["cpu_ms"] > 0)
            # No shadow worker was observed, so there is nothing to decide about.
            self.assertEqual(route["decisions"], [])
            # The anchor's reservation is still accounted for.
            self.assertTrue(route["envelope_claim"]["anchor_cost_reserved"])

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


class _Clock:
    """A clock a test can advance deliberately."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _lc0_events() -> list[dict]:
    """A minimal real LC0 telemetry stream reporting engine-native work."""
    adapter = Lc0TelemetryAdapter(
        score_type="centipawn",
        search_id="unit:lc0-shadow:0",
        engine_instance="lc0-shadow",
        position_id="pos-unit",
    )
    events = [
        adapter.start(
            position={"base_fen": STARTPOS_FEN, "moves": []},
            request={"limits": [], "raw": "go nodes 20000", "root_moves": ["g1f3"]},
            observed_ms=0,
            controller={"execution_mode": "active", "owner": "lc0"},
        )
    ]
    events += adapter.consume(
        "info depth 3 multipv 1 nodes 4242 score cp 12 pv g1f3 g8f6", observed_ms=10.0
    )
    return events


class _WorkContext:
    """A context whose single worker reports engine-native work."""

    run_id = "unit-test-work"
    run_dir = Path("/nonexistent")
    owners = ("lc0",)
    owner_roots: dict[str, tuple[str, ...]] = {"lc0": ("g1f3",)}
    external_go_command = "go movetime 1000"
    started_monotonic = 0.0

    def elapsed_ms(self) -> float:
        return 0.0

    def dispatchable_owners(self) -> tuple[str, ...]:
        return self.owners

    def active_owners(self) -> tuple[str, ...]:
        return ()

    def owner_instance(self, owner: str) -> str:
        return "lc0-shadow"

    def owner_family(self, owner: str) -> str:
        return "lc0"

    def owner_events(self, owner: str) -> list[dict]:
        return _lc0_events()

    def owner_active(self, owner: str) -> bool:
        return False

    def owner_stages(self, owner: str) -> int:
        return 1

    def owner_elapsed_stage_ms(self, owner: str) -> float | None:
        return None

    def owner_events_truncated(self, owner: str) -> bool:
        return False


class _FakeContext:
    run_id = "unit-test-run"
    run_dir = Path("/nonexistent")
    owners = ("stockfish", "reckless", "lc0")
    owner_roots: dict[str, tuple[str, ...]] = {}
    external_go_command = "go movetime 1000"
    stage_elapsed_ms: float | None = None
    elapsed = 0.0
    dispatchable: tuple[str, ...] | None = None

    def elapsed_ms(self) -> float:
        return self.elapsed

    def dispatchable_owners(self) -> tuple[str, ...]:
        return self.owners if self.dispatchable is None else self.dispatchable

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

    def owner_elapsed_stage_ms(self, owner: str) -> float | None:
        return self.stage_elapsed_ms

    def owner_events_truncated(self, owner: str) -> bool:
        return False

    pending: int = 0
    lossy: bool = False

    def owner_events_pending(self, owner: str, *, barrier_s: float = 0.025) -> int:
        return self.pending

    def owner_evidence_lossy(self, owner: str) -> bool:
        return self.lossy

    threads: int = 1
    anchor_thread_count: int = 1

    def owner_threads(self, owner: str) -> int:
        return self.threads

    def anchor_threads(self) -> int:
        return self.anchor_thread_count

    def owner_last_stage_ms(self, owner: str) -> float | None:
        return self.stage_ms

    stage_ms: float | None = None


if __name__ == "__main__":
    unittest.main()
