#!/usr/bin/env python3
"""Real-engine counterfactual hybrid-decision contract.

The contract proves that a deterministic hybrid proposal can be frozen from
typed evidence before the anchor boundary while the outward UCI move remains
exactly the Stockfish anchor move. It makes no strength or correctness claim.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.counterfactual import (
    load_counterfactual_artifact,
    verify_counterfactual_integrity,
)
from controller.crossfeed import verify_crossfeed_integrity
from controller.replay import discover_replay_bundles, load_manifest, verify_bundle_integrity
from controller.runtime import load_runtime_config
from controller.verification import verify_verification_integrity
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.counterfactual.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "counterfactual"
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
        or config.counterfactual is None
        or config.verification is None
    ):
        raise ContractError(
            "counterfactual validation profile must enable VERIFY, cross-feed and counterfactual mode"
        )
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
            label="counterfactual outward bestmove",
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
                if (candidate / "decision" / "counterfactual.json").is_file():
                    run_dir = candidate
                    break
            time.sleep(0.05)
        if run_dir is None:
            raise ContractError("counterfactual run did not seal its decision artifact")

    parent_problems = verify_bundle_integrity(run_dir)
    verify_problems = verify_verification_integrity(run_dir)
    crossfeed_problems = verify_crossfeed_integrity(run_dir)
    decision_problems = verify_counterfactual_integrity(run_dir)
    if parent_problems:
        raise ContractError(f"parent replay integrity failed: {parent_problems}")
    if verify_problems:
        raise ContractError(f"VERIFY integrity failed: {verify_problems}")
    if crossfeed_problems:
        raise ContractError(f"cross-feed integrity failed: {crossfeed_problems}")
    if decision_problems:
        raise ContractError(
            f"counterfactual decision integrity failed: {decision_problems}"
        )

    parent = load_manifest(run_dir)
    artifact = load_counterfactual_artifact(run_dir)
    anchor = next(stage for stage in parent["stages"] if stage["role"] == "anchor")
    if anchor.get("bestmove") != outward:
        raise ContractError(
            f"outward {outward} differs from recorded anchor {anchor.get('bestmove')}"
        )

    proposal = artifact.get("proposal") or {}
    if proposal.get("frozen_before_anchor") is not True:
        raise ContractError(
            "qualification run did not freeze counterfactual evidence before anchor completion"
        )
    if artifact.get("counterfactual", {}).get("outward_authority") != "stockfish-anchor":
        raise ContractError("counterfactual artifact changed outward authority")

    all_search_ids = [
        str(stage.get("search_id", ""))
        for stage in parent.get("stages") or []
        if isinstance(stage, dict)
    ]
    verification = json.loads(
        (run_dir / "verification" / "manifest.json").read_text(encoding="utf-8")
    )
    all_search_ids.extend(
        str(stage.get("search_id", ""))
        for stage in verification.get("stages") or []
        if isinstance(stage, dict)
    )
    if any(
        "counterfactual" in value.lower() or "decision" in value.lower()
        for value in all_search_ids
    ):
        raise ContractError("decision laboratory launched an unexpected engine search")

    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "run_id": parent["run_id"],
        "outward_bestmove": outward,
        "decision_id": artifact["decision_id"],
        "proposal_disposition": proposal.get("disposition", {}).get("code"),
        "proposal_move": proposal.get("move"),
        "proposal_source_owner": proposal.get("source_owner"),
        "frozen_before_anchor": proposal.get("frozen_before_anchor"),
        "would_change_outward_move": artifact.get("counterfactual", {}).get(
            "would_change_outward_move"
        ),
        "parent_integrity": True,
        "verification_integrity": True,
        "crossfeed_integrity": True,
        "counterfactual_integrity": True,
        "claim": (
            "Control-plane qualification only: one deterministic hybrid proposal "
            "record was frozen from typed VERIFY/cross-feed evidence before the "
            "anchor completion boundary, while the sole outward move remained "
            "the Stockfish anchor move. No move-correctness, Elo or strength claim."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Counterfactual contract passed: "
        f"run={parent['run_id']}, decision={artifact['decision_id']}, "
        f"disposition={report['proposal_disposition']}, outward={outward}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"counterfactual contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
