#!/usr/bin/env python3
"""Real-process M14-G3 qualification on the frozen ONLINE-2 CPU bundle."""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controller.online_hybrid_profile import load_json, validate_online_hybrid_profile
from controller.replay import discover_replay_bundles, load_manifest, verify_bundle_integrity
from controller.counterfactual import verify_counterfactual_integrity
from controller.final_decision import load_final_decision_artifact, verify_final_decision_integrity
from tests.harness.uci_session import UciSession

POLICY = ROOT / "qualification/online-hybrid-authority.json"
CONFIG = ROOT / "config/allfather.online-hybrid.validation.json"
ONLINE2 = ROOT / "config/allfather.online.cpu-reference.json"
RESULT = ROOT / "build/test-results/online-hybrid"
MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

class QualificationError(RuntimeError):
    pass

def require(condition, message):
    if not condition:
        raise QualificationError(message)

def wait_bundle(root: Path, known: set[str]) -> Path:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for run in discover_replay_bundles(root).bundles:
            if run.name not in known and (run / "decision" / "final.json").is_file():
                return run
        time.sleep(0.05)
    raise QualificationError("G3 replay/final decision did not finalize")

def main() -> int:
    policy = load_json(POLICY)
    config_doc = load_json(CONFIG)
    online2 = load_json(ONLINE2)
    validate_online_hybrid_profile(policy, config_doc, online2)
    replay_root = ROOT / config_doc["shadow"]["replay_root"]
    known = {p.name for p in discover_replay_bundles(replay_root).bundles}
    cases = policy.get("positive_cases")
    require(isinstance(cases, list) and cases, "positive_cases must be non-empty")
    requirement = policy["positive_requirement"]

    records = []
    winner = None
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=45,
        args=["-m", "controller", "--config", str(CONFIG)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        for case in cases:
            label = str(case["id"])
            position_moves = list(case["moves"])
            command = str(case["command"])
            shell.new_game()
            shell.set_position({"startpos_moves": position_moves})
            shell.ready()
            started = time.monotonic()
            shell.send(command)
            lines = shell.read_until(
                lambda line: line.startswith("bestmove "),
                label=f"G3 bestmove {label}",
                timeout=8,
            )
            elapsed = (time.monotonic() - started) * 1000
            moves_out = [
                line.split()[1]
                for line in lines
                if line.startswith("bestmove ")
            ]
            require(
                len(moves_out) == 1
                and MOVE_RE.fullmatch(moves_out[0]) is not None,
                f"{label}: invalid outward move: {moves_out!r}",
            )

            run = wait_bundle(replay_root, known)
            known.add(run.name)
            problems = verify_bundle_integrity(run)
            require(not problems, f"{label}: replay integrity failed: {problems}")
            manifest = load_manifest(run)
            decision = load_final_decision_artifact(run)["decision"]
            snap = decision["authorization_snapshot"]
            final_problems = verify_final_decision_integrity(run)
            require(
                not final_problems,
                f"{label}: final decision integrity failed: {final_problems}",
            )

            record = {
                "case": label,
                "run_id": run.name,
                "authority": decision["authority"],
                "anchor_move": decision["anchor_move"],
                "proposal_move": decision["proposal_move"],
                "emitted_move": decision["emitted_move"],
                "terminal_source": snap.get("terminal_source"),
                "route_action": snap.get("route_action"),
                "staged_complete": snap.get("staged_complete"),
                "driver_observed_ms": elapsed,
                "clock_outcome": manifest.get("clock_outcome"),
            }
            records.append(record)

            qualifies = (
                decision["authority"] == requirement["require_authority"]
                and snap.get("terminal_source")
                == requirement["require_terminal_source"]
                and snap.get("route_action") == "BUY_STAGED_VERIFY"
                and snap.get("route_buy_extension") is True
                and snap.get("staged_complete") is True
                and decision["proposal_move"] is not None
                and (
                    not requirement.get("require_non_anchor_move")
                    or decision["proposal_move"] != decision["anchor_move"]
                )
            )
            if not qualifies:
                continue

            counterfactual_problems = verify_counterfactual_integrity(run)
            require(
                not counterfactual_problems,
                f"{label}: counterfactual integrity failed: "
                f"{counterfactual_problems}",
            )
            outcome = manifest.get("clock_outcome") or {}
            require(
                outcome.get("output_within_deadline") is True,
                f"{label}: outward move missed hard deadline",
            )
            require(
                (manifest.get("outward_decision") or {}).get("emitted_move")
                == moves_out[0],
                f"{label}: manifest outward decision differs from UCI output",
            )
            winner = record
            break

    require(
        winner is not None,
        "no predeclared real-backend case demonstrated non-anchor HYBRID "
        + json.dumps(records, sort_keys=True),
    )

    RESULT.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "profile_id": policy["profile_id"],
        "positive_case": winner,
        "cases": records,
        "claim": (
            "M14-G3 clocked staged authority integration only; one "
            "predeclared real-backend case emitted a non-anchor HYBRID move. "
            "No learned-SKIP, Elo, superiority, or deployment claim."
        ),
    }
    (RESULT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("M14-G3 qualification passed:", json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        RESULT.mkdir(parents=True, exist_ok=True)
        (RESULT / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
