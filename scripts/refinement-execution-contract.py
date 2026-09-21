#!/usr/bin/env python3
"""Real-engine contract for EXPLORE -> VERIFY -> one-level shadow REFINE.

This is an orchestration/evidence contract, not a strength experiment. REFINE
remains shadow-only, the LC0 profile is backend-light/random, and unrestricted
Stockfish remains the sole outward decision authority.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.refinement import (
    load_refinement_manifest,
    verify_refinement_integrity,
)
from controller.replay import (
    discover_replay_bundles,
    load_manifest,
    verify_bundle_integrity,
)
from controller.runtime import load_runtime_config
from controller.verification import (
    load_verification_manifest,
    verify_verification_integrity,
)
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.refine.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "refine-execution"
ANCHOR_MOVETIME_MS = 6000
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def _load_validator():
    path = ROOT / "scripts" / "validate-telemetry-contract.py"
    spec = importlib.util.spec_from_file_location("telemetry_contract_validator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()


def extract_bestmove(lines: list[str]) -> str:
    moves = [line.split()[1] for line in lines if line.startswith("bestmove ")]
    if len(moves) != 1:
        raise ContractError(f"expected exactly one outward bestmove, got {moves}")
    move = moves[0].lower()
    if not _MOVE_RE.fullmatch(move):
        raise ContractError(f"non-canonical outward bestmove: {move!r}")
    return move


def engine_processes_alive(config) -> list[str]:
    alive: list[str] = []
    for spec in config.backends.values():
        probe = subprocess.run(
            ["pgrep", "-f", str(spec.binary)],
            capture_output=True,
            text=True,
        )
        pids = [pid for pid in probe.stdout.split() if pid and int(pid) != os.getpid()]
        if pids:
            alive.append(f"{spec.name}:{','.join(pids)}")
    return alive


def stream_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def assert_prefix_free(snapshot: dict) -> None:
    by_id = {row["id"]: tuple(row["prefix"]) for row in snapshot["shards"]}
    frontier = [by_id[shard_id] for shard_id in snapshot["frontier_ids"]]
    for i, left in enumerate(frontier):
        for right in frontier[i + 1 :]:
            if len(left) < len(right) and right[: len(left)] == left:
                raise ContractError(f"frontier prefix overlap: {left} < {right}")
            if len(right) < len(left) and left[: len(right)] == right:
                raise ContractError(f"frontier prefix overlap: {right} < {left}")


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    if (
        config.mode != "shadow"
        or config.verification is None
        or config.refinement is None
    ):
        raise ContractError("REFINE profile must be shadow mode with VERIFY and REFINE enabled")

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
            label="REFINE contract outward bestmove",
            timeout=90.0,
        )
        outward = extract_bestmove(lines)

        deadline = time.monotonic() + 20.0
        run_dir: Path | None = None
        while time.monotonic() < deadline:
            discovery = discover_replay_bundles(replay_root)
            candidates = [path for path in discovery.bundles if path.name not in known]
            if candidates:
                candidate = candidates[-1]
                if (
                    (candidate / "verification" / "manifest.json").is_file()
                    and (candidate / "refinement" / "manifest.json").is_file()
                ):
                    run_dir = candidate
                    break
            time.sleep(0.05)
        if run_dir is None:
            raise ContractError("REFINE contract produced no finalized child artifact")

    parent = load_manifest(run_dir)
    problems = verify_bundle_integrity(run_dir)
    if problems:
        raise ContractError(f"parent replay integrity failed: {problems}")

    verification = load_verification_manifest(run_dir)
    problems = verify_verification_integrity(run_dir)
    if problems:
        raise ContractError(f"verification integrity failed: {problems}")

    refinement = load_refinement_manifest(run_dir)
    problems = verify_refinement_integrity(run_dir)
    if problems:
        raise ContractError(f"refinement integrity failed: {problems}")

    if verification["disposition"]["run"] != "completed":
        raise ContractError(f"VERIFY did not complete: {verification['disposition']}")

    targets = refinement["nomination"]["targets"]
    if not targets:
        raise ContractError(
            "real REFINE contract observed unanimous VERIFY finals; "
            "validation profile must exercise at least one disagreement target"
        )
    if refinement["disposition"]["run"] != "completed":
        raise ContractError(f"REFINE did not complete: {refinement['disposition']}")

    verify_candidates = verification["nomination"]["candidate_roots"]
    verify_finals = set(refinement["nomination"]["verify_final_by_owner"].values())
    expected_targets = [move for move in verify_candidates if move in verify_finals][
        : refinement["nomination"]["max_targets"]
    ]
    if targets != expected_targets:
        raise ContractError(
            f"REFINE targets are not deterministic VERIFY-final disagreement: "
            f"expected={expected_targets}, actual={targets}"
        )

    assert_prefix_free(refinement["prefix_ledger"]["final_v2_snapshot"])

    telemetry_contract = VALIDATOR.load_contract()
    validated_streams: list[str] = []
    for target in refinement["targets"]:
        if target["disposition"]["target"] not in ("completed", "terminal"):
            raise ContractError(
                f"target {target['target_id']} did not complete: {target['disposition']}"
            )
        children = target["child_oracle"]["children"]
        if target["child_oracle"]["terminal"]:
            if children or target["stages"] or target["streams"]:
                raise ContractError("terminal target contains dispatched child work")
            continue

        union: list[str] = []
        for owner in refinement["owners"]:
            union.extend(target["child_partition"][owner])
        if len(union) != len(set(union)) or set(union) != set(children):
            raise ContractError(
                f"target {target['target_id']} child ownership is not exact/disjoint"
            )

        stage_by_instance = {stage["instance"]: stage for stage in target["stages"]}
        for record in target["streams"]:
            if not record["contract_validatable"]:
                raise ContractError(
                    f"{target['target_id']}:{record['instance']} stream is not validatable"
                )
            path = run_dir / "refinement" / record["path"]
            VALIDATOR.validate_stream(path, telemetry_contract)
            events = stream_events(path)
            if not events:
                raise ContractError(f"empty REFINE stream: {record['path']}")
            started = events[0]
            if started["controller"]["phase"] != "REFINE":
                raise ContractError(f"{record['instance']}: telemetry phase is not REFINE")
            stage = stage_by_instance[record["instance"]]
            if started["request"].get("root_moves") != stage["child_moves"]:
                raise ContractError(
                    f"{record['instance']}: telemetry child roots differ from stage"
                )
            if started["position"].get("moves", []) != (
                parent["position"]["moves"] + [target["root_move"]]
            ):
                raise ContractError(
                    f"{record['instance']}: telemetry did not bind descendant position"
                )
            validated_streams.append(
                f"{target['target_id']}:{record['instance']}"
            )

    anchor_stage = next(stage for stage in parent["stages"] if stage["role"] == "anchor")
    if anchor_stage["bestmove"] != outward:
        raise ContractError(
            f"outward answer {outward} differs from recorded anchor {anchor_stage['bestmove']}"
        )
    if any(":refine:" in stage["search_id"] for stage in parent["stages"]):
        raise ContractError("REFINE stages leaked into parent replay schema v1")
    if "REFINE" in json.dumps(verification):
        raise ContractError("REFINE evidence leaked into raw VERIFY artifact")

    raw_blob = json.dumps(refinement).lower()
    for forbidden in (
        "correct_move",
        "winner",
        "routing_value",
        "expected_value",
        "strength_gain",
        "elo",
    ):
        if forbidden in raw_blob:
            raise ContractError(f"derived/strength field leaked into REFINE artifact: {forbidden}")

    orphans = engine_processes_alive(config)
    if orphans:
        raise ContractError(f"engine processes outlived REFINE contract: {orphans}")

    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "run_id": parent["run_id"],
        "outward_bestmove": outward,
        "verification_candidates": verify_candidates,
        "verification_finals": refinement["nomination"]["verify_final_by_owner"],
        "refinement_targets": targets,
        "validated_refine_streams": validated_streams,
        "parent_integrity": True,
        "verification_integrity": True,
        "refinement_integrity": True,
        "claim": (
            "Orchestration only: completed VERIFY disagreement nominated exact root "
            "targets, each target was split by the Stockfish perft-1 oracle into a "
            "pairwise-disjoint descendant frontier, and shadow engines searched their "
            "owned child regions while Stockfish anchor retained sole outward authority. "
            "No strength, correctness, routing-value or equal-compute claim."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "REFINE execution contract passed: "
        f"targets={targets}, streams={len(validated_streams)}, outward={outward}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"REFINE execution contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
