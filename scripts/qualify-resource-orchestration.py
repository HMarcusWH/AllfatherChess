#!/usr/bin/env python3
"""M14-J J12 orchestrated-composition qualification.

Hosted runners may lack trustworthy cgroup capacity discovery.  In that case
the three-engine mechanism is exercised with an explicitly synthetic capacity
observation, but the J12 outward gate must reject it.  HYBRID qualification
requires a naturally complete, non-synthetic HostCapabilities path.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.decision import ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY
from controller.final_decision import (
    load_final_decision_artifact,
    verify_final_decision_integrity,
)
from controller.orchestration_integrity import (
    verify_orchestrated_composition_integrity,
)
from controller.replay import (
    discover_replay_bundles,
    load_manifest,
    verify_bundle_integrity,
)
from controller.routing import build_router
from controller.runtime import BackendManager, load_runtime_config
from controller.shadow import ShadowRunCoordinator
from controller.uci_frontend import UciFrontend
from tools.engine_opt.domain import (
    candidate_bundle_identity,
    load_execution_domain,
)


CONFIG = ROOT / "config/allfather.orchestrated-v1.validation.json"
RESULT = ROOT / "build/test-results/engine-opt-v2-profile-domain/j12"
MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
POSITIVE_CASE = {
    "id": "queen-pawn-white",
    "moves": ["d2d4", "d7d5"],
    "command": "go movetime 4000 searchmoves c2c4 g1f3 e2e3",
}


class J12QualificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise J12QualificationError(message)


def wait_for(predicate, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise J12QualificationError(f"timeout waiting for {label}")


def static_contract() -> dict[str, object]:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    for spec in document.get("instances", {}).values():
        spec["binary"] = sys.executable
        spec.pop("fallback_glob", None)
    document["root"] = "."
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "orchestrated-v1.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_runtime_config(path)
    require(config.orchestration is not None, "J12 requires MoveResourcePlan")
    require(config.work_scheduler is not None, "J12 requires WorkGrant scheduler")
    require(config.resource_allocator is not None, "J12 requires adaptive allocator")
    require(config.crossfeed is not None, "J12 requires crossfeed")
    require(config.counterfactual is not None, "J12 requires counterfactual")
    require(config.hybrid_authority is not None, "J12 requires hybrid authority")
    require(
        config.hybrid_authority.policy
        == ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        "J12 authority policy drift",
    )
    require(
        (config.routing or {}).get("policy") == "conservative_v1",
        "J12 must not introduce unified_value_v1 as a second allocator",
    )
    require(
        dict(config.shadow.dispatch_limit) == {"nodes": 16}
        and dict(config.verification.dispatch_limit) == {"nodes": 16}
        and dict(config.verification.staged_extension.dispatch_limit)
        == {"nodes": 32},
        "J12 frozen n16/n16/n32 grid drift",
    )
    return {
        "schema_version": 1,
        "profile_id": "allfather.orchestrated-v1",
        "static_contract": True,
        "passed": True,
    }


def mechanism_run() -> dict[str, object]:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    replay_root = ROOT / doc["shadow"]["replay_root"]
    replay_root.mkdir(parents=True, exist_ok=True)
    known = {p.name for p in discover_replay_bundles(replay_root).bundles}

    manager = BackendManager.from_path(CONFIG)
    observed = manager._adaptive_host
    require(observed is not None, "J12 mechanism contract lacks HostCapabilities")
    require(
        observed.allowed_cpus is not None and len(observed.allowed_cpus) >= 2,
        "J12 real-engine mechanism requires at least two visible CPUs",
    )
    require(
        observed.effective_memory_limit_bytes is not None
        and observed.effective_memory_limit_bytes >= 773 * 1024 * 1024,
        "J12 mechanism host lacks sufficient observed memory",
    )

    replacement: dict[str, object] = {}
    synthetic_fields: list[str] = []
    if observed.cpu_quota_status == "unknown":
        replacement["cpu_quota_status"] = "unlimited"
        replacement["cpu_quota_equivalents"] = None
        synthetic_fields.append("cpu_quota_status")
    if observed.cgroup_memory_status == "unknown":
        replacement["cgroup_memory_status"] = "limited"
        replacement["cgroup_memory_limit_bytes"] = (
            observed.effective_memory_limit_bytes
        )
        synthetic_fields.append("cgroup_memory_status")
    if replacement:
        replacement["provider_id"] = "j12-real-engines-synthetic-capacity"
        replacement["capacity_complete"] = True
        replacement["cpu_identity_complete"] = False
        replacement["qualification_domain_complete"] = False
        observed = replace(observed, **replacement)

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
        frontend.handle_command(
            "position startpos moves " + " ".join(POSITIVE_CASE["moves"])
        )
        frontend.handle_command(POSITIVE_CASE["command"])
        wait_for(
            lambda: any(
                line.startswith("bestmove ")
                for line in output.getvalue().splitlines()
            ),
            timeout=15,
            label="J12 outward bestmove",
        )
        frontend.handle_command("isready")
        wait_for(
            lambda: output.getvalue().splitlines().count("readyok") >= 1,
            timeout=20,
            label="J12 post-output barrier",
        )
        wait_for(
            lambda: any(
                p.name not in known
                and (p / "decision/final.json").is_file()
                and (p / "orchestration.json").is_file()
                for p in discover_replay_bundles(replay_root).bundles
            ),
            timeout=20,
            label="J12 final/orchestration evidence",
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
        if p.name not in known
        and (p / "decision/final.json").is_file()
        and (p / "orchestration.json").is_file()
    ]
    require(len(candidates) == 1, f"expected one J12 replay, got {len(candidates)}")
    run = candidates[0]

    parent_problems = verify_bundle_integrity(run)
    require(not parent_problems, f"J12 parent replay invalid: {parent_problems}")
    expected_source = os.environ.get("ALLFATHER_SOURCE_SHA")
    domain_path = os.environ.get("ALLFATHER_EXECUTION_DOMAIN_PATH")
    require(
        isinstance(domain_path, str) and domain_path,
        "J12 real qualification requires a bound execution domain",
    )
    execution_domain = load_execution_domain(
        Path(domain_path),
        expected_source_commit=expected_source,
    )
    candidate_bundle = candidate_bundle_identity(
        ROOT / "build/online-engine-opt-v2",
        expected_source_commit=expected_source,
    )
    orchestration_problems = verify_orchestrated_composition_integrity(
        run,
        root=ROOT,
        expected_source_commit=expected_source,
    )
    require(
        not orchestration_problems,
        f"J12 orchestration integrity failed: {orchestration_problems}",
    )
    final_problems = verify_final_decision_integrity(run)
    require(
        not final_problems,
        f"J12 final-decision integrity failed: {final_problems}",
    )

    manifest = load_manifest(run)
    route = json.loads((run / "route.json").read_text(encoding="utf-8"))
    final = load_final_decision_artifact(run)
    decision = final.get("decision") or {}
    authorization = decision.get("authorization") or {}
    snapshot = decision.get("authorization_snapshot") or {}
    allocation_rows = route.get("allocation_decisions") or []
    require(len(allocation_rows) == 1, "J12 requires one AllocationDecision")
    allocation = allocation_rows[0]
    require(
        allocation.get("action") == "BUY_BUNDLE",
        "unpromoted J12 must fail closed to BUY_BUNDLE",
    )
    scheduler = route.get("work_scheduler") or {}
    events = scheduler.get("events") or []
    authorized = [
        row for row in events
        if row.get("event") == "authorize" and row.get("granted") is True
    ]
    settled = [row for row in events if row.get("event") == "settle"]
    require(len(authorized) == 9, "J12 must authorize the exact 3x3 WorkGrant grid")
    require(len(settled) == 9, "J12 must settle all nine WorkGrants")
    require(
        (route.get("budget") or {}).get("open_reservations") == 0,
        "J12 left an open BudgetLedger reservation",
    )
    value_decisions = route.get("value_decisions") or []
    require(len(value_decisions) == 1, "J12 requires one allocation-route projection")
    projection = value_decisions[0]
    provenance = snapshot.get("orchestration_provenance") or {}
    require(
        projection.get("action") == "BUY_STAGED_VERIFY"
        and projection.get("buy_extension") is True
        and projection.get("allocation_decision_digest")
        == provenance.get("allocation_decision_digest"),
        "J12 route projection does not bind BUY_BUNDLE",
    )

    moves = [
        line.split()[1]
        for line in output.getvalue().splitlines()
        if line.startswith("bestmove ") and len(line.split()) >= 2
    ]
    require(
        len(moves) == 1 and MOVE_RE.fullmatch(moves[0]) is not None,
        f"expected one canonical J12 outward move, got {moves!r}",
    )
    require(
        decision.get("emitted_move") == moves[0],
        "J12 final certificate differs from stdout",
    )
    require(
        authorization.get("policy")
        == ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
        "J12 final decision uses the wrong authority policy",
    )

    synthetic = bool(synthetic_fields)
    authority_qualified = bool(
        not synthetic
        and decision.get("authority") == "HYBRID"
        and authorization.get("authorized") is True
        and decision.get("proposal_move") is not None
        and decision.get("proposal_move") != decision.get("anchor_move")
    )
    if synthetic:
        require(
            decision.get("authority") == "ANCHOR_FALLBACK"
            and authorization.get("authorized") is False,
            "synthetic J12 host evidence was allowed to reach HYBRID authority",
        )
        require(
            "linux-host-v2" in str(authorization.get("reason")),
            "synthetic host fallback did not identify the real-provider gate",
        )

    case = {
        "case": POSITIVE_CASE["id"],
        "command": POSITIVE_CASE["command"],
        "moves": list(POSITIVE_CASE["moves"]),
        "run_id": run.name,
        "authority": decision.get("authority"),
        "authorization_granted": authorization.get("authorized"),
        "anchor_move": decision.get("anchor_move"),
        "proposal_move": decision.get("proposal_move"),
        "emitted_move": decision.get("emitted_move"),
        "synthetic_capacity_observation": synthetic,
    }
    return {
        "schema_version": 1,
        "profile_id": "allfather.orchestrated-v1",
        "passed": authority_qualified,
        "source_commit": expected_source,
        "run_id": run.name,
        "execution_domain": execution_domain,
        "candidate_bundle": candidate_bundle,
        "cases": [case],
        "positive_case": case if authority_qualified else None,
        "mechanism_valid": True,
        "authority_qualified": authority_qualified,
        "qualification_disposition": (
            "QUALIFIED_ORCHESTRATED_AUTHORITY"
            if authority_qualified
            else (
                "NOT_QUALIFIED_HOST_CAPACITY"
                if synthetic
                else "NOT_QUALIFIED_POSITIVE_WITNESS"
            )
        ),
        "synthetic_capacity_observation": synthetic,
        "synthetic_capacity_fields": synthetic_fields,
        "allocation_action": allocation.get("action"),
        "work_grants_authorized": len(authorized),
        "work_grants_settled": len(settled),
        "open_reservations": (route.get("budget") or {}).get("open_reservations"),
        "route_projection": projection,
        "authority": decision.get("authority"),
        "authorization_granted": authorization.get("authorized"),
        "authorization_reason": authorization.get("reason"),
        "anchor_move": decision.get("anchor_move"),
        "proposal_move": decision.get("proposal_move"),
        "emitted_move": decision.get("emitted_move"),
        "claim_boundary": {
            "orchestrated_mechanism": True,
            "hybrid_authority": authority_qualified,
            "adaptive_stop_promoted": False,
            "generic_host_portability": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--record-disposition", action="store_true")
    args = parser.parse_args()

    static = static_contract()
    if args.static:
        print(json.dumps(static, sort_keys=True))
        return 0

    report = mechanism_run()
    RESULT.mkdir(parents=True, exist_ok=True)
    (RESULT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("J12 orchestration qualification:", json.dumps(report, sort_keys=True))
    if report["authority_qualified"]:
        return 0
    return 0 if args.record_disposition else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        RESULT.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": 1,
            "profile_id": "allfather.orchestrated-v1",
            "mechanism_valid": False,
            "authority_qualified": False,
            "qualification_disposition": "INVALID_EVIDENCE",
            "error": f"{type(exc).__name__}: {exc}",
        }
        (RESULT / "report.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise
