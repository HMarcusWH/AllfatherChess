#!/usr/bin/env python3
"""Real-engine subprocess contract for M14-E cross-feed adapters.

This contract qualifies adapter-generated ordinary UCI restrictions against the
three vendored engine families.  It does not wire adapters into the controller
runtime and makes no move-quality or strength claim.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.crossfeed import (
    AdapterSourceHint,
    CrossFeedAdapterEvidence,
    Lc0CrossFeedAdapter,
    RecklessCrossFeedAdapter,
    StockfishCrossFeedAdapter,
)
from common.search_request import parse_position_command
from controller.crossfeed import NativeEvaluation, NativeWork
from controller.runtime import load_runtime_config
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.crossfeed.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "crossfeed-adapters"
ROOTS = ("e2e4", "d2d4", "g1f3")
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def _hint(owner: str, move: str, rank: int) -> AdapterSourceHint:
    evaluation = (
        NativeEvaluation(
            kind="scalar",
            semantics="lc0.uci_score.Q",
            value=float(rank) / 10.0,
            bound="none",
            perspective="unknown",
        )
        if owner == "lc0"
        else NativeEvaluation(
            kind="cp",
            semantics=f"{owner}.uci_cp",
            value=rank * 10,
            bound="none",
            perspective="unknown",
        )
    )
    return AdapterSourceHint(
        decision_move=move,
        observed_move=move,
        source_owner=owner,
        source_instance=f"{owner}-shadow",
        source_family=owner,
        source_phase="VERIFY",
        source_scope_id=f"contract:verify:{owner}",
        source_prefix=(),
        source_depth=0,
        candidate_universe=ROOTS,
        candidate_universe_complete=True,
        source_rank=rank,
        pv_prefix=(move,),
        evaluations=(evaluation,),
        work=(
            NativeWork(
                value=100 + rank,
                unit="count" if owner == "lc0" else "nodes",
                semantics=f"{owner}.uci_nodes",
            ),
        ),
        search_id=f"contract:verify:{owner}",
        sequence=rank,
        observed_ms=float(rank),
        stage_disposition="completed",
    )


def _refine_hint() -> AdapterSourceHint:
    return AdapterSourceHint(
        decision_move="e2e4",
        observed_move="e7e5",
        source_owner="stockfish",
        source_instance="stockfish-shadow",
        source_family="stockfish",
        source_phase="REFINE",
        source_scope_id="contract:refine:e2e4",
        source_prefix=("e2e4",),
        source_depth=1,
        candidate_universe=("e7e5",),
        candidate_universe_complete=False,
        source_rank=1,
        pv_prefix=("e2e4", "e7e5"),
        evaluations=(
            NativeEvaluation(
                kind="cp",
                semantics="stockfish.uci_cp",
                value=20,
                bound="none",
                perspective="unknown",
            ),
        ),
        work=(
            NativeWork(
                value=120,
                unit="nodes",
                semantics="stockfish.uci_nodes",
            ),
        ),
        search_id="contract:refine:stockfish",
        sequence=1,
        observed_ms=5.0,
        stage_disposition="completed",
    )


def _evidence() -> CrossFeedAdapterEvidence:
    hints = tuple(
        _hint(owner, move, rank)
        for owner in ("stockfish", "reckless", "lc0")
        for rank, move in enumerate(ROOTS, start=1)
    ) + (_refine_hint(),)
    # This digest represents the upstream immutable CrossFeedView identity for
    # the contract fixture. Adapter operation identity additionally binds the
    # full adapter evidence digest.
    return CrossFeedAdapterEvidence(
        run_id="crossfeed-adapter-contract",
        generation=1,
        position_id="startpos",
        candidate_roots=ROOTS,
        source_view_digest="0" * 64,
        hints=hints,
        evidence_faults=(),
    )


def _bestmove(lines: list[str]) -> str:
    values = [
        line.split()[1].lower()
        for line in lines
        if line.startswith("bestmove ") and len(line.split()) >= 2
    ]
    if len(values) != 1 or not _MOVE_RE.fullmatch(values[0]):
        raise ContractError(f"expected one canonical bestmove, got {values}")
    return values[0]


def _run_operation(spec, operation) -> str:
    with UciSession(
        spec.binary,
        cwd=spec.cwd,
        timeout=30.0,
        args=list(spec.args),
    ) as session:
        session.configure(dict(spec.options))
        session.new_game()
        session.send(operation.position_command)
        session.send(operation.go_command)
        lines = session.read_until(
            lambda line: line.startswith("bestmove "),
            label=f"{spec.name} adapter operation",
            timeout=45.0,
        )
        return _bestmove(lines)


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    if config.shadow is None:
        raise ContractError("cross-feed validation config has no shadow instances")

    evidence = _evidence()
    position = parse_position_command("position startpos")
    adapters = {
        "stockfish": StockfishCrossFeedAdapter(),
        "reckless": RecklessCrossFeedAdapter(),
        "lc0": Lc0CrossFeedAdapter(),
    }

    results: dict[str, object] = {}
    for family, adapter in adapters.items():
        instance = config.shadow.instance_by_owner[family]
        spec = config.backends[instance]

        verify = adapter.compile_verify_set(
            evidence,
            position,
            ROOTS,
            limit={"nodes": 64},
        )
        verify_move = _run_operation(spec, verify)
        if verify_move not in verify.searchmoves:
            raise ContractError(
                f"{family} VERIFY escaped adapter searchmoves: {verify_move}"
            )

        subset = adapter.compile_verify_set(
            evidence,
            position,
            ("e2e4", "g1f3"),
            limit={"nodes": 64},
        )
        subset_move = _run_operation(spec, subset)
        if subset_move not in subset.searchmoves:
            raise ContractError(
                f"{family} subset VERIFY escaped adapter searchmoves: {subset_move}"
            )

        refine = adapter.compile_refine_prefix(
            evidence,
            position,
            ("e2e4", "e7e5"),
            limit={"nodes": 64},
        )
        refine_move = _run_operation(spec, refine)
        if refine_move != "e7e5":
            raise ContractError(
                f"{family} REFINE_PREFIX escaped certified prefix: {refine_move}"
            )

        results[family] = {
            "verify_operation": verify.as_dict(),
            "verify_bestmove": verify_move,
            "subset_operation": subset.as_dict(),
            "subset_bestmove": subset_move,
            "refine_operation": refine.as_dict(),
            "refine_bestmove": refine_move,
            "priority_hint_count": len(adapter.priority_hints(evidence)),
            "tactical_alarm_count": len(adapter.tactical_alarms(evidence)),
        }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "adapter_evidence_digest": evidence.digest,
        "families": results,
        "claim": (
            "Subprocess-safety qualification only: each engine accepted deterministic "
            "adapter-generated VERIFY_SET and REFINE_PREFIX restrictions and returned "
            "inside the declared region. The adapters were not wired into controller "
            "routing, resource authorization, DecisionAuthorization or outward move "
            "selection. No Elo or strength claim."
        ),
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "cross-feed adapter contract passed: "
        + ", ".join(
            f"{family}={entry['verify_bestmove']}"
            for family, entry in results.items()
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"cross-feed adapter contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
