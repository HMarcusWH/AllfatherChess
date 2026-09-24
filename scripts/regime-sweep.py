#!/usr/bin/env python3
"""Offline M14-F regime dataset extraction and optional support calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.regime_calibration import (
    build_regime_dataset,
    fit_regime_support_model,
    write_regime_dataset,
    write_regime_support_model,
)
from controller.regimes import (
    RegimeError,
    build_regime_observation_from_run,
    classify_regimes,
)


def _discover(inputs: list[Path]) -> list[Path]:
    runs: dict[str, Path] = {}
    for item in inputs:
        if (item / "manifest.json").is_file():
            runs[str(item.resolve())] = item
            continue
        if not item.is_dir():
            raise RegimeError(f"regime sweep input does not exist: {item}")
        for child in sorted(item.iterdir()):
            if child.is_dir() and (child / "manifest.json").is_file():
                runs[str(child.resolve())] = child
    if not runs:
        raise RegimeError("regime sweep found no replay bundles")
    return [runs[key] for key in sorted(runs)]


def _load_groups(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RegimeError("position-group mapping must be a JSON object")
    return {str(key): str(value) for key, value in payload.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "regimes")
    parser.add_argument(
        "--position-groups",
        type=Path,
        help="optional JSON mapping run_id -> position_group",
    )
    parser.add_argument("--fit", action="store_true")
    parser.add_argument(
        "--calibration-output",
        type=Path,
        default=ROOT / "build" / "regime-calibration",
    )
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--min-position-groups", type=int, default=2)
    args = parser.parse_args()

    runs = _discover(args.inputs)
    groups = _load_groups(args.position_groups)
    observations = []
    classifications = []
    skipped: list[dict[str, str]] = []
    for run_dir in runs:
        try:
            observation = build_regime_observation_from_run(run_dir)
        except RegimeError as exc:
            skipped.append({"run_dir": str(run_dir), "reason": str(exc)})
            continue
        observations.append(observation)
        classifications.append(classify_regimes(observation).as_dict())

    if not observations:
        raise RegimeError(
            "no input replay bundle produced an eligible M14-F observation"
        )

    dataset = build_regime_dataset(
        observations,
        position_groups=groups,
    )
    dataset_path = write_regime_dataset(dataset, args.output)
    summary_path = dataset_path.with_name("sweep.json")
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": dataset["dataset_id"],
                "classifications_without_domain_model": classifications,
                "skipped": skipped,
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(dataset_path)

    if args.fit:
        source_sha = hashlib.sha256(
            dataset_path.read_bytes()
        ).hexdigest()
        model = fit_regime_support_model(
            dataset,
            source_sha256=source_sha,
            min_support=args.min_support,
            min_position_groups=args.min_position_groups,
        )
        model_path = write_regime_support_model(
            model,
            args.calibration_output,
        )
        print(model_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RegimeError, OSError, json.JSONDecodeError) as exc:
        print(f"regime sweep failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
