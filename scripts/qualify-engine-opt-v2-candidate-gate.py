#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.candidate_gate import (  # noqa: E402
    CandidateGateError,
    compare_canonical_surface,
    evaluate_gate,
)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CandidateGateError(f"{path}: JSON root must be object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--host-binding", type=Path, required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.base_sha:
            surface = compare_canonical_surface(args.repo_root.resolve(), args.base_sha)
        else:
            # workflow_dispatch has no PR base. That path can still pass when
            # canonical qualifies normally, but may not use the unmatched-host
            # diagnostic exception without a comparison anchor.
            surface = {
                "base_sha": None,
                "head_sha": None,
                "unchanged": False,
                "changed_files": [],
                "changed_trees": [],
                "comparison_available": False,
            }
        report = evaluate_gate(
            canonical=load(args.canonical),
            candidate=load(args.candidate),
            host_binding=load(args.host_binding),
            canonical_surface=surface,
        )
    except (CandidateGateError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ENGINE-OPT-V2 final candidate gate FAILED: {exc}")
        return 2

    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
