#!/usr/bin/env python3
"""Independently validate M14-J J11 orchestration evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.orchestration_integrity import (
    ORCHESTRATION_PATH,
    verify_orchestration_integrity,
)


def _select_run(args: argparse.Namespace) -> Path:
    if args.run_dir is not None:
        return args.run_dir.resolve()
    root = args.replay_root.resolve()
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and (path / ORCHESTRATION_PATH).is_file()
    )
    if not candidates:
        raise ValueError(
            f"no replay containing {ORCHESTRATION_PATH} exists under {root}"
        )
    if args.latest:
        return candidates[-1]
    if len(candidates) != 1:
        raise ValueError(
            "multiple J11 replays found; pass --latest or --run-dir explicitly"
        )
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-dir", type=Path)
    source.add_argument("--replay-root", type=Path)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run_dir = _select_run(args)
    problems = verify_orchestration_integrity(
        run_dir,
        root=ROOT,
        expected_source_commit=args.expected_source_commit,
    )
    report = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "expected_source_commit": args.expected_source_commit,
        "qualified": not problems,
        "problems": problems,
        "claim_boundary": {
            "allocation_provenance": not problems,
            "outward_move": False,
            "hybrid_authority": False,
            "adaptive_stop_promoted": False,
            "generic_host_portability": False,
            "strength": False,
            "elo": False,
            "deployment": False,
        },
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"J11 orchestration validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
