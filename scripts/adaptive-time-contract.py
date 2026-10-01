#!/usr/bin/env python3
"""Real-process M14-J J8 integration on the ENGINE-OPT-V2 bundle.

This qualifies the additive MoveResourcePlan plumbing only. The outward move
remains the Stockfish anchor; no J7 operating point, WorkGrant, or hybrid move
authority is promoted here.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.replay import (
    discover_replay_bundles,
    load_manifest,
    verify_bundle_integrity,
)
from tests.harness.uci_session import UciError, UciSession


CONFIG = ROOT / "config/allfather.m14-j-j8.validation.json"
RESULT = ROOT / "build/test-results/engine-opt-v2-profile-domain/j8"
MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class J8IntegrationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise J8IntegrationError(message)


def wait_bundle(root: Path, known: set[str]) -> Path:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for run in discover_replay_bundles(root).bundles:
            if (
                run.name not in known
                and (run / "route.json").is_file()
                and (run / "resource.json").is_file()
            ):
                return run
        time.sleep(0.05)
    raise J8IntegrationError("J8 replay/route/resource evidence did not finalize")


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    require(config.get("hybrid_authority") is None, "J8 runtime gained hybrid authority")
    require(config.get("verification") is None, "J8 runtime gained VERIFY")
    require(config.get("refinement") is None, "J8 runtime gained REFINE")
    require(config.get("crossfeed") is None, "J8 runtime gained crossfeed")
    require(config.get("counterfactual") is None, "J8 runtime gained counterfactual")
    require(
        (config.get("routing") or {}).get("policy") == "conservative_v1",
        "J8 runtime no longer uses conservative_v1",
    )

    replay_root = ROOT / config["shadow"]["replay_root"]
    known = {path.name for path in discover_replay_bundles(replay_root).bundles}
    command = "go movetime 1500"

    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=45,
        args=["-m", "controller", "--config", str(CONFIG)],
        start_new_session=True,
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.ready()
        started = time.monotonic()
        shell.send(command)
        lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="J8 bestmove",
            timeout=10,
        )
        bestmoves = [
            line.split()[1]
            for line in lines
            if line.startswith("bestmove ") and len(line.split()) >= 2
        ]
        require(
            len(bestmoves) == 1 and MOVE_RE.fullmatch(bestmoves[0]) is not None,
            f"expected one canonical outward bestmove, got {bestmoves!r}",
        )
        shell.send("isready")
        shell.read_until(
            lambda line: line == "readyok",
            label="J8 post-output ready barrier",
            timeout=15,
        )
        driver_ms = (time.monotonic() - started) * 1000.0
        run = wait_bundle(replay_root, known)

    problems = verify_bundle_integrity(run)
    require(not problems, f"J8 replay integrity failed: {problems}")
    manifest = load_manifest(run)
    route = json.loads((run / "route.json").read_text(encoding="utf-8"))
    resource = json.loads((run / "resource.json").read_text(encoding="utf-8"))

    time_plan = manifest.get("time_plan")
    move_plan = manifest.get("move_resource_plan")
    require(isinstance(time_plan, dict), "J8 replay lacks baseline TimePlan")
    require(isinstance(move_plan, dict), "J8 replay lacks MoveResourcePlan")
    require(
        route.get("move_resource_plan") == move_plan,
        "route/replay MoveResourcePlan mismatch",
    )
    require(
        move_plan.get("baseline_time_plan_id") == time_plan.get("plan_id"),
        "MoveResourcePlan does not bind its TimePlan",
    )
    require(
        move_plan.get("policy_id") == "adaptive_clock_envelope_v1",
        "wrong J8 policy identity",
    )
    require(
        time_plan.get("policy") == "clock_envelope_v1",
        "J8 rewrote the frozen TimePlan policy",
    )
    require(
        move_plan.get("soft_budget_ms") == time_plan.get("soft_budget_ms")
        and move_plan.get("hard_ceiling_ms") == time_plan.get("hard_budget_ms"),
        "J8 changed the TimePlan deadline",
    )

    j8_envelope = move_plan.get("resource_envelope") or {}
    parent_envelope = time_plan.get("envelope") or {}
    for name in ("wall_ms", "cpu_ms", "gpu_ms"):
        left = j8_envelope.get(name)
        right = parent_envelope.get(name)
        require(
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
            and float(left) <= float(right) + 1e-9,
            f"J8 {name} exceeded parent TimePlan",
        )
    require(float(j8_envelope["gpu_ms"]) == 0.0, "J8 runtime gained GPU budget")

    authority = move_plan.get("authority") or {}
    require(
        authority
        == {
            "resource_plan": True,
            "resource_authorization": False,
            "outward_move": False,
        },
        "J8 MoveResourcePlan authority marker changed",
    )
    claim = move_plan.get("claim_boundary") or {}
    require(claim.get("runtime_profile_selection") is False, "J8 consumed J7 selection")
    require(claim.get("work_grant") is False, "J8 created WorkGrant authority")
    require(claim.get("outward_move") is False, "J8 gained move authority")
    require(manifest.get("outward_decision") is None, "J8 emitted hybrid decision evidence")

    anchor = [
        stage
        for stage in manifest.get("stages", [])
        if isinstance(stage, dict) and stage.get("role") == "anchor"
    ]
    require(len(anchor) == 1, "J8 replay must contain exactly one anchor stage")
    require(
        anchor[0].get("bestmove") == bestmoves[0],
        "outward bestmove differs from Stockfish anchor",
    )

    report = {
        "schema_version": 1,
        "passed": True,
        "config": str(CONFIG.relative_to(ROOT)),
        "run_id": run.name,
        "outward_move": bestmoves[0],
        "driver_observed_ms": driver_ms,
        "time_plan_id": time_plan.get("plan_id"),
        "move_resource_plan_id": move_plan.get("plan_id"),
        "move_resource_disposition": move_plan.get("disposition"),
        "fallback_reason": move_plan.get("fallback_reason"),
        "host_capacity_claim": move_plan.get("host_capacity_claim"),
        "time_plan_envelope": parent_envelope,
        "move_resource_envelope": j8_envelope,
        "resource_qualified": resource.get("qualified"),
        "route_envelope_claimed": (route.get("envelope_claim") or {}).get("claimed"),
        "claim_boundary": {
            "real_process_move_resource_plan": True,
            "composition_qualification": False,
            "generic_host_portability": False,
            "work_grant": False,
            "hybrid_authority": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }
    RESULT.mkdir(parents=True, exist_ok=True)
    (RESULT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("J8 real-process contract passed:", json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (J8IntegrationError, UciError, OSError, ValueError, RuntimeError) as exc:
        RESULT.mkdir(parents=True, exist_ok=True)
        (RESULT / "failure.txt").write_text(
            f"{type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        print(f"J8 real-process contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
