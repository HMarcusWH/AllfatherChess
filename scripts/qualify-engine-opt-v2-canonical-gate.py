#!/usr/bin/env python3
"""Fail-closed canonical admission; preserves the historical b7->b4 gate."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.engine_opt.promotion_gate import (
    PromotionGateError, _base_json, _load, compare_promotion_surface,
    evaluate_promotion_gate, require,
)
from tools.engine_opt.continuity_gate import (
    B4, compare_continuity_surface, evaluate_continuity_gate,
)

B7 = "b7-p8-c256k-warm64"
AUTHORIZED_MANUAL_BASE = "0229b34b1c173388c31eacf9d75765eeddfa1588"


def seal(doc: dict) -> dict:
    result = dict(doc)
    result.pop("content_sha256", None)
    binary = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    result["content_sha256"] = hashlib.sha256(binary).hexdigest()
    return result


def select_mode(base: dict, head: dict) -> str:
    old = ((base.get("selected") or {}).get("lc0") or {}).get("matrix_profile")
    new = ((head.get("selected") or {}).get("lc0") or {}).get("matrix_profile")
    if (old, new) == (B7, B4):
        return "promotion"
    if (old, new) == (B4, B4):
        return "continuity"
    raise PromotionGateError(f"unrecognized canonical transition {old!r} -> {new!r}")


def evaluate(*, aggregate: dict, host_binding: dict, catalog: dict,
             root: Path, base_sha: str, event_name: str) -> dict:
    require(event_name in ("pull_request", "workflow_dispatch"),
            "unsupported canonical admission event")
    require(len(base_sha) == 40 and all(x in "0123456789abcdef" for x in base_sha.lower()),
            "missing or malformed canonical base SHA")
    if event_name == "workflow_dispatch":
        require(base_sha == AUTHORIZED_MANUAL_BASE,
                "manual base must be the immutable PR #64 b4 promotion commit")
    base = _base_json(root, base_sha, "qualification/engine-opt-v2-selection.json")
    head = _load(root / "qualification/engine-opt-v2-selection.json")
    mode = select_mode(base, head)
    if event_name == "workflow_dispatch":
        require(mode == "continuity",
                "manual dispatch cannot issue historical promotion")
    if mode == "promotion":
        surface = compare_promotion_surface(root, base_sha)
        return evaluate_promotion_gate(aggregate=aggregate, host_binding=host_binding,
                                       catalog=catalog, promotion_surface=surface,
                                       current_head=surface["head_sha"])
    surface = compare_continuity_surface(root, base_sha)
    return evaluate_continuity_gate(aggregate=aggregate, surface=surface,
                                    current_head=surface["head_sha"])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aggregate", type=Path, required=True)
    p.add_argument("--host-binding", type=Path, required=True)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--event-name", required=True)
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    aggregate = None
    failed = False
    try:
        aggregate = _load(a.aggregate)
        report = evaluate(aggregate=aggregate, host_binding=_load(a.host_binding),
                          catalog=_load(a.catalog), root=a.repo_root.resolve(),
                          base_sha=a.base_sha, event_name=a.event_name)
    except (PromotionGateError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        failed = True
        report = {
            "schema_version": 1, "passed": False,
            "disposition": "BLOCKED_FAIL_CLOSED",
            "failure": {"type": type(exc).__name__, "message": str(exc)},
            "aggregate_disposition": aggregate.get("qualification_disposition") if aggregate else None,
            "promotion_surface": {"base_sha": a.base_sha,
                                  "authorized_profile_change": False,
                                  "canonical_b4_continuity_verified": False},
            "claim_boundary": {
                "canonical_b4_continuity": False, "canonical_b4_promotion": False,
                "runtime_profile_selection": False, "adaptive_stop_promoted": False,
                "generic_host_portability": False, "strength": False, "elo": False,
                "equal_compute": False, "deployment": False,
            },
        }
    report = seal(report)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(report, sort_keys=True))
    if failed:
        print("ENGINE-OPT-V2 canonical admission BLOCKED: " + report["failure"]["message"],
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
