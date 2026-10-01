#!/usr/bin/env python3
"""Build the J10 allocation-calibration promotion report.

The current source-controlled seed corpus is intentionally below the frozen
independent-group threshold.  This script therefore emits a BUY-only promotion
report instead of fitting or pretending to fit a STOP policy.  Once a future
corpus reaches the threshold, the existing staged-decision and regime-support
fitters can be invoked on leakage-free grouped datasets before this artifact is
updated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "qualification/j10-calibration-corpus-v1.json"
POLICY = ROOT / "qualification/adaptive-resource-allocation-v1.json"
OUTPUT = ROOT / "build/j10-calibration/promotion-report.json"


class CalibrationBuildError(ValueError):
    pass


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    groups = int(corpus["independent_groups"])
    minimum = int(
        corpus["minimum_independent_groups_for_stop_promotion"]
    )
    promotion = policy.get("promotion") or {}

    if groups >= minimum and not corpus.get("labels_frozen"):
        raise CalibrationBuildError(
            "independence threshold reached but staged labels are not frozen; "
            "fit remains forbidden"
        )
    if bool(promotion.get("stop_promotion")):
        if groups < minimum:
            raise CalibrationBuildError(
                "STOP promotion enabled below independent-group minimum"
            )
        raise CalibrationBuildError(
            "source-controlled STOP promotion requires separately fitted, "
            "held-out-validated staged and regime models"
        )

    report = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "corpus_id": corpus["corpus_id"],
        "independent_groups": groups,
        "minimum_independent_groups": minimum,
        "staged_labels_frozen": bool(corpus.get("labels_frozen")),
        "stop_promotion": False,
        "runtime_behavior": "BUY_BUNDLE_FAIL_CLOSED",
        "reason": promotion.get("reason"),
        "next_required_evidence": [
            "freeze >=32 independent source groups before outcomes",
            "collect same-process n16->n32 staged labels",
            "split by source_group, never by replay/position id",
            "fit staged decision-change model",
            "fit regime support model",
            "require held-out observed serving buckets before STOP promotion"
        ],
        "claim_boundary": {
            "allocator_mechanism": True,
            "adaptive_stop_promoted": False,
            "move_quality": False,
            "elo": False,
            "strength": False,
            "deployment": False
        }
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CalibrationBuildError, OSError, KeyError, ValueError) as exc:
        print(f"J10 calibration build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
