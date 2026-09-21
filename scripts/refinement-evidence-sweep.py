#!/usr/bin/env python3
"""Collect shadow REFINE mechanism evidence over the frozen validation corpus.

This is a research sweep, not a CI gate and not a strength experiment. The
validation LC0 profile is backend-light/random and REFINE intentionally runs
outside the active equal-resource envelope.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.refinement import (
    load_refinement_manifest,
    verify_refinement_integrity,
)
from controller.replay import discover_replay_bundles
from controller.runtime import load_runtime_config
from controller.verification import load_verification_manifest
from tests.harness.uci_session import UciError, UciSession


CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
RESULT_DIR = ROOT / "build" / "test-results" / "refine-sweep"
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


def position_payload(position: dict) -> dict:
    if "fen" in position:
        return {"fen": position["fen"]}
    return {"startpos_moves": list(position.get("startpos_moves", []))}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "allfather.refine.validation.json",
    )
    p.add_argument("--movetime-ms", type=int, default=5000)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--cases", nargs="*", default=list(DEFAULT_CASES))
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_runtime_config(args.config)
    if (
        config.shadow is None
        or config.verification is None
        or config.refinement is None
    ):
        raise SystemExit("REFINE sweep requires shadow + VERIFY + REFINE profile")

    corpus = {
        case["id"]: case["position"]
        for case in json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
    }
    missing = [case for case in args.cases if case not in corpus]
    if missing:
        raise SystemExit(f"unknown corpus cases: {missing}")

    replay_root = config.shadow.replay_root
    replay_root.mkdir(parents=True, exist_ok=True)
    before = discover_replay_bundles(replay_root)
    known = {path.name for path in before.bundles} | {
        item.path.name for item in before.skipped
    }

    outward: list[dict] = []
    started = time.monotonic()
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=60.0,
        args=["-m", "controller", "--config", str(args.config)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        for repeat in range(max(1, args.repeats)):
            for case_id in args.cases:
                shell.new_game()
                shell.set_position(position_payload(corpus[case_id]))
                shell.send(f"go movetime {args.movetime_ms}")
                lines = shell.read_until(
                    lambda line: line.startswith("bestmove "),
                    label=f"REFINE sweep bestmove for {case_id}",
                    timeout=90.0,
                )
                move = next(
                    line.split()[1] for line in lines if line.startswith("bestmove ")
                )
                outward.append(
                    {"case": case_id, "repeat": repeat, "bestmove": move}
                )

    discovery = discover_replay_bundles(replay_root)
    new_runs = [path for path in discovery.bundles if path.name not in known]
    refinement_runs = [
        path
        for path in new_runs
        if (path / "refinement" / "manifest.json").is_file()
    ]

    dispositions: dict[str, int] = {}
    target_count_hist: dict[str, int] = {}
    target_status: dict[str, int] = {}
    child_counts: list[int] = []
    stage_ms: list[float] = []
    terminal_targets = 0
    valid_runs = 0
    invalid: list[dict] = []
    verify_patterns: dict[str, int] = {}

    for run_dir in refinement_runs:
        problems = verify_refinement_integrity(run_dir)
        if problems:
            invalid.append({"run_id": run_dir.name, "problems": problems})
            continue
        valid_runs += 1
        refinement = load_refinement_manifest(run_dir)
        verification = load_verification_manifest(run_dir)
        disposition = refinement["disposition"]["run"]
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        target_count = len(refinement["nomination"]["targets"])
        target_count_hist[str(target_count)] = target_count_hist.get(str(target_count), 0) + 1

        finals = [
            stage["bestmove"]
            for stage in verification.get("stages", [])
            if stage.get("disposition") == "completed"
        ]
        unique = len(set(finals))
        pattern = (
            "unanimous"
            if unique == 1 and len(finals) == 3
            else "two_one"
            if unique == 2 and len(finals) == 3
            else "all_different"
            if unique == 3 and len(finals) == 3
            else "undefined"
        )
        verify_patterns[pattern] = verify_patterns.get(pattern, 0) + 1

        for target in refinement["targets"]:
            status = target["disposition"]["target"]
            target_status[status] = target_status.get(status, 0) + 1
            children = target["child_oracle"]["children"]
            child_counts.append(len(children))
            if target["child_oracle"]["terminal"]:
                terminal_targets += 1
            for stage in target["stages"]:
                if (
                    stage.get("completed_ms") is not None
                    and stage.get("dispatched_ms") is not None
                ):
                    stage_ms.append(
                        max(
                            0.0,
                            float(stage["completed_ms"])
                            - float(stage["dispatched_ms"]),
                        )
                    )

    report = {
        "schema_version": 1,
        "config": str(args.config.relative_to(ROOT)),
        "movetime_ms": args.movetime_ms,
        "cases": list(args.cases),
        "repeats": args.repeats,
        "elapsed_s": round(time.monotonic() - started, 3),
        "outward_moves": outward,
        "runs_attempted": len(new_runs),
        "refinement_artifacts": len(refinement_runs),
        "valid_refinement_runs": valid_runs,
        "invalid_refinement_runs": invalid,
        "verify_final_patterns": verify_patterns,
        "refinement_dispositions": dispositions,
        "target_count_histogram": target_count_hist,
        "target_dispositions": target_status,
        "terminal_targets": terminal_targets,
        "median_child_count": (
            None if not child_counts else statistics.median(child_counts)
        ),
        "median_refine_stage_ms": (
            None if not stage_ms else statistics.median(stage_ms)
        ),
        "skipped_replay_directories": [
            item.as_dict()
            for item in discovery.skipped
            if item.path.name not in known
        ],
        "claim": (
            "Mechanism evidence only. The validation LC0 profile is not "
            "strength-qualified and REFINE runs outside the active equal-resource "
            "envelope. Counts describe orchestration, not move correctness or value."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "sweep.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "REFINE evidence sweep: "
        f"runs={len(new_runs)}, artifacts={len(refinement_runs)}, "
        f"valid={valid_runs}, patterns={verify_patterns}, targets={target_count_hist}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UciError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"REFINE evidence sweep failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
