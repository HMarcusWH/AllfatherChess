#!/usr/bin/env python3
"""Deterministic M14-F regime-classifier contract.

This contract is intentionally engine-free: M14-F is an offline evidence
classifier. Real/fake sealed-source extraction is covered by the controller
regressions; earlier milestones already qualify the source evidence machinery.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.regime_calibration import (
    build_regime_dataset,
    fit_regime_support_model,
)
from controller.regimes import (
    RegimeObservation,
    RegimeStatus,
    RefinementRegimeFeatures,
    SearchRegime,
    TimingRegimeFeatures,
    VerifierRegimeFeatures,
    classify_regimes,
)


RESULT_DIR = ROOT / "build" / "test-results" / "regimes"
OWNERS = ("stockfish", "reckless", "lc0")


class ContractError(RuntimeError):
    pass


def _observation(
    name: str,
    *,
    terminals: tuple[str, str, str],
    pattern: str,
    relock: str,
    mate: tuple[str, ...] = (),
    refinement: RefinementRegimeFeatures | None = None,
    request_mode: str = "nodes",
) -> RegimeObservation:
    return RegimeObservation(
        run_id=f"contract-{name}",
        generation=1,
        position_id=f"pos-{name}",
        candidate_roots=("e2e4", "d2d4", "g1f3"),
        adapter_evidence_digest=hashlib.sha256(name.encode()).hexdigest(),
        source_hashes=(
            ("parent_manifest", "1" * 64),
            ("verification_manifest", "2" * 64),
            ("crossfeed_manifest", "3" * 64),
        ),
        verify_terminal_by_owner=tuple(zip(OWNERS, terminals)),
        verify_pattern=pattern,
        relock_status=relock,
        relock_fraction=0.5 if relock == "RELOCK_OBSERVED" else None,
        verifiers=tuple(
            VerifierRegimeFeatures(
                owner=owner,
                terminal_move=move,
                observation_count=8,
                leader_flips=0 if relock == "RELOCK_OBSERVED" else 2,
                stable_run_fraction=0.9 if relock == "RELOCK_OBSERVED" else 0.4,
                pv_persistence=0.8,
            )
            for owner, move in zip(OWNERS, terminals)
        ),
        native_mate_alarm_families=mate,
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
        timing=TimingRegimeFeatures(
            request_mode=request_mode,
            limits=(("nodes", 64),)
            if request_mode == "nodes"
            else (("movetime", 100),),
        ),
    )


def _assert_status(result, regime, expected):
    actual = result.status_for(regime)
    if actual is not expected:
        raise ContractError(
            f"{regime.value}: expected {expected.value}, got {actual.value}"
        )


def main() -> int:
    stable = _observation(
        "stable",
        terminals=("e2e4", "e2e4", "e2e4"),
        pattern="unanimous",
        relock="RELOCK_OBSERVED",
    )
    stable_result = classify_regimes(stable)
    _assert_status(
        stable_result,
        SearchRegime.STABLE_CONVERGENT,
        RegimeStatus.ACTIVE,
    )
    _assert_status(
        stable_result,
        SearchRegime.CROSS_ENGINE_DISAGREEMENT,
        RegimeStatus.INACTIVE,
    )

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
    rupture = _observation(
        "rupture",
        terminals=("e2e4", "d2d4", "g1f3"),
        pattern="all_different",
        relock="RELOCK_FAILED",
        mate=("stockfish",),
        refinement=productive,
    )
    rupture_result = classify_regimes(rupture)
    for regime in (
        SearchRegime.TACTICAL_RUPTURE,
        SearchRegime.CROSS_ENGINE_DISAGREEMENT,
        SearchRegime.REFINEMENT_PRODUCTIVE,
    ):
        _assert_status(rupture_result, regime, RegimeStatus.ACTIVE)
    for regime in (
        SearchRegime.POLICY_DIFFUSE,
        SearchRegime.ENDGAME_EXACT,
        SearchRegime.TIME_CRITICAL,
    ):
        _assert_status(rupture_result, regime, RegimeStatus.UNSUPPORTED)

    training = [
        _observation(
            f"train-{index}",
            terminals=("e2e4", "d2d4", "g1f3"),
            pattern="all_different",
            relock="RELOCK_FAILED",
        )
        for index in range(1, 8)
    ]
    dataset = build_regime_dataset(training)
    source_sha = hashlib.sha256(
        json.dumps(dataset, sort_keys=True).encode("utf-8")
    ).hexdigest()
    model = fit_regime_support_model(
        dataset,
        source_sha256=source_sha,
        min_support=1,
        min_position_groups=1,
    )
    unseen = _observation(
        "unseen",
        terminals=("e2e4", "e2e4", "e2e4"),
        pattern="unanimous",
        relock="RELOCK_OBSERVED",
        mate=("lc0",),
        request_mode="movetime",
    )
    domain = model.evaluate(unseen)
    if domain.in_domain:
        raise ContractError("unseen structural bucket was admitted in-domain")
    ood = classify_regimes(unseen, domain=domain)
    _assert_status(ood, SearchRegime.OUT_OF_DOMAIN, RegimeStatus.ACTIVE)

    report = {
        "schema_version": 1,
        "stable": stable_result.as_dict(),
        "rupture": rupture_result.as_dict(),
        "out_of_domain": ood.as_dict(),
        "model_id": model.model_id,
        "claim": (
            "M14-F deterministically classifies supported multi-label structural "
            "regimes, reports unsupported hypotheses rather than fabricating "
            "proxies, and fails closed on an unseen support bucket. It does not "
            "route work, reserve resources, dispatch engines, or authorize moves."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("search-regime contract passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"search-regime contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
