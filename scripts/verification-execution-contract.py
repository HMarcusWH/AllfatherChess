#!/usr/bin/env python3
"""Real-engine contract for deterministic EXPLORE -> VERIFY execution.

This is an orchestration/evidence contract, not a strength experiment. The LC0
profile is backend-light/random, the unrestricted Stockfish anchor remains the
sole outward authority, and the run intentionally overspends research compute.
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

CONFIG_PATH = ROOT / "config" / "allfather.verify.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "verify-execution"
ANCHOR_MOVETIME_MS = 3000
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


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    if config.mode != "shadow" or config.verification is None:
        raise ContractError("verification profile must be shadow mode with VERIFY enabled")

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
            label="VERIFY contract outward bestmove",
            timeout=60.0,
        )
        outward = extract_bestmove(lines)

        # Under on_anchor_complete=drain, a VERIFY stage that was already
        # committed before the outward answer is allowed to finish. Keep the
        # UCI shell alive until the child artifact is finalized; leaving this
        # context immediately would send quit and turn a legitimate drain into
        # a controller-induced cancellation.
        deadline = time.monotonic() + 15.0
        run_dir: Path | None = None
        while time.monotonic() < deadline:
            discovery = discover_replay_bundles(replay_root)
            candidates = [path for path in discovery.bundles if path.name not in known]
            if candidates:
                candidate = candidates[-1]
                if (candidate / "verification" / "manifest.json").is_file():
                    run_dir = candidate
                    break
            time.sleep(0.05)
        if run_dir is None:
            raise ContractError("VERIFY contract produced no finalized child artifact")

    parent = load_manifest(run_dir)
    problems = verify_bundle_integrity(run_dir)
    if problems:
        raise ContractError(f"parent replay integrity failed: {problems}")

    verification = load_verification_manifest(run_dir)
    problems = verify_verification_integrity(run_dir)
    if problems:
        raise ContractError(f"verification integrity failed: {problems}")

    shadow_stages = [stage for stage in parent["stages"] if stage["role"] == "shadow"]
    if len(shadow_stages) != 3:
        raise ContractError(f"expected three EXPLORE stages, got {len(shadow_stages)}")
    if any(stage["disposition"] != "completed" for stage in shadow_stages):
        raise ContractError(
            f"EXPLORE did not complete cleanly: "
            f"{[(s['owner'], s['disposition']) for s in shadow_stages]}"
        )

    owner_roots = parent["ledger"]["owner_roots"]
    explore_union: list[str] = []
    for roots in owner_roots.values():
        explore_union.extend(roots)
    if len(explore_union) != len(set(explore_union)):
        raise ContractError("EXPLORE ownership overlapped before VERIFY")

    by_owner = {stage["owner"]: stage for stage in shadow_stages}
    owner_order = list(parent["ledger"]["owners"])
    expected = [by_owner[owner]["bestmove"] for owner in owner_order]
    if len(expected) != 3 or len(set(expected)) != 3:
        raise ContractError(f"EXPLORE nominees were not three distinct moves: {expected}")
    for owner, move in zip(owner_order, expected):
        if move not in owner_roots[owner]:
            raise ContractError(f"{owner} nominee {move} escaped its EXPLORE region")

    declared = verification["nomination"]["candidate_roots"]
    if declared != expected:
        raise ContractError(
            f"VERIFY candidate set differs from EXPLORE nominees: "
            f"expected={expected}, declared={declared}"
        )
    if verification["disposition"]["run"] != "completed":
        raise ContractError(
            f"VERIFY run did not complete: {verification['disposition']}"
        )
    if len(verification["stages"]) != 3:
        raise ContractError("VERIFY did not dispatch all three shadow instances")
    for stage in verification["stages"]:
        if stage["candidate_roots"] != declared:
            raise ContractError(f"{stage['instance']}: VERIFY roots differ")
        if stage["disposition"] != "completed":
            raise ContractError(
                f"{stage['instance']}: VERIFY disposition={stage['disposition']}"
            )

    telemetry_contract = VALIDATOR.load_contract()
    validated: list[str] = []
    for record in verification["streams"]:
        if not record["contract_validatable"]:
            raise ContractError(
                f"{record['instance']}: VERIFY stream is not contract-validatable"
            )
        path = run_dir / "verification" / record["path"]
        VALIDATOR.validate_stream(path, telemetry_contract)
        events = stream_events(path)
        started = events[0]
        if started["controller"]["phase"] != "VERIFY":
            raise ContractError(f"{record['instance']}: phase is not VERIFY")
        if started["request"].get("root_moves") != declared:
            raise ContractError(
                f"{record['instance']}: telemetry root set differs from VERIFY plan"
            )
        validated.append(record["instance"])

    if sorted(validated) != sorted(config.shadow.instance_by_owner.values()):
        raise ContractError(f"unexpected VERIFY stream set: {validated}")

    anchor_stage = next(stage for stage in parent["stages"] if stage["role"] == "anchor")
    if anchor_stage["bestmove"] != outward:
        raise ContractError(
            f"outward answer {outward} differs from recorded anchor {anchor_stage['bestmove']}"
        )
    if any(":verify:" in stage["search_id"] for stage in parent["stages"]):
        raise ContractError("VERIFY stages leaked into the EXPLORE replay manifest")

    blob = json.dumps(verification).lower()
    for forbidden in (
        "leader_agreement",
        "rank_agreement",
        "consensus",
        "relock",
        "winner",
        "correct_move",
        "routing_value",
    ):
        if forbidden in blob:
            raise ContractError(f"derived field leaked into raw VERIFY artifact: {forbidden}")

    orphans = engine_processes_alive(config)
    if orphans:
        raise ContractError(f"engine processes outlived the VERIFY contract: {orphans}")

    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "run_id": parent["run_id"],
        "outward_bestmove": outward,
        "explore_nominees": expected,
        "verification_candidates": declared,
        "verification_instances": validated,
        "parent_integrity": True,
        "verification_integrity": True,
        "claim": (
            "Orchestration only: pairwise-disjoint EXPLORE was followed by explicit "
            "three-engine common-support VERIFY while Stockfish anchor retained sole "
            "outward authority. No strength, correctness, RELOCK or equal-compute claim."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "VERIFY execution contract passed: "
        f"nominees={expected}, common={declared}, outward={outward}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"VERIFY execution contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
