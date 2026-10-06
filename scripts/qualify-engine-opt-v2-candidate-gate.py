#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

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


def _sealed(report: dict[str, Any]) -> dict[str, Any]:
    core = dict(report)
    core.pop("content_sha256", None)
    encoded = json.dumps(
        core,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    core["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    return core


def write_report(path: Path, report: dict[str, Any]) -> None:
    sealed = _sealed(report)
    payload = json.dumps(sealed, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    print(payload, end="")


def _candidate_snapshot(doc: dict[str, Any] | None) -> dict[str, Any]:
    doc = doc or {}
    details = doc.get("details") or {}
    domain = details.get("execution_domain") or {}
    return {
        "qualified": doc.get("candidate_qualified"),
        "promotion_ready": doc.get("promotion_ready"),
        "passed": doc.get("passed"),
        "qualification_disposition": doc.get("qualification_disposition"),
        "qualification_failures": doc.get("qualification_failures") or [],
        "execution_domain_digest": domain.get("execution_domain_digest"),
    }


def _canonical_snapshot(doc: dict[str, Any] | None) -> dict[str, Any]:
    doc = doc or {}
    details = doc.get("details") or {}
    domain = details.get("execution_domain") or {}
    return {
        "qualified": doc.get("qualified"),
        "passed": doc.get("passed"),
        "qualification_failures": doc.get("qualification_failures") or [],
        "execution_domain_digest": domain.get("execution_domain_digest"),
    }


def blocked_report(
    exc: Exception,
    *,
    canonical: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    canonical_surface: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "passed": False,
        "disposition": "BLOCKED_FAIL_CLOSED",
        "failure": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "candidate": _candidate_snapshot(candidate),
        "canonical": _canonical_snapshot(canonical),
        "canonical_surface": canonical_surface,
        "claim_boundary": {
            "canonical_b4_promotion": False,
            "canonical_unmatched_host_requalified": False,
            "generic_host_portability_established": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--host-binding", type=Path, required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    canonical_doc: dict[str, Any] | None = None
    candidate_doc: dict[str, Any] | None = None
    canonical_surface: dict[str, Any] = {
        "base_sha": args.base_sha or None,
        "head_sha": None,
        "unchanged": False,
        "changed_files": [],
        "changed_trees": [],
        "comparison_available": False,
    }
    try:
        canonical_doc = load(args.canonical)
        candidate_doc = load(args.candidate)
        host_binding_doc = load(args.host_binding)
        if args.base_sha:
            canonical_surface = compare_canonical_surface(args.repo_root.resolve(), args.base_sha)
            canonical_surface["comparison_available"] = True
        report = evaluate_gate(
            canonical=canonical_doc,
            candidate=candidate_doc,
            host_binding=host_binding_doc,
            canonical_surface=canonical_surface,
        )
    except (CandidateGateError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        report = blocked_report(
            exc,
            canonical=canonical_doc,
            candidate=candidate_doc,
            canonical_surface=canonical_surface,
        )
        write_report(args.output, report)
        print(f"ENGINE-OPT-V2 final candidate gate FAILED: {exc}", file=sys.stderr)
        return 2

    write_report(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
