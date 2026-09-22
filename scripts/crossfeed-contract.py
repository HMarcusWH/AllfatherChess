#!/usr/bin/env python3
"""Real-engine typed cross-feed contract.

This contract proves that cross-feed v1 composes existing VERIFY / optional
REFINE evidence into a source-bound artifact without launching a new search or
changing the outward Stockfish-anchor move. It makes no strength claim.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.crossfeed import (
    CROSSFEED_POLICY,
    load_crossfeed_manifest,
    verify_crossfeed_integrity,
)
from controller.refinement import load_refinement_manifest, verify_refinement_integrity
from controller.replay import discover_replay_bundles, load_manifest, verify_bundle_integrity
from controller.runtime import load_runtime_config
from controller.verification import load_verification_manifest, verify_verification_integrity
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.crossfeed.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "crossfeed"
ANCHOR_MOVETIME_MS = 5000
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def _bestmove(lines: list[str]) -> str:
    moves = [line.split()[1].lower() for line in lines if line.startswith("bestmove ")]
    if len(moves) != 1 or not _MOVE_RE.fullmatch(moves[0]):
        raise ContractError(f"expected one canonical outward bestmove, got {moves}")
    return moves[0]


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    if (
        config.mode != "shadow"
        or config.crossfeed is None
        or config.verification is None
        or config.refinement is None
    ):
        raise ContractError(
            "cross-feed validation profile must be shadow mode with VERIFY, REFINE and cross-feed"
        )
    if config.crossfeed.policy != CROSSFEED_POLICY:
        raise ContractError(f"unexpected cross-feed policy: {config.crossfeed.policy}")
    assert config.shadow is not None

    replay_root = config.shadow.replay_root
    replay_root.mkdir(parents=True, exist_ok=True)
    before = discover_replay_bundles(replay_root)
    known = {path.name for path in before.bundles} | {
        item.path.name for item in before.skipped
    }

    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=30.0,
        args=["-m", "controller", "--config", str(CONFIG_PATH)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.send(f"go movetime {ANCHOR_MOVETIME_MS}")
        lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="cross-feed outward bestmove",
            timeout=90.0,
        )
        outward = _bestmove(lines)

        deadline = time.monotonic() + 20.0
        run_dir: Path | None = None
        while time.monotonic() < deadline:
            discovery = discover_replay_bundles(replay_root)
            candidates = [path for path in discovery.bundles if path.name not in known]
            if candidates:
                candidate = candidates[-1]
                if (candidate / "crossfeed" / "manifest.json").is_file():
                    run_dir = candidate
                    break
            time.sleep(0.05)
        if run_dir is None:
            raise ContractError("cross-feed run did not seal its derived artifact")

    parent_problems = verify_bundle_integrity(run_dir)
    verify_problems = verify_verification_integrity(run_dir)
    refine_problems = verify_refinement_integrity(run_dir)
    crossfeed_problems = verify_crossfeed_integrity(run_dir)
    if parent_problems:
        raise ContractError(f"parent replay integrity failed: {parent_problems}")
    if verify_problems:
        raise ContractError(f"VERIFY integrity failed: {verify_problems}")
    if refine_problems:
        raise ContractError(f"REFINE integrity failed: {refine_problems}")
    if crossfeed_problems:
        raise ContractError(f"cross-feed integrity failed: {crossfeed_problems}")

    parent = load_manifest(run_dir)
    verification = load_verification_manifest(run_dir)
    refinement = load_refinement_manifest(run_dir)
    crossfeed = load_crossfeed_manifest(run_dir)

    anchor = next(stage for stage in parent["stages"] if stage["role"] == "anchor")
    if anchor.get("bestmove") != outward:
        raise ContractError(
            f"outward {outward} differs from recorded anchor {anchor.get('bestmove')}"
        )

    if any("crossfeed" in str(stage.get("search_id", "")).lower() for stage in parent["stages"]):
        raise ContractError("cross-feed created an unexpected parent replay search stage")
    if any("crossfeed" in str(stage.get("search_id", "")).lower() for stage in verification["stages"]):
        raise ContractError("cross-feed created an unexpected VERIFY search stage")
    for target in refinement.get("targets") or []:
        if any(
            "crossfeed" in str(stage.get("search_id", "")).lower()
            for stage in target.get("stages") or []
        ):
            raise ContractError("cross-feed created an unexpected REFINE search stage")

    view = crossfeed.get("view") or {}
    candidates = view.get("candidates") or []
    if len(candidates) != 3:
        raise ContractError(f"expected three decision-root candidates, got {len(candidates)}")
    if not (view.get("verification") or {}).get("complete"):
        raise ContractError("real cross-feed contract requires completed VERIFY evidence")

    blob = json.dumps(view, sort_keys=True).lower()
    for forbidden in (
        "score_delta",
        "centipawn_delta",
        "cross_engine_margin",
        "combined_score",
        "weighted_score",
        "winner",
        "correct_move",
    ):
        if forbidden in blob:
            raise ContractError(f"forbidden cross-engine derived field leaked: {forbidden}")

    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "run_id": parent["run_id"],
        "outward_bestmove": outward,
        "crossfeed_id": crossfeed["crossfeed_id"],
        "candidate_count": len(candidates),
        "verification_disposition": verification["disposition"],
        "refinement_disposition": refinement["disposition"],
        "parent_integrity": True,
        "verification_integrity": True,
        "refinement_integrity": True,
        "crossfeed_integrity": True,
        "claim": (
            "Control-plane qualification only: existing VERIFY / optional REFINE "
            "evidence was composed into one typed, hash-bound cross-feed artifact "
            "without a new engine search and without changing Stockfish-anchor "
            "outward decision authority. No correctness, Elo or strength claim."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Cross-feed contract passed: "
        f"run={parent['run_id']}, crossfeed={crossfeed['crossfeed_id']}, "
        f"outward={outward}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"cross-feed contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
