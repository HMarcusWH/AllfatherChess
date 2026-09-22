#!/usr/bin/env python3
"""Fit the prospective VERIFY decision-change calibration model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.decision_calibration import (
    DecisionCalibrationError,
    fit_from_dataset_path,
    write_decision_calibration,
)
from controller.value_of_compute import ValueOfComputeError


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "build" / "decision-calibration",
    )
    p.add_argument("--min-support", type=int, default=2)
    p.add_argument("--smoothing-alpha", type=float, default=1.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    model = fit_from_dataset_path(
        args.dataset,
        min_support=args.min_support,
        smoothing_alpha=args.smoothing_alpha,
    )
    path = write_decision_calibration(model, args.output_root)
    summary = {
        "model_id": model.model_id,
        "model_kind": model.model_kind,
        "dataset_id": model.dataset_id,
        "path": str(path),
        "min_support": model.min_support,
        "buckets": len(model.buckets),
        "evaluation": model.evaluation,
        "claim": (
            "Calibration estimates only the probability that additional VERIFY "
            "budget changes the frozen counterfactual decision in the observed "
            "domain. It is not a correctness, Elo, or move-quality estimate."
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DecisionCalibrationError, ValueOfComputeError, OSError, ValueError) as exc:
        print(f"decision calibration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
