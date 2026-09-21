#!/usr/bin/env python3
"""Build derived residual features and calibrated reversal-risk models.

Pipeline:

```text
replays/<run_id>/        raw evidence          (written by the controller)
derived/<derived_id>/    residual features     (this script, --derive)
calibration/<model_id>/  calibrated risk model (this script, --fit)
```

Raw bundles are never modified. Every derived artifact records the exact run
ids and content hashes it was produced from, and every calibration records the
derived artifacts it was fitted from.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.calibration import (
    CalibrationError,
    ReversalRiskModel,
    training_rows_from_derived,
    write_calibration,
)
from controller.replay import discover_replay_bundles, sha256_file
from controller.residuals import (
    FeatureExtractionError,
    build_derived_artifact,
    load_derived_artifact,
    write_derived_artifact,
)
from controller.runtime import load_runtime_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "allfather.shadow.validation.json",
        help="runtime config used to locate the replay root",
    )
    parser.add_argument("--replay-root", type=Path, default=None)
    parser.add_argument("--derive", action="store_true", help="extract residual features")
    parser.add_argument("--fit", action="store_true", help="fit a reversal-risk calibration")
    parser.add_argument("--derived-id", default=None)
    parser.add_argument("--min-support", type=int, default=25)
    parser.add_argument(
        "--horizon-fraction",
        type=float,
        default=None,
        help="default 0.25 when deriving; when fitting alone it is read from the artifact",
    )
    parser.add_argument("--top-k", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.derive and not args.fit:
        args.derive = args.fit = True

    if args.replay_root is not None:
        replay_root = args.replay_root.resolve()
    else:
        config = load_runtime_config(args.config)
        if config.shadow is None:
            raise SystemExit("the supplied config declares no shadow replay root")
        replay_root = config.shadow.replay_root

    base = replay_root.parent
    derived_root = base / "derived"
    calibration_root = base / "calibration"

    derived_path: Path | None = None
    if args.derive:
        discovery = discover_replay_bundles(replay_root)
        runs = list(discovery.bundles)
        for skipped in discovery.skipped:
            print(
                f"skipping replay directory {skipped.path.name}: {skipped.reason}",
                file=sys.stderr,
            )
        if not runs:
            raise SystemExit(f"no finalized replay runs under {replay_root}")
        artifact = build_derived_artifact(
            runs,
            top_k=args.top_k,
            horizon_fraction=0.25 if args.horizon_fraction is None else args.horizon_fraction,
            derived_id=args.derived_id,
        )
        derived_path = write_derived_artifact(artifact, derived_root)
        print(
            f"derived {artifact.derived_id} from {len(runs)} replay run(s) -> "
            f"{derived_path.relative_to(ROOT) if derived_path.is_relative_to(ROOT) else derived_path}"
        )

    if args.fit:
        if derived_path is None:
            candidates = sorted(derived_root.glob("*/features.json"))
            if not candidates:
                raise SystemExit(f"no derived artifacts under {derived_root}")
            if args.derived_id:
                # Directory names are content hashes, so the greatest by string
                # order is neither the newest nor the one that was asked for.
                # An explicitly named artifact is resolved, or the run fails.
                wanted = args.derived_id
                matches = [
                    path
                    for path in candidates
                    if path.parent.name in (wanted, f"derived-{wanted}")
                ]
                if not matches:
                    available = ", ".join(sorted(path.parent.name for path in candidates))
                    raise SystemExit(
                        f"--derived-id {wanted!r} matches no artifact under {derived_root}; "
                        f"available: {available}"
                    )
                derived_path = matches[0]
            else:
                # No id given: take the most recently written artifact and say
                # which one, so training data is never selected silently.
                derived_path = max(candidates, key=lambda path: path.stat().st_mtime)
                if len(candidates) > 1:
                    print(
                        f"fitting from the most recent of {len(candidates)} derived "
                        f"artifacts: {derived_path.parent.name} "
                        f"(pass --derived-id to choose another)"
                    )
        derived = load_derived_artifact(derived_path)
        # The labels were produced with the artifact's horizon. Recording the
        # CLI default instead would make the model's id and provenance describe
        # a horizon its own labels never used.
        artifact_horizon = float(derived.get("parameters", {}).get("horizon_fraction", 0.25))
        if args.horizon_fraction is not None and args.horizon_fraction != artifact_horizon:
            raise SystemExit(
                f"--horizon-fraction {args.horizon_fraction} does not match the derived "
                f"artifact's {artifact_horizon}; re-derive or drop the flag"
            )
        rows = training_rows_from_derived(derived)
        if not rows:
            raise SystemExit(
                "no labelled rows: replay evidence contains no checkpoint with both a "
                "leader and an observable later horizon"
            )
        model = ReversalRiskModel.fit(
            rows,
            min_support=args.min_support,
            horizon_fraction=artifact_horizon,
            sources=[
                {
                    "derived_id": derived["derived_id"],
                    "path": str(derived_path.parent.name),
                    "sha256": sha256_file(derived_path),
                }
            ],
        )
        path = write_calibration(model, calibration_root)
        evaluation = model.evaluation
        print(
            f"calibrated {model.model_id}: {len(rows)} rows, "
            f"{len(model.buckets)} buckets, "
            f"train={evaluation.get('train_rows')} test={evaluation.get('test_rows')}, "
            f"brier={evaluation.get('brier_score')}, "
            f"in-domain={evaluation.get('in_domain_rate')} -> "
            f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}"
        )
        in_domain = sum(1 for record in model.buckets.values() if record["support"] >= model.min_support)
        if in_domain == 0:
            print(
                "NOTE: every bucket is below the declared support floor, so this model "
                "is out-of-domain everywhere and can only license conservative actions.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CalibrationError, FeatureExtractionError, OSError, ValueError) as exc:
        print(f"residual calibration failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
