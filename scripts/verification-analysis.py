#!/usr/bin/env python3
"""Derive COMPARE / RELOCK analysis from finalized VERIFY replay children."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import discover_replay_bundles
from controller.verification_analysis import (
    VerificationAnalysisError,
    build_verification_analysis_artifact,
    write_verification_analysis_artifact,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--replay-root",
        type=Path,
        default=ROOT / "build" / "replays-verify",
    )
    value.add_argument(
        "--derived-root",
        type=Path,
        default=ROOT / "build" / "verification-derived",
    )
    value.add_argument("--top-k", type=int, default=3)
    value.add_argument(
        "--checkpoint-fractions",
        type=float,
        nargs="*",
        default=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    discovery = discover_replay_bundles(args.replay_root)
    runs = [
        run_dir
        for run_dir in discovery.bundles
        if (run_dir / "verification" / "manifest.json").is_file()
    ]
    if not runs:
        raise SystemExit(
            f"no finalized replay bundles with verification children under {args.replay_root}"
        )

    artifact = build_verification_analysis_artifact(
        runs,
        fractions=args.checkpoint_fractions,
        top_k=args.top_k,
    )
    path = write_verification_analysis_artifact(artifact, args.derived_root)

    eligible = sum(bool(run["analysis_eligible"]) for run in artifact.runs)
    statuses: dict[str, int] = {}
    patterns: dict[str, int] = {}
    for run in artifact.runs:
        status = run["relock"]["status"]
        statuses[status] = statuses.get(status, 0) + 1
        pattern = run["final_pattern"]
        patterns[pattern] = patterns.get(pattern, 0) + 1

    print(
        json.dumps(
            {
                "analysis_id": artifact.analysis_id,
                "path": str(path),
                "runs": len(artifact.runs),
                "analysis_eligible": eligible,
                "relock": statuses,
                "final_patterns": patterns,
                "claim": (
                    "Derived structural evidence only. RELOCK is descriptive and "
                    "does not certify chess correctness or authorize routing."
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationAnalysisError, OSError, ValueError) as exc:
        print(f"verification analysis failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
