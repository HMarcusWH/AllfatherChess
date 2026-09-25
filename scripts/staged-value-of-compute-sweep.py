#!/usr/bin/env python3
"""Build an M14-G1 staged VERIFY value-of-compute dataset from sealed runs.

This tool is deliberately offline. It never starts an engine. Each input must
already contain a completed base VERIFY artifact and a completed
same_process_staged_verify_v1 extension artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import load_manifest
from controller.staged_value_of_compute import (
    StagedValueOfComputeError,
    build_dataset,
    load_staged_transition,
    write_dataset,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument(
        "--dataset-root",
        type=Path,
        default=ROOT / "build" / "staged-value-of-compute",
    )
    p.add_argument(
        "--collection-output",
        type=Path,
        default=ROOT / "build" / "staged-value-of-compute" / "collection.json",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    run_dirs = sorted(Path(path) for path in args.runs)
    if not run_dirs:
        raise SystemExit("at least one sealed staged VERIFY run is required")

    counts: dict[str, int] = defaultdict(int)
    transitions = []
    entries = []
    seen_run_ids: set[str] = set()

    for run_dir in run_dirs:
        parent = load_manifest(run_dir)
        run_id = str(parent.get("run_id") or "")
        if not run_id:
            raise StagedValueOfComputeError(f"{run_dir}: parent run_id is missing")
        if run_id in seen_run_ids:
            raise StagedValueOfComputeError(
                f"duplicate staged evidence for run_id {run_id!r}"
            )
        seen_run_ids.add(run_id)

        position_id = str((parent.get("position") or {}).get("position_id") or "")
        if not position_id:
            raise StagedValueOfComputeError(
                f"{run_dir}: parent position_id is missing"
            )
        replicate = counts[position_id]
        counts[position_id] += 1

        transition = load_staged_transition(
            run_dir,
            position_group=position_id,
            replicate=replicate,
        )
        transitions.append(transition)
        entries.append(
            {
                "run_dir": str(run_dir),
                "run_id": transition.run_id,
                "position_group": transition.position_group,
                "replicate": transition.replicate,
                "transition": transition.transition_key,
                "feature_digest": transition.feature_digest,
                "label_digest": transition.label_digest,
                "decision_changed": transition.label_payload()["decision_changed"],
            }
        )

    dataset = build_dataset(transitions)
    dataset_path = write_dataset(dataset, args.dataset_root)
    collection = {
        "schema_version": 1,
        "input_runs": len(run_dirs),
        "position_groups": len(counts),
        "dataset_id": dataset["dataset_id"],
        "dataset_path": str(dataset_path),
        "rows": len(dataset["rows"]),
        "entries": entries,
        "claim": (
            "Offline same-process staged VERIFY decision-change evidence only. "
            "The dataset records whether the shared frozen policy changed after "
            "the declared extension; it does not label correctness, Elo, move "
            "quality, strength, or strategic utility."
        ),
    }
    args.collection_output.parent.mkdir(parents=True, exist_ok=True)
    args.collection_output.write_text(
        json.dumps(collection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "staged value-of-compute dataset: "
        f"rows={len(dataset['rows'])}, groups={len(counts)}, "
        f"dataset={dataset['dataset_id']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StagedValueOfComputeError, OSError, ValueError) as exc:
        print(f"staged value-of-compute sweep failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
