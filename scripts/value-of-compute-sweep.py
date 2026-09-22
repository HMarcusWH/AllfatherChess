#!/usr/bin/env python3
"""Collect prospective VERIFY value-of-compute evidence over the frozen corpus.

Each budget arm is a real completed controller run. The script never synthesizes
an engine bestmove by truncating a longer trajectory.

This is research collection, not a strength benchmark. The LC0 profile remains
backend-light/random and no label in the resulting dataset is chess correctness.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import discover_replay_bundles
from controller.value_of_compute import (
    ValueOfComputeError,
    build_dataset,
    load_budget_point,
    write_dataset,
)
from tests.harness.uci_session import UciError, UciSession


BASE_CONFIG = ROOT / "config" / "allfather.value.validation.json"
CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
DEFAULT_CASES = (
    "startpos",
    "kiwipete_castling",
    "perft_position_3",
    "perft_position_4",
    "perft_position_5",
    "perft_position_6",
    "en_passant_available",
    "promotion_available",
    "side_in_check",
    "history_ruy_lopez",
)
DEFAULT_BUDGETS = (64, 128, 256, 512)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=BASE_CONFIG)
    p.add_argument("--budgets", nargs="+", type=int, default=list(DEFAULT_BUDGETS))
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--movetime-ms", type=int, default=3000)
    p.add_argument("--cases", nargs="*", default=list(DEFAULT_CASES))
    p.add_argument(
        "--replay-root",
        type=Path,
        default=ROOT / "build" / "replays-value-of-compute",
    )
    p.add_argument(
        "--dataset-root",
        type=Path,
        default=ROOT / "build" / "value-of-compute",
    )
    p.add_argument(
        "--collection-output",
        type=Path,
        default=ROOT / "build" / "value-of-compute" / "collection.json",
    )
    return p


def _position_payload(position: dict) -> dict:
    if "fen" in position:
        return {"fen": position["fen"]}
    return {"startpos_moves": list(position.get("startpos_moves", []))}


def _arm_config(
    base: dict,
    *,
    nodes: int,
    replay_root: Path,
    path: Path,
) -> Path:
    if nodes < 1:
        raise ValueOfComputeError("VERIFY budgets must be positive")
    doc = json.loads(json.dumps(base))
    doc["root"] = str(ROOT)
    doc["shadow"]["replay_root"] = str(replay_root.resolve())
    doc["verification"]["dispatch_limit"] = {"nodes": int(nodes)}
    # This experiment isolates VERIFY. A downstream REFINE branch whose
    # execution depends on VERIFY disagreement would confound the intervention.
    doc.pop("refinement", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _wait_new_run(
    replay_root: Path,
    known: set[str],
    *,
    timeout: float = 30.0,
) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        discovery = discover_replay_bundles(replay_root)
        candidates = [
            path
            for path in discovery.bundles
            if path.name not in known
            and (path / "decision" / "counterfactual.json").is_file()
        ]
        if candidates:
            return candidates[-1]
        time.sleep(0.05)
    raise ValueOfComputeError("timed out waiting for counterfactual arm artifact")


def _run_arm(
    *,
    config_path: Path,
    replay_root: Path,
    position: dict,
    movetime_ms: int,
) -> Path:
    discovery = discover_replay_bundles(replay_root)
    known = {path.name for path in discovery.bundles} | {
        item.path.name for item in discovery.skipped
    }
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=60.0,
        args=["-m", "controller", "--config", str(config_path)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position(_position_payload(position))
        shell.send(f"go movetime {movetime_ms}")
        shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="value-of-compute outward bestmove",
            timeout=max(90.0, movetime_ms / 1000.0 + 30.0),
        )
        # Keep the controller process alive while shadow VERIFY / cross-feed /
        # counterfactual finalization drains after an early anchor bestmove.
        return _wait_new_run(replay_root, known)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    budgets = sorted(set(args.budgets))
    if len(budgets) < 2 or any(value < 1 for value in budgets):
        raise SystemExit("value-of-compute sweep requires at least two positive budgets")
    if args.repeats < 1:
        raise SystemExit("--repeats must be positive")

    base = json.loads(args.config.read_text(encoding="utf-8"))
    corpus = {
        case["id"]: case["position"]
        for case in json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
        if not case.get("terminal")
    }
    missing = [case for case in args.cases if case not in corpus]
    if missing:
        raise SystemExit(f"unknown/nonterminal corpus cases: {missing}")

    replay_root = args.replay_root.resolve()
    replay_root.mkdir(parents=True, exist_ok=True)
    config_root = ROOT / "build" / "value-of-compute" / "configs"
    entries: list[dict] = []
    points = []
    ineligible: list[dict] = []
    started = time.monotonic()

    for repeat in range(args.repeats):
        for case_id in args.cases:
            arm_points = []
            for nodes in budgets:
                config_path = _arm_config(
                    base,
                    nodes=nodes,
                    replay_root=replay_root,
                    path=config_root / f"n{nodes}.json",
                )
                run_dir = _run_arm(
                    config_path=config_path,
                    replay_root=replay_root,
                    position=corpus[case_id],
                    movetime_ms=args.movetime_ms,
                )
                point = load_budget_point(
                    run_dir,
                    position_group=case_id,
                    replicate=repeat,
                )
                arm_points.append(point)
                points.append(point)
                entries.append(
                    {
                        "case": case_id,
                        "position_group": case_id,
                        "replicate": repeat,
                        "verify_nodes": nodes,
                        "run_dir": str(run_dir),
                        "run_id": point.run_id,
                        "upstream_fingerprint": point.upstream_fingerprint,
                        "feature_digest": point.feature_digest,
                    }
                )

            fingerprints = {point.upstream_fingerprint for point in arm_points}
            if len(fingerprints) != 1:
                ineligible.append(
                    {
                        "case": case_id,
                        "replicate": repeat,
                        "reason": "upstream fingerprint changed across VERIFY budget arms",
                        "arms": [
                            {
                                "nodes": point.verify_nodes,
                                "run_id": point.run_id,
                                "upstream_fingerprint": point.upstream_fingerprint,
                            }
                            for point in arm_points
                        ],
                    }
                )

    # The dataset builder groups only matching upstream fingerprints, so an
    # ineligible arm family is recorded rather than cross-paired.
    dataset = build_dataset(points)
    dataset_path = write_dataset(dataset, args.dataset_root)

    collection = {
        "schema_version": 1,
        "base_config": str(args.config),
        "budgets": budgets,
        "cases": list(args.cases),
        "repeats": args.repeats,
        "movetime_ms": args.movetime_ms,
        "elapsed_s": round(time.monotonic() - started, 3),
        "entries": entries,
        "ineligible_upstream_groups": ineligible,
        "dataset_id": dataset["dataset_id"],
        "dataset_path": str(dataset_path),
        "transition_count": len(dataset["transitions"]),
        "claim": (
            "Prospective mechanism evidence only. Every budget point is a real "
            "completed VERIFY run. decision_changed records controller decision "
            "change, not chess correctness or Elo improvement."
        ),
    }
    args.collection_output.parent.mkdir(parents=True, exist_ok=True)
    args.collection_output.write_text(
        json.dumps(collection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "VERIFY value-of-compute sweep: "
        f"arms={len(entries)}, transitions={len(dataset['transitions'])}, "
        f"ineligible_groups={len(ineligible)}, dataset={dataset['dataset_id']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UciError, ValueOfComputeError, OSError, ValueError) as exc:
        print(f"value-of-compute sweep failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
