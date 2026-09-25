#!/usr/bin/env python3
"""Fit the M14-G1 same-process staged VERIFY decision-change model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.staged_decision_calibration import (
    StagedDecisionCalibrationError,
    fit_from_path,
    write_staged_decision_calibration,
)
from controller.staged_value_of_compute import StagedValueOfComputeError


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "build" / "staged-decision-calibration",
    )
    p.add_argument("--min-support", type=int, default=5)
    p.add_argument("--min-position-groups", type=int, default=3)
    p.add_argument("--smoothing-alpha", type=float, default=1.0)
    p.add_argument("--prior-change-probability", type=float, default=1.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    model = fit_from_path(
        args.dataset,
        min_support=args.min_support,
        min_position_groups=args.min_position_groups,
        smoothing_alpha=args.smoothing_alpha,
        prior_change_probability=args.prior_change_probability,
    )
    path = write_staged_decision_calibration(model, args.output_root)
    summary = {
        "model_id": model.model_id,
        "model_kind": model.model_kind,
        "dataset_id": model.dataset_id,
        "path": str(path),
        "min_support": model.min_support,
        "min_position_groups": model.min_position_groups,
        "buckets": len(model.buckets),
        "evaluation": model.evaluation,
        "claim": (
            "Calibration estimates only the probability that the declared "
            "same-process staged VERIFY extension changes the shared frozen "
            "decision policy in-domain. It is not a correctness, Elo, strength, "
            "move-quality, or strategic-utility estimate."
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        StagedDecisionCalibrationError,
        StagedValueOfComputeError,
        OSError,
        ValueError,
    ) as exc:
        print(f"staged decision calibration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
