#!/usr/bin/env python3
"""Collect shadow replay evidence across the frozen position corpus.

This is a research data-collection tool, not a gate. Shadow mode deliberately
runs an unrestricted anchor alongside three restricted workers, so a sweep
consumes more compute than the eventual competitive envelope permits. Nothing
it produces is a strength claim.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import discover_replay_bundles, load_manifest
from controller.runtime import load_runtime_config
from tests.harness.uci_session import UciError, UciSession


CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
RESULT_DIR = ROOT / "build" / "test-results" / "shadow-sweep"

#: Terminal positions are included on purpose: an empty legal-root universe is a
#: case the controller must handle, not one to avoid.
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
    "checkmate_terminal",
    "stalemate_terminal",
)


def position_payload(position: dict) -> dict:
    if "fen" in position:
        return {"fen": position["fen"]}
    return {"startpos_moves": list(position.get("startpos_moves", []))}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config" / "allfather.shadow.validation.json"
    )
    parser.add_argument("--movetime-ms", type=int, default=800)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--cases", nargs="*", default=list(DEFAULT_CASES))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_runtime_config(args.config)
    if config.shadow is None:
        raise SystemExit("the supplied config declares no shadow mode")
    corpus = {case["id"]: case["position"] for case in json.loads(CORPUS_PATH.read_text())["cases"]}
    missing = [case for case in args.cases if case not in corpus]
    if missing:
        raise SystemExit(f"unknown corpus cases: {missing}")

    replay_root = config.shadow.replay_root
    replay_root.mkdir(parents=True, exist_ok=True)
    before = discover_replay_bundles(replay_root)
    known = {path.name for path in before.bundles} | {
        skipped.path.name for skipped in before.skipped
    }

    started = time.monotonic()
    collected: list[dict] = []
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
                    label=f"bestmove for {case_id}",
                    timeout=90.0,
                )
                bestmove = next(
                    line.split()[1] for line in lines if line.startswith("bestmove ")
                )
                collected.append({"case": case_id, "repeat": repeat, "bestmove": bestmove})

    discovery = discover_replay_bundles(replay_root)
    runs = [path for path in discovery.bundles if path.name not in known]
    skipped = [item for item in discovery.skipped if item.path.name not in known]
    summary = {
        "schema_version": 1,
        "config": str(args.config.relative_to(ROOT)),
        "movetime_ms": args.movetime_ms,
        "cases": list(args.cases),
        "repeats": args.repeats,
        "elapsed_s": round(time.monotonic() - started, 3),
        "outward_moves": collected,
        "runs": [],
        "skipped_replay_directories": [item.as_dict() for item in skipped],
        "claim": (
            "MEASURED shadow observations only. Shadow mode overspends compute by "
            "design and establishes no equal-envelope or strength result."
        ),
    }
    for run_dir in runs:
        manifest = load_manifest(run_dir)
        summary["runs"].append(
            {
                "run_id": manifest["run_id"],
                "generation": manifest["generation"],
                "position_id": manifest["position"]["position_id"],
                "disposition": manifest["disposition"]["run"],
                "root_count": manifest["legal_root_oracle"]["root_count"],
                "shadow_stages": len([s for s in manifest["stages"] if s["role"] == "shadow"]),
                "overhead": manifest["controller"]["overhead"],
            }
        )

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "sweep.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    dispositions: dict[str, int] = {}
    for record in summary["runs"]:
        dispositions[record["disposition"]] = dispositions.get(record["disposition"], 0) + 1
    print(
        f"shadow evidence sweep: {len(runs)} bundles in {summary['elapsed_s']}s; "
        f"skipped={len(skipped)}; dispositions={dispositions}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UciError, OSError, ValueError) as exc:
        print(f"shadow evidence sweep failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
