#!/usr/bin/env python3
"""Collect and summarize shadow VERIFY evidence over the frozen corpus.

This is a research sweep, not a CI gate and not a strength experiment. The
validation LC0 profile is backend-light/random and the VERIFY observatory
intentionally overspends the eventual competitive envelope.
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

from controller.replay import discover_replay_bundles, load_manifest
from controller.runtime import load_runtime_config
from controller.verification_analysis import (
    VerificationAnalysisError,
    build_verification_analysis_artifact,
    write_verification_analysis_artifact,
)
from tests.harness.uci_session import UciError, UciSession


CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
RESULT_DIR = ROOT / "build" / "test-results" / "verify-sweep"
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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "allfather.verify.validation.json",
    )
    p.add_argument("--movetime-ms", type=int, default=3000)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--cases", nargs="*", default=list(DEFAULT_CASES))
    p.add_argument(
        "--derived-root",
        type=Path,
        default=ROOT / "build" / "verification-derived",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_runtime_config(args.config)
    if config.shadow is None or config.verification is None:
        raise SystemExit("verification sweep requires the shadow VERIFY profile")

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
        skipped.path.name for skipped in before.skipped
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
                    label=f"VERIFY sweep bestmove for {case_id}",
                    timeout=90.0,
                )
                move = next(
                    line.split()[1] for line in lines if line.startswith("bestmove ")
                )
                outward.append({"case": case_id, "repeat": repeat, "bestmove": move})

    discovery = discover_replay_bundles(replay_root)
    new_runs = [path for path in discovery.bundles if path.name not in known]
    skipped = [item for item in discovery.skipped if item.path.name not in known]
    verify_runs = [
        path
        for path in new_runs
        if (path / "verification" / "manifest.json").is_file()
    ]
    no_verify_runs = [
        path
        for path in new_runs
        if not (path / "verification" / "manifest.json").is_file()
    ]

    artifact = None
    if verify_runs:
        artifact = build_verification_analysis_artifact(verify_runs)
        write_verification_analysis_artifact(artifact, args.derived_root)

    patterns: dict[str, int] = {}
    relock: dict[str, int] = {}
    adoption = {
        verifier: {source: 0 for source in ("stockfish", "reckless", "lc0")}
        for verifier in ("stockfish", "reckless", "lc0")
    }
    self_retention = {owner: {"retained": 0, "changed": 0} for owner in adoption}
    unanimous_source = {owner: 0 for owner in adoption}
    pair_agree = {
        "stockfish-shadow|reckless-shadow": [0, 0],
        "stockfish-shadow|lc0-shadow": [0, 0],
        "reckless-shadow|lc0-shadow": [0, 0],
    }
    relock_fractions: list[float] = []
    eligible = 0

    if artifact is not None:
        for run in artifact.runs:
            patterns[run["final_pattern"]] = patterns.get(run["final_pattern"], 0) + 1
            status = run["relock"]["status"]
            relock[status] = relock.get(status, 0) + 1
            if not run["analysis_eligible"]:
                continue
            eligible += 1
            fraction = run["relock"].get("relock_fraction")
            if fraction is not None:
                relock_fractions.append(float(fraction))
            source = run.get("unanimous_source_owner")
            if source in unanimous_source:
                unanimous_source[source] += 1
            for row in run["explore_to_verify"]:
                owner = row["owner"]
                selected = row.get("selected_nominee_owner")
                if selected in adoption[owner]:
                    adoption[owner][selected] += 1
                if row.get("self_retained") is True:
                    self_retention[owner]["retained"] += 1
                elif row.get("self_retained") is False:
                    self_retention[owner]["changed"] += 1
            for item in run["final_pairwise"]:
                key = f"{item['left']}|{item['right']}"
                if key not in pair_agree:
                    key = f"{item['right']}|{item['left']}"
                if key in pair_agree and item.get("leader_agree") is not None:
                    pair_agree[key][1] += 1
                    if item["leader_agree"]:
                        pair_agree[key][0] += 1

    report = {
        "schema_version": 1,
        "config": str(args.config.relative_to(ROOT)),
        "movetime_ms": args.movetime_ms,
        "cases": list(args.cases),
        "repeats": args.repeats,
        "elapsed_s": round(time.monotonic() - started, 3),
        "outward_moves": outward,
        "runs_attempted": len(new_runs),
        "verify_children": len(verify_runs),
        "no_verify_children": len(no_verify_runs),
        "analysis_eligible": eligible,
        "final_patterns": patterns,
        "relock": relock,
        "median_relock_fraction": (
            None if not relock_fractions else statistics.median(relock_fractions)
        ),
        "adoption_matrix": adoption,
        "self_retention": self_retention,
        "unanimous_source_owner": unanimous_source,
        "pairwise_final_leader_agreement": {
            key: {
                "agree": values[0],
                "defined": values[1],
                "rate": None if values[1] == 0 else values[0] / values[1],
            }
            for key, values in pair_agree.items()
        },
        "analysis_id": None if artifact is None else artifact.analysis_id,
        "runs_without_verify": [
            load_manifest(path)["run_id"] for path in no_verify_runs
        ],
        "skipped_replay_directories": [item.as_dict() for item in skipped],
        "claim": (
            "Mechanism evidence only. The validation LC0 profile is not "
            "strength-qualified, shadow VERIFY overspends compute, and descriptive "
            "RELOCK is not a correctness or routing certificate."
        ),
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "sweep.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "VERIFY evidence sweep: "
        f"runs={len(new_runs)}, verify={len(verify_runs)}, eligible={eligible}, "
        f"patterns={patterns}, relock={relock}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UciError, VerificationAnalysisError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"VERIFY evidence sweep failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
