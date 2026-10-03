#!/usr/bin/env python3
"""Independently qualify one retained LC0 matrix against a frozen selection."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from tools.engine_opt.domain import validate_execution_domain
from tools.engine_opt.matrix_qualification import (
    MatrixQualificationError,
    qualify_lc0_matrix,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixQualificationError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixQualificationError(f"{path}: cannot load JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path}: JSON root must be an object")
    return value


def find_one(root: Path, pattern: str) -> Path:
    found = sorted(root.glob(pattern))
    require(len(found) == 1, f"{pattern}: expected one file, found {len(found)}")
    return found[0]


def current_source() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--matrix-pattern",
        default="engine-opt-v2-profile-domain/**/matrix/canonical-lc0.json",
    )
    parser.add_argument(
        "--execution-domain-pattern",
        default="engine-opt-v2-profile-domain/**/execution-domain.json",
    )
    parser.add_argument(
        "--candidate-report-pattern",
        default="engine-opt-v2-profile-domain/**/candidate/report.json",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=ROOT / "qualification/engine-opt-v2-selection.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = current_source()
    report: dict[str, Any]
    try:
        root = args.root.resolve()
        selection_path = args.selection if args.selection.is_absolute() else ROOT / args.selection
        selection = load(selection_path)
        matrix = load(find_one(root, args.matrix_pattern))
        domain = validate_execution_domain(
            load(find_one(root, args.execution_domain_pattern)),
            expected_source_commit=source,
        )
        candidate_report = load(find_one(root, args.candidate_report_pattern))
        require(candidate_report.get("source_commit") == source, "candidate report source is not exact head")
        require(candidate_report.get("passed") is True, "candidate identity report did not pass")
        require(
            candidate_report.get("candidate_identity_valid") is True,
            "candidate identity is not valid",
        )
        candidate_bundle = candidate_report.get("candidate_bundle")
        require(isinstance(candidate_bundle, dict), "candidate bundle identity missing")
        result = qualify_lc0_matrix(
            matrix=matrix,
            selection=selection,
            expected_source_commit=source,
            expected_execution_domain=domain,
            candidate_bundle=candidate_bundle,
        )
        report = {
            "schema_version": 1,
            "profile_id": "engine-opt-v2-canonical-matrix",
            "qualification_scope": "canonical_lc0_matrix",
            "source_commit": source,
            "selection_profile_id": selection.get("profile_id"),
            "evidence_valid": True,
            "qualified": result["qualified"],
            "passed": result["qualified"],
            "qualification_failures": result["qualification_failures"],
            "invalid_evidence": [],
            "details": result["details"],
            "claim_boundary": {
                "canonical_matrix_qualified": result["qualified"],
                "candidate_overlay": False,
                "strength": False,
                "elo": False,
                "deployment": False,
            },
        }
    except Exception as exc:
        report = {
            "schema_version": 1,
            "profile_id": "engine-opt-v2-canonical-matrix",
            "qualification_scope": "canonical_lc0_matrix",
            "source_commit": source,
            "evidence_valid": False,
            "qualified": False,
            "passed": False,
            "qualification_failures": [],
            "invalid_evidence": [f"{type(exc).__name__}: {exc}"],
            "claim_boundary": {
                "canonical_matrix_qualified": False,
                "candidate_overlay": False,
                "strength": False,
                "elo": False,
                "deployment": False,
            },
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("evidence_valid") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
