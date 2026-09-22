#!/usr/bin/env python3
"""Summarize frozen counterfactual hybrid-decision artifacts.

This is descriptive research tooling. It counts proposal/disagreement/timing
states only; it does not score chess correctness or strength.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.counterfactual import (
    CounterfactualError,
    load_counterfactual_artifact,
    verify_counterfactual_integrity,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--replay-root",
        type=Path,
        default=ROOT / "build" / "replays-counterfactual",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build" / "counterfactual-derived" / "summary.json",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    replay_root = args.replay_root
    if not replay_root.is_dir():
        raise SystemExit(f"replay root does not exist: {replay_root}")

    dispositions: Counter[str] = Counter()
    timing: Counter[str] = Counter()
    relation: Counter[str] = Counter()
    source_owner: Counter[str] = Counter()
    invalid: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []

    run_dirs = sorted(
        path
        for path in replay_root.iterdir()
        if path.is_dir() and (path / "decision" / "counterfactual.json").is_file()
    )
    for run_dir in run_dirs:
        problems = verify_counterfactual_integrity(run_dir)
        if problems:
            invalid.append({"run_dir": run_dir.name, "problems": problems})
            continue
        try:
            artifact = load_counterfactual_artifact(run_dir)
        except CounterfactualError as exc:
            invalid.append({"run_dir": run_dir.name, "problems": [str(exc)]})
            continue

        proposal = artifact.get("proposal") or {}
        disposition = str((proposal.get("disposition") or {}).get("code"))
        dispositions[disposition] += 1
        before = proposal.get("frozen_before_anchor")
        timing["PRE_ANCHOR" if before is True else "POST_ANCHOR"] += 1

        move = proposal.get("move")
        anchor = (artifact.get("anchor") or {}).get("move")
        if move is None:
            relation["NO_PROPOSAL"] += 1
        elif move == anchor:
            relation["PROPOSAL_EQUALS_ANCHOR"] += 1
        else:
            relation["PROPOSAL_DIFFERS_FROM_ANCHOR"] += 1

        owner = proposal.get("source_owner")
        if isinstance(owner, str):
            source_owner[owner] += 1

        rows.append(
            {
                "run_dir": run_dir.name,
                "run_id": (artifact.get("source") or {}).get("run_id"),
                "decision_id": artifact.get("decision_id"),
                "disposition": disposition,
                "proposal_move": move,
                "source_owner": owner,
                "frozen_before_anchor": before,
                "anchor_move": anchor,
                "would_change_outward_move": (
                    artifact.get("counterfactual") or {}
                ).get("would_change_outward_move"),
            }
        )

    summary = {
        "schema_version": 1,
        "replay_root": str(replay_root),
        "runs_with_decision_artifact": len(run_dirs),
        "valid_runs": len(rows),
        "invalid_runs": len(invalid),
        "dispositions": dict(sorted(dispositions.items())),
        "timing": dict(sorted(timing.items())),
        "anchor_relation": dict(sorted(relation.items())),
        "proposal_source_owner": dict(sorted(source_owner.items())),
        "rows": rows,
        "invalid": invalid,
        "claim": (
            "Descriptive counterfactual structure only. Proposal frequency, "
            "pre/post-anchor timing and anchor disagreement are not chess "
            "correctness or playing-strength measurements."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in summary.items() if k not in {"rows", "invalid"}}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
