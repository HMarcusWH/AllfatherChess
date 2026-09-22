#!/usr/bin/env python3
"""Real-engine active VERIFY/REFINE specialist-envelope contract.

This proves accounting and authority wiring, not chess strength. Every
specialist stage must be authorized from a declared reserve before dispatch,
and the final route certificate must close with no open reservation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.refinement import load_refinement_manifest, verify_refinement_integrity
from controller.replay import discover_replay_bundles, load_manifest, verify_bundle_integrity
from controller.runtime import load_runtime_config
from controller.verification import load_verification_manifest, verify_verification_integrity
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.active.specialist.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "active-specialist"
ANCHOR_MOVETIME_MS = 5000
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


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


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    if (
        config.mode != "active"
        or config.verification is None
        or config.refinement is None
        or config.budget is None
        or config.routing is None
    ):
        raise ContractError(
            "active specialist profile must enable budgeted VERIFY and REFINE"
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
            label="active specialist outward bestmove",
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
                    (candidate / "route.json").is_file()
                    and (candidate / "verification" / "manifest.json").is_file()
                    and (candidate / "refinement" / "manifest.json").is_file()
                ):
                    run_dir = candidate
                    break
            time.sleep(0.05)
        if run_dir is None:
            raise ContractError("active specialist run did not finalize all evidence")

    parent = load_manifest(run_dir)
    parent_problems = verify_bundle_integrity(run_dir)
    verify_problems = verify_verification_integrity(run_dir)
    refine_problems = verify_refinement_integrity(run_dir)
    if parent_problems:
        raise ContractError(f"parent replay integrity failed: {parent_problems}")
    if verify_problems:
        raise ContractError(f"VERIFY integrity failed: {verify_problems}")
    if refine_problems:
        raise ContractError(f"REFINE integrity failed: {refine_problems}")

    verification = load_verification_manifest(run_dir)
    refinement = load_refinement_manifest(run_dir)
    route = json.loads((run_dir / "route.json").read_text(encoding="utf-8"))

    if verification["disposition"]["run"] != "completed":
        raise ContractError(f"VERIFY did not complete: {verification['disposition']}")
    if refinement["disposition"]["run"] not in ("completed", "not_applicable"):
        raise ContractError(f"REFINE did not complete cleanly: {refinement['disposition']}")

    anchor_stage = next(stage for stage in parent["stages"] if stage["role"] == "anchor")
    if anchor_stage["bestmove"] != outward:
        raise ContractError(
            f"outward {outward} differs from recorded anchor {anchor_stage['bestmove']}"
        )

    budget = route["budget"]
    if budget["open_reservations"] != 0:
        raise ContractError(f"open reservations remain: {budget['open_reservations']}")
    if not budget["within_envelope"]:
        raise ContractError(f"global CPU/GPU envelope exceeded: {budget}")
    if not budget["within_partition_caps"]:
        raise ContractError(f"solver/specialist partition cap exceeded: {budget}")
    if not route["envelope_claim"].get("specialist_settlement_complete"):
        raise ContractError(
            f"specialist settlement was incomplete: {route['envelope_claim']}"
        )
    if not route["envelope_claim"]["claimed"]:
        raise ContractError(
            f"positive active specialist envelope claim was not reached: "
            f"{route['envelope_claim']}"
        )

    specialist = route.get("specialist_actions") or []
    grants = [
        row for row in specialist
        if row.get("event") == "authorize" and row.get("granted")
    ]
    phases = {row.get("phase") for row in grants}
    if "verify" not in phases:
        raise ContractError("no VERIFY dispatch carried an active reservation")

    verify_stages = verification.get("stages") or []
    verify_grants = [row for row in grants if row.get("phase") == "verify"]
    if len(verify_grants) != len(verify_stages):
        raise ContractError(
            f"VERIFY authorization count differs from dispatched stages: "
            f"{len(verify_grants)} vs {len(verify_stages)}"
        )

    targets = refinement.get("nomination", {}).get("targets") or []
    if targets:
        if "refine_oracle" not in phases or "refine" not in phases:
            raise ContractError(
                "applicable REFINE did not account for both oracle and descendant stages"
            )
        expected_oracles = len(refinement.get("targets") or [])
        oracle_grants = [row for row in grants if row.get("phase") == "refine_oracle"]
        if len(oracle_grants) != expected_oracles:
            raise ContractError(
                f"REFINE oracle authorization count mismatch: "
                f"{len(oracle_grants)} vs {expected_oracles}"
            )
        expected_refine_stages = sum(
            len(target.get("stages") or []) for target in refinement.get("targets") or []
        )
        refine_grants = [row for row in grants if row.get("phase") == "refine"]
        if len(refine_grants) != expected_refine_stages:
            raise ContractError(
                f"REFINE stage authorization count mismatch: "
                f"{len(refine_grants)} vs {expected_refine_stages}"
            )

    settles = [
        row for row in specialist
        if row.get("event") == "settle"
    ]
    if len(settles) != len(grants):
        raise ContractError(
            f"specialist settlement count differs from granted work: "
            f"{len(settles)} vs {len(grants)}"
        )

    purpose = budget["purpose_totals"]
    if purpose["verify"]["spent_cpu_ms"] <= 0:
        raise ContractError("VERIFY purpose lane recorded no CPU spend")
    if targets and purpose["refine"]["spent_cpu_ms"] <= 0:
        raise ContractError("REFINE purpose lane recorded no CPU spend")

    if budget["committed_cpu_ms"] > route["envelope"]["cpu_ms"]:
        raise ContractError("committed CPU exceeds declared envelope")
    if budget["committed_gpu_ms"] > route["envelope"]["gpu_ms"]:
        raise ContractError("committed GPU exceeds declared envelope")

    orphans = engine_processes_alive(config)
    if orphans:
        raise ContractError(f"engine processes outlived active specialist contract: {orphans}")

    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "run_id": parent["run_id"],
        "outward_bestmove": outward,
        "verification_stages": len(verify_stages),
        "refinement_targets": targets,
        "specialist_authorizations": grants,
        "budget": budget,
        "envelope_claim": route["envelope_claim"],
        "parent_integrity": True,
        "verification_integrity": True,
        "refinement_integrity": True,
        "claim": (
            "Control-plane qualification only: active VERIFY and any applicable "
            "one-level REFINE/oracle work were reserved, dispatched and settled "
            "inside one declared CPU/GPU/wall envelope while Stockfish anchor "
            "retained sole outward authority. No strength, Elo or correctness claim."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Active specialist contract passed: "
        f"verify={len(verify_stages)}, targets={targets}, "
        f"claimed={route['envelope_claim']['claimed']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"active specialist contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
