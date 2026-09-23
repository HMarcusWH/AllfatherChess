#!/usr/bin/env python3
"""M14-C hybrid-authority configuration and real-engine contract.

Default mode is engine-independent and validates the shipped profile grammar.
The --real mode runs the built Stockfish/Reckless/LC0 processes under the actual
Allfather UCI shell, then verifies the sealed final-authority evidence. The real
contract qualifies integration only; it does not require or claim that HYBRID
beats the anchor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.counterfactual import verify_counterfactual_integrity
from controller.crossfeed import verify_crossfeed_integrity
from controller.final_decision import (
    load_final_decision_artifact,
    verify_final_decision_integrity,
)
from controller.replay import (
    discover_replay_bundles,
    load_manifest,
    verify_bundle_integrity,
)
from controller.runtime import load_runtime_config
from controller.verification import verify_verification_integrity
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.hybrid.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "active-hybrid-decision"
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def _profile_contract() -> dict[str, object]:
    document = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    # Engine-independent CI does not require built vendored engines. Preserve
    # every controller setting while redirecting process paths to a real file.
    for spec in document.get("instances", {}).values():
        spec["binary"] = sys.executable
        spec.pop("fallback_glob", None)
    document["root"] = "."

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hybrid.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_runtime_config(path)

    if config.mode != "active":
        raise ContractError("hybrid profile must be active mode")
    if config.crossfeed is None or not config.crossfeed.enabled:
        raise ContractError("hybrid profile must enable cross-feed")
    if config.counterfactual is None or not config.counterfactual.enabled:
        raise ContractError("hybrid profile must enable proposal generation")
    if config.hybrid_authority is None or not config.hybrid_authority.enabled:
        raise ContractError("hybrid profile must enable bounded live authority")
    if config.hybrid_authority.policy != "bounded_preanchor_v0":
        raise ContractError("unexpected live authority policy")
    if config.hybrid_authority.request_class != "movetime_v0":
        raise ContractError("unexpected live authority request class")
    if config.resource_measurement is None or not config.resource_measurement.enabled:
        raise ContractError("hybrid profile must enable physical measurement")
    if not config.resource_measurement.require_cpu_for_claim:
        raise ContractError("hybrid profile must require CPU measurement")
    if config.resource_measurement.require_gpu_for_claim:
        raise ContractError("v0 cannot require unsupported GPU device time")

    return {
        "status": "ok",
        "mode": config.mode,
        "policy": config.hybrid_authority.policy,
        "request_class": config.hybrid_authority.request_class,
    }


def _one_bestmove(lines: list[str]) -> str:
    moves = [
        line.split()[1].lower()
        for line in lines
        if line.startswith("bestmove ") and len(line.split()) >= 2
    ]
    if len(moves) != 1 or not _MOVE_RE.fullmatch(moves[0]):
        raise ContractError(f"expected exactly one canonical bestmove, got {moves}")
    return moves[0]


def _wait_for_new_final_artifact(
    replay_root: Path,
    known: set[str],
    *,
    timeout_s: float = 30.0,
) -> Path:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        discovery = discover_replay_bundles(replay_root)
        candidates = [
            path
            for path in discovery.bundles
            if path.name not in known
            and (path / "decision" / "final.json").is_file()
        ]
        if candidates:
            return candidates[-1]
        time.sleep(0.05)
    raise ContractError("real hybrid run did not seal decision/final.json")


def _real_contract() -> dict[str, object]:
    config = load_runtime_config(CONFIG_PATH)
    if config.hybrid_authority is None:
        raise ContractError("real contract requires hybrid_authority settings")
    if config.shadow is None:
        raise ContractError("real contract requires the active shadow substrate")
    for name, spec in config.backends.items():
        if not spec.binary.is_file():
            raise ContractError(f"real backend is not built: {name}: {spec.binary}")

    replay_root = config.shadow.replay_root
    replay_root.mkdir(parents=True, exist_ok=True)
    before = discover_replay_bundles(replay_root)
    known = {path.name for path in before.bundles} | {
        item.path.name for item in before.skipped
    }

    # Stay inside the profile's declared 7000 ms wall envelope while leaving
    # enough room for EXPLORE/VERIFY/optional REFINE to freeze pre-anchor.
    movetime_ms = 6000
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=30.0,
        args=["-m", "controller", "--config", str(CONFIG_PATH)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.send(f"go movetime {movetime_ms}")
        lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="M14-C real outward bestmove",
            timeout=90.0,
        )
        outward = _one_bestmove(lines)
        run_dir = _wait_for_new_final_artifact(replay_root, known)

    checks = {
        "parent": verify_bundle_integrity(run_dir),
        "verification": verify_verification_integrity(run_dir),
        "crossfeed": verify_crossfeed_integrity(run_dir),
        "counterfactual": verify_counterfactual_integrity(run_dir),
        "final_decision": verify_final_decision_integrity(run_dir),
    }
    failures = {key: value for key, value in checks.items() if value}
    if failures:
        raise ContractError(f"real hybrid evidence integrity failed: {failures}")

    parent = load_manifest(run_dir)
    artifact = load_final_decision_artifact(run_dir)
    decision = artifact.get("decision") or {}
    emitted = decision.get("emitted_move")
    authority = decision.get("authority")
    if emitted != outward:
        raise ContractError(
            f"stdout move {outward} differs from final certificate {emitted}"
        )
    if authority not in ("HYBRID", "ANCHOR_FALLBACK"):
        raise ContractError(f"unknown final authority {authority!r}")

    authorization = decision.get("authorization") or {}
    snapshot = decision.get("authorization_snapshot") or {}
    if authority == "HYBRID" and authorization.get("authorized") is not True:
        raise ContractError("HYBRID certificate lacks granted authorization")
    if authority == "ANCHOR_FALLBACK" and authorization.get("authorized") is not False:
        raise ContractError("ANCHOR_FALLBACK certificate carries a grant")
    if snapshot.get("request_class") != "movetime_v0":
        raise ContractError("real certificate lost movetime_v0 request class")
    if snapshot.get("request_eligible") is not True:
        raise ContractError("real movetime request was not classified eligible")
    if artifact.get("audit_complete") is not True:
        raise ContractError(
            f"real final decision audit is incomplete: {artifact.get('missing_sources')}"
        )

    anchor = next(
        stage for stage in parent.get("stages", [])
        if isinstance(stage, dict) and stage.get("role") == "anchor"
    )
    if authority == "ANCHOR_FALLBACK" and emitted != anchor.get("bestmove"):
        raise ContractError("fallback did not preserve the recorded Stockfish anchor")

    return {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "run_id": parent.get("run_id"),
        "movetime_ms": movetime_ms,
        "outward_bestmove": outward,
        "authority": authority,
        "anchor_bestmove": anchor.get("bestmove"),
        "proposal_move": decision.get("proposal_move"),
        "authorization_reason": authorization.get("reason"),
        "decision_id": artifact.get("decision_id"),
        "audit_complete": artifact.get("audit_complete"),
        "claim": (
            "Real-engine control-plane qualification only. This proves that the "
            "built Stockfish/Reckless/LC0 substrate can execute the bounded M14-C "
            "authority mechanism and seal auditable HYBRID/ANCHOR_FALLBACK "
            "evidence. It is not an Elo or move-quality claim."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real",
        action="store_true",
        help="run the built three-engine M14-C integration contract",
    )
    args = parser.parse_args(argv)

    profile = _profile_contract()
    if not args.real:
        print(json.dumps(profile, sort_keys=True))
        return 0

    report = _real_contract()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "active hybrid decision contract passed: "
        f"run={report['run_id']}, authority={report['authority']}, "
        f"outward={report['outward_bestmove']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"active hybrid decision contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
