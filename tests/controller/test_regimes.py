#!/usr/bin/env python3
"""M14-F search-regime classifier regressions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.regimes import (
    RegimeDomainAssessment,
    RegimeObservation,
    RegimeStatus,
    RefinementRegimeFeatures,
    SearchRegime,
    TimingRegimeFeatures,
    VerifierRegimeFeatures,
    build_regime_observation_from_run,
    classify_regimes,
)
from tests.controller.test_shadow_runtime import ANCHOR, run_shell, write_shadow_config


OWNERS = ("stockfish", "reckless", "lc0")


def _verifiers(terminals: tuple[str, str, str]) -> tuple[VerifierRegimeFeatures, ...]:
    return tuple(
        VerifierRegimeFeatures(
            owner=owner,
            terminal_move=move,
            observation_count=8,
            leader_flips=0,
            stable_run_fraction=0.9,
            pv_persistence=0.8,
        )
        for owner, move in zip(OWNERS, terminals)
    )


def observation(
    *,
    terminals: tuple[str, str, str] = ("e2e4", "e2e4", "e2e4"),
    verify_pattern: str = "unanimous",
    relock_status: str = "RELOCK_OBSERVED",
    mate_families: tuple[str, ...] = (),
    refinement: RefinementRegimeFeatures | None = None,
    timing: TimingRegimeFeatures | None = None,
    position_id: str = "pos-test",
) -> RegimeObservation:
    return RegimeObservation(
        run_id=f"run-{position_id}",
        generation=1,
        position_id=position_id,
        candidate_roots=("e2e4", "d2d4", "g1f3"),
        adapter_evidence_digest="a" * 64,
        source_hashes=(
            ("parent_manifest", "1" * 64),
            ("verification_manifest", "2" * 64),
            ("crossfeed_manifest", "3" * 64),
        ),
        verify_terminal_by_owner=tuple(zip(OWNERS, terminals)),
        verify_pattern=verify_pattern,
        relock_status=relock_status,
        relock_fraction=0.5 if relock_status == "RELOCK_OBSERVED" else None,
        verifiers=_verifiers(terminals),
        native_mate_alarm_families=mate_families,
        refinement=refinement
        or RefinementRegimeFeatures(
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
        timing=timing
        or TimingRegimeFeatures(
            request_mode="nodes",
            limits=(("nodes", 64),),
        ),
    )


class RegimeClassifierTests(unittest.TestCase):
    def test_stable_convergent_is_exactly_relock_observed(self):
        stable = classify_regimes(observation())
        self.assertIs(
            stable.status_for(SearchRegime.STABLE_CONVERGENT),
            RegimeStatus.ACTIVE,
        )

        no_relock = classify_regimes(
            observation(relock_status="RELOCK_FAILED")
        )
        self.assertIs(
            no_relock.status_for(SearchRegime.STABLE_CONVERGENT),
            RegimeStatus.INACTIVE,
        )

    def test_disagreement_and_tactical_rupture_are_multi_label(self):
        subject = observation(
            terminals=("e2e4", "d2d4", "g1f3"),
            verify_pattern="all_different",
            relock_status="RELOCK_FAILED",
            mate_families=("stockfish",),
        )
        result = classify_regimes(subject)
        self.assertIs(
            result.status_for(SearchRegime.CROSS_ENGINE_DISAGREEMENT),
            RegimeStatus.ACTIVE,
        )
        self.assertIs(
            result.status_for(SearchRegime.TACTICAL_RUPTURE),
            RegimeStatus.ACTIVE,
        )
        self.assertIs(
            result.status_for(SearchRegime.STABLE_CONVERGENT),
            RegimeStatus.INACTIVE,
        )

    def test_unsupported_regimes_are_not_fabricated(self):
        subject = observation(
            timing=TimingRegimeFeatures(
                request_mode="movetime",
                limits=(("movetime", 100),),
            )
        )
        result = classify_regimes(subject)
        for regime in (
            SearchRegime.POLICY_DIFFUSE,
            SearchRegime.ENDGAME_EXACT,
            SearchRegime.TIME_CRITICAL,
        ):
            self.assertIs(result.status_for(regime), RegimeStatus.UNSUPPORTED)

    def test_refinement_productive_requires_recorded_recursive_work(self):
        productive = RefinementRegimeFeatures(
            present=True,
            run_disposition="completed",
            max_depth=4,
            max_expansions=6,
            target_count=2,
            completed_nonterminal_targets=2,
            expansion_count=2,
            completed_expansions=2,
            terminal_expansions=0,
            max_observed_depth=3,
            boundary_reasons=(),
        )
        result = classify_regimes(observation(refinement=productive))
        self.assertIs(
            result.status_for(SearchRegime.REFINEMENT_PRODUCTIVE),
            RegimeStatus.ACTIVE,
        )
        self.assertIs(
            result.status_for(SearchRegime.REFINEMENT_STALLED),
            RegimeStatus.INACTIVE,
        )

    def test_depth_two_profile_is_not_called_stalled(self):
        root_only = RefinementRegimeFeatures(
            present=True,
            run_disposition="completed",
            max_depth=2,
            max_expansions=3,
            target_count=2,
            completed_nonterminal_targets=2,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=(),
        )
        result = classify_regimes(observation(refinement=root_only))
        self.assertIs(
            result.status_for(SearchRegime.REFINEMENT_STALLED),
            RegimeStatus.UNSUPPORTED,
        )

    def test_explicit_boundary_is_not_mislabeled_as_stall(self):
        blocked = RefinementRegimeFeatures(
            present=True,
            run_disposition="completed",
            max_depth=4,
            max_expansions=1,
            target_count=2,
            completed_nonterminal_targets=2,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=("recursive REFINE expansion cap reached",),
        )
        result = classify_regimes(observation(refinement=blocked))
        self.assertIs(
            result.status_for(SearchRegime.REFINEMENT_STALLED),
            RegimeStatus.INACTIVE,
        )

    def test_narrow_clean_stall_definition(self):
        stalled = RefinementRegimeFeatures(
            present=True,
            run_disposition="completed",
            max_depth=4,
            max_expansions=6,
            target_count=2,
            completed_nonterminal_targets=2,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=(),
        )
        result = classify_regimes(observation(refinement=stalled))
        self.assertIs(
            result.status_for(SearchRegime.REFINEMENT_STALLED),
            RegimeStatus.ACTIVE,
        )

    def test_domain_state_is_separate_from_structural_regimes(self):
        subject = observation()
        unknown = RegimeDomainAssessment(
            bucket="x",
            support=0,
            position_group_support=0,
            in_domain=False,
            reason="unseen",
        )
        result = classify_regimes(subject, domain=unknown)
        self.assertIs(
            result.status_for(SearchRegime.OUT_OF_DOMAIN),
            RegimeStatus.OUT_OF_DOMAIN,
        )
        self.assertIs(
            result.status_for(SearchRegime.STABLE_CONVERGENT),
            RegimeStatus.ACTIVE,
        )

    def test_observation_schema_contains_no_cross_engine_numeric_score_or_decision(self):
        payload = json.dumps(observation().as_dict(), sort_keys=True).lower()
        for forbidden in (
            "counterfactual",
            "decisionauthorization",
            "anchor_bestmove",
            "game_outcome",
            "centipawn",
            "uci_score.q",
        ):
            self.assertNotIn(forbidden, payload)


class RegimeExtractionTests(unittest.TestCase):
    def test_sealed_crossfeed_run_builds_decision_inert_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_shadow_config(
                root,
                verification=True,
                crossfeed=True,
                dispatch_nodes=64,
                verification_nodes=32,
                instance_args={
                    ANCHOR: ["--info-lines", "200", "--info-delay-ms", "5"],
                    "stockfish-shadow": ["--leader-schedule", "e2e4"],
                    "reckless-shadow": ["--leader-schedule", "d2d4"],
                    "lc0-shadow": ["--leader-schedule", "g1f3"],
                },
            )
            run_shell(config, ["go nodes 64", "await:bestmove "], timeout=45.0)
            run_dir = next(
                path for path in (root / "replays").iterdir() if path.is_dir()
            )

            subject = build_regime_observation_from_run(run_dir)
            self.assertEqual(subject.position_id[:4], "pos-")
            self.assertEqual(
                tuple(owner for owner, _ in subject.verify_terminal_by_owner),
                OWNERS,
            )
            self.assertEqual(subject.timing.request_mode, "nodes")
            self.assertEqual(
                {name for name, _ in subject.source_hashes},
                {
                    "parent_manifest",
                    "verification_manifest",
                    "crossfeed_manifest",
                },
            )

            payload = subject.as_dict()
            self.assertNotIn("anchor", payload)
            self.assertNotIn("decision", payload)


if __name__ == "__main__":
    unittest.main()
