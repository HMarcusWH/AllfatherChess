#!/usr/bin/env python3
"""M14-G2 unified value-of-compute route tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.regimes import (
    OWNER_ORDER,
    RefinementRegimeFeatures,
    RegimeDomainAssessment,
    RegimeObservation,
    TimingRegimeFeatures,
    VerifierRegimeFeatures,
    classify_regimes,
)
from controller.routing import RoutingPolicy
from controller.staged_decision_calibration import (
    StagedDecisionChangeModel,
    StagedValueEstimate,
    bucket_key,
)
from controller.unified_value_router import choose_staged_route


FEATURES = {
    "transition": "same-process:n64->n128",
    "base_decision_disposition": "NO_PROPOSAL_NONUNANIMOUS",
    "min_observation_count": 8,
    "max_leader_flips": 1,
    "min_stable_run_fraction": 0.75,
}


def staged_model(*, heldout: bool = True) -> StagedDecisionChangeModel:
    key = bucket_key(FEATURES)
    reliability = (
        [
            {
                "bucket": key,
                "count": 4,
                "in_domain_rows": 4,
                "observed_change_rate": 0.0,
                "mean_probability": 0.02,
            }
        ]
        if heldout
        else []
    )
    return StagedDecisionChangeModel(
        model_id="staged-decision-change-v1:test",
        created_utc="2026-09-25T00:00:00Z",
        dataset_id="dataset-test",
        min_support=2,
        min_position_groups=2,
        smoothing_alpha=1.0,
        prior_change_probability=1.0,
        buckets={},
        split_by_position={},
        evaluation={"holdout": {"test_rows": 4, "reliability": reliability}},
        source_sha256="a" * 64,
    )


def staged_estimate(*, probability: float = 0.02, in_domain: bool = True):
    return StagedValueEstimate(
        change_probability=probability,
        support=20 if in_domain else 0,
        position_group_support=8 if in_domain else 0,
        in_domain=in_domain,
        bucket=bucket_key(FEATURES),
        reason=None if in_domain else "unseen bucket",
    )


def classification(*, in_domain: bool = True):
    terminals = (
        ("stockfish", "e2e4"),
        ("reckless", "d2d4"),
        ("lc0", "g1f3"),
    )
    verifiers = tuple(
        VerifierRegimeFeatures(
            owner=owner,
            terminal_move=move,
            observation_count=8,
            leader_flips=1,
            stable_run_fraction=0.75,
            pv_persistence=0.75,
        )
        for owner, move in terminals
    )
    observation = RegimeObservation(
        run_id="run-test",
        generation=1,
        position_id="pos-test",
        candidate_roots=("e2e4", "d2d4", "g1f3"),
        adapter_evidence_digest="b" * 64,
        source_hashes=(),
        verify_terminal_by_owner=terminals,
        verify_pattern="all_different",
        relock_status="RELOCK_FAILED",
        relock_fraction=None,
        verifiers=verifiers,
        native_mate_alarm_families=(),
        refinement=RefinementRegimeFeatures(
            present=False,
            run_disposition=None,
            max_depth=None,
            max_expansions=None,
            target_count=0,
            completed_nonterminal_targets=0,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=(),
        ),
        timing=TimingRegimeFeatures(
            request_mode="movetime",
            limits=(("movetime", 5000),),
        ),
    )
    domain = RegimeDomainAssessment(
        bucket="regime-test",
        support=20 if in_domain else 0,
        position_group_support=8 if in_domain else 0,
        in_domain=in_domain,
        reason=None if in_domain else "unseen structural bucket",
    )
    return classify_regimes(observation, domain=domain)


class UnifiedValueRouteTests(unittest.TestCase):
    def decide(
        self,
        *,
        estimate=None,
        staged=None,
        regime=None,
        regime_model=SimpleNamespace(model_id="regime-support-test"),
        threshold=0.10,
        fallback=False,
        wall=False,
    ):
        return choose_staged_route(
            transition=FEATURES["transition"],
            features=dict(FEATURES),
            estimate=estimate,
            classification=regime,
            staged_model=staged,
            regime_model=regime_model,
            skip_max_change_probability=threshold,
            fallback_latched=fallback,
            wall_exhausted=wall,
        )

    def test_low_risk_heldout_in_domain_state_can_skip_extension(self):
        decision = self.decide(
            estimate=staged_estimate(probability=0.02),
            staged=staged_model(heldout=True),
            regime=classification(in_domain=True),
        )
        self.assertFalse(decision.buy_extension)
        self.assertEqual(decision.action, "SKIP_STAGED_VERIFY")
        self.assertTrue(all(gate.passed for gate in decision.gates))
        self.assertFalse(decision.as_dict()["authority"]["resource"])
        self.assertFalse(decision.as_dict()["authority"]["outward_move"])

    def test_high_change_probability_buys_more_compute(self):
        decision = self.decide(
            estimate=staged_estimate(probability=0.50),
            staged=staged_model(),
            regime=classification(),
        )
        self.assertTrue(decision.buy_extension)
        self.assertEqual(decision.action, "BUY_STAGED_VERIFY")
        self.assertIn("decision_change_risk_low", decision.reason)

    def test_unseen_staged_bucket_buys_more_compute(self):
        decision = self.decide(
            estimate=staged_estimate(in_domain=False),
            staged=staged_model(),
            regime=classification(),
        )
        self.assertTrue(decision.buy_extension)
        self.assertIn("staged_calibration_in_domain", decision.reason)

    def test_bucket_without_heldout_observation_cannot_license_skip(self):
        decision = self.decide(
            estimate=staged_estimate(probability=0.01),
            staged=staged_model(heldout=False),
            regime=classification(),
        )
        self.assertTrue(decision.buy_extension)
        self.assertIn("staged_bucket_heldout_observed", decision.reason)

    def test_out_of_domain_regime_buys_more_compute(self):
        decision = self.decide(
            estimate=staged_estimate(probability=0.01),
            staged=staged_model(),
            regime=classification(in_domain=False),
        )
        self.assertTrue(decision.buy_extension)
        self.assertIn("regime_in_domain", decision.reason)

    def test_missing_models_fail_closed_to_buy_compute(self):
        decision = self.decide(
            estimate=None,
            staged=None,
            regime=None,
            regime_model=None,
        )
        self.assertTrue(decision.buy_extension)
        self.assertEqual(decision.action, "BUY_STAGED_VERIFY")

    def test_closed_resource_window_does_not_start_new_specialist_work(self):
        decision = self.decide(
            estimate=staged_estimate(),
            staged=staged_model(),
            regime=classification(),
            fallback=True,
        )
        self.assertFalse(decision.buy_extension)
        self.assertEqual(decision.action, "FALLBACK_ANCHOR")

    def test_routing_policy_accepts_unified_value_v1(self):
        policy = RoutingPolicy.from_config({"policy": "unified_value_v1"})
        self.assertEqual(policy.policy_name, "unified_value_v1")
        self.assertEqual(policy.as_dict()["policy"], "unified_value_v1")


if __name__ == "__main__":
    unittest.main()
