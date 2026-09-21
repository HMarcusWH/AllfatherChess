#!/usr/bin/env python3
"""Derive and validate COMPARE / RELOCK from the real VERIFY contract evidence.

This consumes the exact raw run written by verification-execution-contract.py.
It is an analysis/provenance gate, not a strength gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import sha256_file
from controller.verification_analysis import (
    VerificationAnalysisError,
    build_verification_analysis_artifact,
    load_verification_analysis_artifact,
    load_verification_bundle,
    write_verification_analysis_artifact,
)


EXECUTION_REPORT = ROOT / "build" / "test-results" / "verify-execution" / "report.json"
REPLAY_ROOT = ROOT / "build" / "replays-verify"
RESULT_DIR = ROOT / "build" / "test-results" / "verify-analysis"
DERIVED_ROOT = ROOT / "build" / "verification-derived"


class ContractError(RuntimeError):
    pass


def raw_hashes(run_dir: Path) -> dict[str, str]:
    files = [run_dir / "manifest.json"]
    files.extend(
        path
        for path in run_dir.glob("*.jsonl")
        if path.is_file()
    )
    verify = run_dir / "verification"
    if verify.is_dir():
        files.append(verify / "manifest.json")
        files.extend(path for path in verify.glob("*.jsonl") if path.is_file())
    return {
        str(path.relative_to(run_dir)): sha256_file(path)
        for path in sorted(files)
        if path.is_file()
    }


def main() -> int:
    if not EXECUTION_REPORT.is_file():
        raise ContractError(
            "verification execution report is missing; run "
            "verification-execution-contract.py first"
        )
    report = json.loads(EXECUTION_REPORT.read_text(encoding="utf-8"))
    run_id = str(report["run_id"])
    run_dir = REPLAY_ROOT / run_id
    if not run_dir.is_dir():
        raise ContractError(f"VERIFY run directory does not exist: {run_dir}")

    before = raw_hashes(run_dir)
    bundle = load_verification_bundle(run_dir)
    if not bundle.analysis_eligible:
        raise ContractError("real VERIFY contract evidence is not three-way analysis eligible")

    first = build_verification_analysis_artifact([run_dir])
    second = build_verification_analysis_artifact([run_dir])
    if first.analysis_id != second.analysis_id:
        raise ContractError("same raw VERIFY evidence produced different analysis ids")

    run = first.runs[0]
    if len(run["final_pairwise"]) != 3:
        raise ContractError("COMPARE did not produce all three final pairwise relations")
    if any(item["support"] != 3 for item in run["final_pairwise"]):
        raise ContractError(f"COMPARE shared support is not exactly three: {run['final_pairwise']}")
    if len(run["explore_to_verify"]) != 3:
        raise ContractError("EXPLORE->VERIFY transition table does not contain three owners")
    if run["final_pattern"] not in {"unanimous", "two_one", "all_different"}:
        raise ContractError(f"unexpected final triad pattern: {run['final_pattern']!r}")
    if run["relock"]["status"] not in {"RELOCK_OBSERVED", "RELOCK_FAILED"}:
        raise ContractError(
            f"completed real VERIFY evidence produced undefined RELOCK: {run['relock']}"
        )
    candidates = set(run["candidate_roots"])
    if any(move not in candidates for move in run["final_leaders"].values()):
        raise ContractError(f"VERIFY final leader escaped candidate set: {run['final_leaders']}")

    path = write_verification_analysis_artifact(first, DERIVED_ROOT)
    loaded = load_verification_analysis_artifact(path)
    if loaded["analysis_id"] != first.analysis_id:
        raise ContractError("written analysis id differs from in-memory content address")

    after = raw_hashes(run_dir)
    if before != after:
        raise ContractError("offline COMPARE/RELOCK analysis mutated raw replay evidence")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "schema_version": 1,
        "run_id": run_id,
        "analysis_id": first.analysis_id,
        "analysis_path": str(path.relative_to(ROOT)),
        "candidate_roots": run["candidate_roots"],
        "final_pattern": run["final_pattern"],
        "relock": run["relock"],
        "pairwise_support": [item["support"] for item in run["final_pairwise"]],
        "raw_immutable": True,
        "claim": (
            "Derived descriptive evidence only. The contract requires reconstruction, "
            "common support and deterministic RELOCK classification; it does not "
            "require agreement, correctness, useful complementarity, or strength."
        ),
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "VERIFY analysis contract passed: "
        f"analysis={first.analysis_id}, pattern={run['final_pattern']}, "
        f"relock={run['relock']['status']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, VerificationAnalysisError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"VERIFY analysis contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
