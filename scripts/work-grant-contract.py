#!/usr/bin/env python3
"""Real-engine M14-J J9 WorkGrant mechanism contract.

The hosted runner currently reports its CPU quota as unknown, so the production
J8 planner correctly falls back there. This contract keeps the real
ENGINE-OPT-V2 binaries/processes and injects only a test-only conservative quota
observation so the ADAPTIVE J8->J9 mechanism can be exercised. It is mechanism
evidence, not deployment-host/composition qualification.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_go_request
from controller.replay import discover_replay_bundles, load_manifest, verify_bundle_integrity
from controller.routing import build_router
from controller.runtime import BackendManager
from controller.shadow import ShadowRunCoordinator
from controller.uci_frontend import UciFrontend


CONFIG = ROOT / "config/allfather.m14-j-j9.validation.json"
RESULT = ROOT / "build/test-results/engine-opt-v2-profile-domain/j9"
MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class J9IntegrationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise J9IntegrationError(message)


def wait_for(predicate, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise J9IntegrationError(f"timeout waiting for {label}")


def main() -> int:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    replay_root = ROOT / doc["shadow"]["replay_root"]
    known = {p.name for p in discover_replay_bundles(replay_root).bundles}

    manager = BackendManager.from_path(CONFIG)
    observed = manager._adaptive_host
    require(observed is not None, "J9 mechanism contract lacks a host observation")
    require(
        observed.allowed_cpus is not None and len(observed.allowed_cpus) >= 2,
        "J9 real-engine mechanism contract requires at least two visible CPUs",
    )
    require(
        observed.effective_memory_limit_bytes is not None
        and observed.effective_memory_limit_bytes >= 773 * 1024 * 1024,
        "J9 real-engine mechanism contract lacks sufficient observed memory",
    )

    require(
        observed.platform != "unknown" and observed.architecture != "unknown",
        "J9 mechanism contract requires observed platform/architecture",
    )
    synthetic_capacity_fields: list[str] = []
    replacement: dict[str, object] = {}
    if observed.cpu_quota_status == "unknown":
        # J8 correctly falls back for this hosted-runner ambiguity. J9's
        # positive witness is explicitly mechanism-only, so this process may
        # synthesize a conservative usable quota state without promoting it as
        # host/composition qualification.
        replacement["cpu_quota_status"] = "unlimited"
        replacement["cpu_quota_equivalents"] = None
        synthetic_capacity_fields.append("cpu_quota_status")
    if observed.cgroup_memory_status == "unknown":
        # We already require a positive observed effective memory ceiling above.
        # Give the synthetic HostCapabilities object an internally consistent
        # cgroup status/limit for this mechanism witness only; the report keeps
        # the synthetic fact explicit and never claims portability.
        replacement["cgroup_memory_status"] = "limited"
        replacement["cgroup_memory_limit_bytes"] = (
            observed.effective_memory_limit_bytes
        )
        synthetic_capacity_fields.append("cgroup_memory_status")
    if replacement:
        replacement["provider_id"] = "j9-real-engines-synthetic-capacity"
        replacement["capacity_complete"] = True
        # A test-only synthetic capacity fact must never accidentally turn a
        # real CPU identity into a complete qualification-domain claim. Degrade
        # that flag explicitly; J8's clamp needs capacity facts, not portable
        # host qualification.
        replacement["cpu_identity_complete"] = False
        replacement["qualification_domain_complete"] = False
        observed = replace(observed, **replacement)

    require(
        observed.cpu_quota_status in ("unlimited", "limited")
        and observed.cgroup_memory_status in ("unlimited", "limited")
        and observed.capacity_complete,
        "J9 mechanism host still lacks a complete synthetic capacity state",
    )
    manager._adaptive_host = observed
    manager._adaptive_host_error = None

    output = io.StringIO()
    shadow = None
    try:
        manager.start()
        frontend = UciFrontend(manager, output=output)
        shadow = ShadowRunCoordinator(
            manager,
            router=build_router(manager.config),
            diagnostic=frontend._diagnostic,
        )
        frontend.shadow = shadow
        frontend.handle_command("position startpos")
        frontend.handle_command("go movetime 4000")
        wait_for(
            lambda: any(
                line.startswith("bestmove ")
                for line in output.getvalue().splitlines()
            ),
            timeout=12,
            label="J9 outward bestmove",
        )
        frontend.handle_command("isready")
        wait_for(
            lambda: output.getvalue().splitlines().count("readyok") >= 1,
            timeout=15,
            label="J9 post-output ready barrier",
        )
        wait_for(
            lambda: any(
                p.name not in known
                and (p / "route.json").is_file()
                and (p / "manifest.json").is_file()
                and (p / "verification" / "manifest.json").is_file()
                and (p / "staged_verification" / "manifest.json").is_file()
                for p in discover_replay_bundles(replay_root).bundles
            ),
            timeout=15,
            label="J9 replay/VERIFY/staged evidence",
        )
        frontend.handle_command("quit")
    finally:
        if shadow is not None:
            try:
                shadow.close()
            except Exception:
                pass
        manager.close()

    candidates = [
        p
        for p in discover_replay_bundles(replay_root).bundles
        if p.name not in known and (p / "route.json").is_file()
    ]
    require(len(candidates) == 1, f"expected one J9 replay bundle, got {len(candidates)}")
    run = candidates[0]
    problems = verify_bundle_integrity(run)
    require(not problems, f"J9 replay integrity failed: {problems}")

    manifest = load_manifest(run)
    route = json.loads((run / "route.json").read_text(encoding="utf-8"))
    verification = json.loads(
        (run / "verification" / "manifest.json").read_text(encoding="utf-8")
    )
    staged = json.loads(
        (run / "staged_verification" / "manifest.json").read_text(encoding="utf-8")
    )

    move_plan = manifest.get("move_resource_plan") or {}
    require(
        move_plan.get("disposition") == "ADAPTIVE",
        f"J9 positive witness did not enter ADAPTIVE branch: {move_plan.get('disposition')!r}",
    )
    require(
        (move_plan.get("authority") or {}).get("outward_move") is False,
        "MoveResourcePlan gained outward authority",
    )

    scheduler = route.get("work_scheduler") or {}
    require(
        scheduler.get("policy_id") == "legacy_fixed_workgrant_v1",
        "route lacks J9 scheduler identity",
    )
    require(
        scheduler.get("move_resource_plan_id") == move_plan.get("plan_id"),
        "J9 scheduler is not bound to the replay MoveResourcePlan",
    )
    require(scheduler.get("settlement_complete") is True, "J9 settlement incomplete")
    events = scheduler.get("events")
    require(isinstance(events, list), "J9 scheduler events missing")

    authorized = [
        event
        for event in events
        if event.get("event") == "authorize" and event.get("granted") is True
    ]
    settled = [event for event in events if event.get("event") == "settle"]
    require(len(authorized) == 9, f"expected 9 admitted WorkGrants, got {len(authorized)}")
    require(len(settled) == 9, f"expected 9 settled WorkGrants, got {len(settled)}")
    require(
        len({event.get("grant_id") for event in authorized}) == 9,
        "J9 grant IDs are not unique",
    )
    require(
        len(
            {
                (event.get("allocation_round"), event.get("owner"))
                for event in authorized
            }
        )
        == 9,
        "J9 did not preserve one grant per owner/round",
    )

    stages = []
    for item in manifest.get("stages", []):
        if isinstance(item, dict) and item.get("role") == "shadow":
            stages.append(item)
    stages.extend(
        item for item in verification.get("stages", []) if isinstance(item, dict)
    )
    stages.extend(
        item for item in staged.get("stages", []) if isinstance(item, dict)
    )
    stage_by_id = {
        item.get("search_id"): item
        for item in stages
        if isinstance(item.get("search_id"), str)
    }

    expected_grid = {
        (0, family, "EXPLORE", 16)
        for family in ("stockfish", "reckless", "lc0")
    } | {
        (1, family, "VERIFY", 16)
        for family in ("stockfish", "reckless", "lc0")
    } | {
        (2, family, "STAGED_VERIFY", 32)
        for family in ("stockfish", "reckless", "lc0")
    }
    actual_grid = set()
    for event in authorized:
        grant = event.get("grant") or {}
        search_id = event.get("search_id")
        stage = stage_by_id.get(search_id)
        require(stage is not None, f"grant {event.get('grant_id')} has no replay stage")
        native = grant.get("native_limit") or {}
        nodes = native.get("value")
        phase = grant.get("phase")
        owner = grant.get("owner")
        round_ = grant.get("allocation_round")
        actual_grid.add((round_, owner, phase, nodes))
        require(
            native.get("kind") == "nodes"
            and native.get("semantics") == f"{owner}.uci_nodes",
            f"{search_id}: grant native semantics drift",
        )
        request = parse_go_request(str(stage.get("command")))
        limits = {
            row.get("name"): row.get("value")
            for row in request.get("limits", [])
            if isinstance(row, dict)
        }
        require(
            limits.get("nodes") == nodes,
            f"{search_id}: UCI request differs from WorkGrant native limit",
        )
        require(
            grant.get("move_resource_plan_id") == move_plan.get("plan_id"),
            f"{search_id}: grant parent MoveResourcePlan mismatch",
        )
        require(
            float(grant.get("wall_deadline_ms"))
            <= float(move_plan.get("soft_budget_ms")) + 1e-9,
            f"{search_id}: WorkGrant deadline exceeds parent soft deadline",
        )
        require(
            (grant.get("authority") or {}).get("outward_move") is False,
            f"{search_id}: WorkGrant gained outward move authority",
        )
    require(actual_grid == expected_grid, "J9 real-process grant grid differs from fixed contract")

    budget = route.get("budget") or {}
    require(budget.get("open_reservations") == 0, "J9 left open BudgetLedger reservations")
    require(
        (route.get("envelope_claim") or {}).get("work_grant_settlement_complete")
        is True,
        "route envelope claim reports unresolved WorkGrant settlement",
    )

    bestmoves = [
        line.split()[1]
        for line in output.getvalue().splitlines()
        if line.startswith("bestmove ") and len(line.split()) >= 2
    ]
    require(
        len(bestmoves) == 1 and MOVE_RE.fullmatch(bestmoves[0]) is not None,
        f"expected one canonical outward bestmove, got {bestmoves!r}",
    )
    anchor = [
        item
        for item in manifest.get("stages", [])
        if isinstance(item, dict) and item.get("role") == "anchor"
    ]
    require(len(anchor) == 1, "J9 replay must contain exactly one anchor stage")
    require(
        anchor[0].get("bestmove") == bestmoves[0],
        "J9 outward move differs from Stockfish anchor",
    )
    require(manifest.get("outward_decision") is None, "J9 gained hybrid DecisionAuthorization")

    report = {
        "schema_version": 1,
        "passed": True,
        "config": str(CONFIG.relative_to(ROOT)),
        "run_id": run.name,
        "synthetic_capacity_fields": synthetic_capacity_fields,
        "synthetic_capacity_observation": bool(synthetic_capacity_fields),
        "real_engine_processes": True,
        "move_resource_plan_id": move_plan.get("plan_id"),
        "move_resource_disposition": move_plan.get("disposition"),
        "work_scheduler_policy": scheduler.get("policy_id"),
        "work_grants_authorized": len(authorized),
        "work_grants_settled": len(settled),
        "open_reservations": budget.get("open_reservations"),
        "outward_move": bestmoves[0],
        "claim_boundary": {
            "workgrant_mechanism": True,
            "real_engine_processes": True,
            "deployment_host_qualification": False,
            "composition_qualification": False,
            "generic_host_portability": False,
            "adaptive_allocation": False,
            "runtime_profile_selection": False,
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
    print("J9 real-engine WorkGrant contract passed:", json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (J9IntegrationError, OSError, ValueError, RuntimeError) as exc:
        RESULT.mkdir(parents=True, exist_ok=True)
        (RESULT / "failure.txt").write_text(
            f"{type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        print(f"J9 real-engine WorkGrant contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
