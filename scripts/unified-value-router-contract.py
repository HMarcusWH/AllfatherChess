#!/usr/bin/env python3
"""Real-engine M14-G2 unified value-of-compute router contract.

The shipped validation profile deliberately has no fitted staged/regime models.
The unified router must therefore fail closed to BUY_STAGED_VERIFY, after which
the existing resource authority independently reserves the extension. The
completed extension becomes the terminal source for the frozen counterfactual
decision. Outward authority remains the Stockfish anchor.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.counterfactual import (
    load_counterfactual_artifact,
    verify_counterfactual_integrity,
)
from controller.replay import discover_replay_bundles, load_manifest
from controller.staged_verification import verify_staged_verification_integrity
from tests.harness.uci_session import UciSession


CONFIG = ROOT / "config" / "allfather.unified-value.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "unified-value-router"
REPLAY_ROOT = RESULT_DIR / "replays"
ANCHOR_MOVETIME_MS = 5000


class ContractError(RuntimeError):
    pass


def _write_config() -> Path:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    doc["root"] = str(ROOT)
    doc["shadow"]["replay_root"] = str(REPLAY_ROOT.resolve())
    path = RESULT_DIR / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _wait_run(known: set[str], timeout: float = 35.0) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        discovery = discover_replay_bundles(REPLAY_ROOT)
        fresh = [path for path in discovery.bundles if path.name not in known]
        for candidate in reversed(fresh):
            required = (
                candidate / "route.json",
                candidate / "resource.json",
                candidate / "staged_verification" / "manifest.json",
                candidate / "decision" / "counterfactual.json",
            )
            if all(path.is_file() for path in required):
                return candidate
            parent = candidate / "manifest.json"
            if parent.is_file():
                manifest = load_manifest(candidate)
                if (manifest.get("disposition") or {}).get("run") in {
                    "aborted",
                    "cancelled",
                }:
                    raise ContractError(
                        "unified-value run ended before its evidence sealed: "
                        f"{manifest.get('notes')}"
                    )
        time.sleep(0.05)
    raise ContractError("timed out waiting for unified-value evidence")


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    before = discover_replay_bundles(REPLAY_ROOT)
    known = {path.name for path in before.bundles} | {
        item.path.name for item in before.skipped
    }
    config = _write_config()

    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=60.0,
        args=["-m", "controller", "--config", str(config)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.send(f"go movetime {ANCHOR_MOVETIME_MS}")
        lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="unified-value outward bestmove",
            timeout=90.0,
        )
        outward = next(
            line.split()[1].lower()
            for line in lines
            if line.startswith("bestmove ")
        )
        run_dir = _wait_run(known)

    staged_problems = verify_staged_verification_integrity(run_dir)
    if staged_problems:
        raise ContractError(f"staged VERIFY integrity failed: {staged_problems}")
    counterfactual_problems = verify_counterfactual_integrity(run_dir)
    if counterfactual_problems:
        raise ContractError(
            f"counterfactual integrity failed: {counterfactual_problems}"
        )

    parent = load_manifest(run_dir)
    route = json.loads((run_dir / "route.json").read_text(encoding="utf-8"))
    resource = json.loads((run_dir / "resource.json").read_text(encoding="utf-8"))
    staged = json.loads(
        (run_dir / "staged_verification" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    counterfactual = load_counterfactual_artifact(run_dir)

    anchor = next(
        stage for stage in parent.get("stages") or [] if stage.get("role") == "anchor"
    )
    if anchor.get("bestmove") != outward:
        raise ContractError("outward move differs from the Stockfish anchor")

    if route.get("policy") != "unified_value_v1":
        raise ContractError(f"unexpected route policy: {route.get('policy')!r}")
    value_decisions = route.get("value_decisions") or []
    if len(value_decisions) != 1:
        raise ContractError(
            f"expected exactly one staged route decision, got {len(value_decisions)}"
        )
    decision = value_decisions[0]
    if decision.get("action") != "BUY_STAGED_VERIFY":
        raise ContractError(
            "null calibration validation profile must fail closed to BUY_STAGED_VERIFY"
        )
    if decision.get("buy_extension") is not True:
        raise ContractError("BUY_STAGED_VERIFY did not request the extension")
    authority = decision.get("authority") or {}
    if authority.get("routing") is not True:
        raise ContractError("unified value decision did not declare routing authority")
    if authority.get("resource") is not False or authority.get("outward_move") is not False:
        raise ContractError("route decision acquired forbidden resource/move authority")

    actions = route.get("specialist_actions") or []
    extension_grants = [
        row
        for row in actions
        if row.get("event") == "authorize"
        and row.get("phase") == "verify"
        and row.get("target_id") == "staged_extension"
        and row.get("granted") is True
    ]
    if len(extension_grants) != 3:
        raise ContractError(
            f"expected three separately authorized extension stages, got {len(extension_grants)}"
        )

    stages = staged.get("stages") or []
    if len(stages) != 3 or any(
        stage.get("disposition") != "completed" for stage in stages
    ):
        raise ContractError("staged extension did not complete cleanly")

    resource_phases = {
        row.get("phase")
        for row in resource.get("stages") or []
        if isinstance(row, dict)
    }
    if "VERIFY" not in resource_phases or "VERIFY_EXTENSION" not in resource_phases:
        raise ContractError("base and extension resource stages were not measured separately")

    source = counterfactual.get("source") or {}
    if source.get("decision_terminal_source") != "staged_verification":
        raise ContractError(
            "completed staged VERIFY was not used as the final counterfactual terminal source"
        )
    if not isinstance(source.get("staged_verification_manifest_sha256"), str):
        raise ContractError("counterfactual did not hash-bind the staged terminal source")

    report = {
        "schema_version": 1,
        "config": str(CONFIG.relative_to(ROOT)),
        "run_id": parent.get("run_id"),
        "outward_bestmove": outward,
        "route_policy": route.get("policy"),
        "route_action": decision.get("action"),
        "staged_model_loaded": decision.get("staged_model_id") is not None,
        "regime_model_loaded": decision.get("regime_model_id") is not None,
        "extension_resource_authorizations": len(extension_grants),
        "counterfactual_terminal_source": source.get("decision_terminal_source"),
        "claim": (
            "Control-plane qualification only: unified_value_v1 made one live "
            "route decision, failed closed to buying the staged VERIFY extension "
            "without fitted models, then the existing resource authority separately "
            "authorized and measured that work. The completed extension updated the "
            "frozen counterfactual decision source. The outward move remained the "
            "Stockfish anchor. No correctness, Elo, strength, or optimal-routing claim."
        ),
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "unified value router contract passed: "
        f"run={parent.get('run_id')}, route={decision.get('action')}, outward={outward}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        print(f"unified value router contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
