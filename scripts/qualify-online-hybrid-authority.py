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
    command = policy["positive_case"]["command"]
    with UciSession(Path(sys.executable), cwd=ROOT, timeout=45,
                    args=["-m", "controller", "--config", str(CONFIG)]) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.ready()
        started = time.monotonic()
        shell.send(command)
        lines = shell.read_until(lambda line: line.startswith("bestmove "), label="G3 bestmove", timeout=8)
        elapsed = (time.monotonic() - started) * 1000
        moves = [line.split()[1] for line in lines if line.startswith("bestmove ")]
        require(len(moves) == 1 and MOVE_RE.fullmatch(moves[0]) is not None, f"invalid outward move: {moves!r}")
    run = wait_bundle(replay_root, known)
    problems = verify_bundle_integrity(run)
    require(not problems, f"replay integrity failed: {problems}")
    problems = verify_counterfactual_integrity(run)
    require(not problems, f"counterfactual integrity failed: {problems}")
    problems = verify_final_decision_integrity(run)
    require(not problems, f"final decision integrity failed: {problems}")
    manifest = load_manifest(run)
    decision = load_final_decision_artifact(run)["decision"]
    require(decision["authority"] == policy["positive_case"]["require_authority"],
            f"positive case did not authorize HYBRID: {decision}")
    snap = decision["authorization_snapshot"]
    require(snap["terminal_source"] == policy["positive_case"]["require_terminal_source"],
            f"wrong terminal source: {snap.get('terminal_source')!r}")
    require(snap["route_action"] == "BUY_STAGED_VERIFY" and snap["route_buy_extension"] is True,
            "authority was not bound to a BUY_STAGED_VERIFY route")
    require(snap["staged_complete"] is True, "staged VERIFY did not complete")
    outcome = manifest.get("clock_outcome") or {}
    require(outcome.get("output_within_deadline") is True, "outward move missed hard deadline")
    require((manifest.get("outward_decision") or {}).get("emitted_move") == moves[0],
            "manifest outward decision differs from UCI output")
    RESULT.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1, "profile_id": policy["profile_id"], "run_id": run.name,
        "authority": decision["authority"], "anchor_move": decision["anchor_move"],
        "proposal_move": decision["proposal_move"], "emitted_move": decision["emitted_move"],
        "terminal_source": snap["terminal_source"], "route_action": snap["route_action"],
        "driver_observed_ms": elapsed, "clock_outcome": outcome,
        "claim": "M14-G3 clocked staged authority integration only; no learned-SKIP, Elo, superiority, or deployment claim.",
    }
    (RESULT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("M14-G3 qualification passed:", json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        RESULT.mkdir(parents=True, exist_ok=True)
        (RESULT / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
