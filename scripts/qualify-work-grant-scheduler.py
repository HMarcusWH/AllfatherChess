#!/usr/bin/env python3
"""Independent static qualification for M14-J J9 WorkGrant compatibility."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.resource_profile_catalog import load_resource_profile_catalog
from controller.work_scheduler import (
    RESERVATION_BASIS,
    WORK_SCHEDULER_POLICY,
    WorkSchedulerSettings,
    load_work_scheduler_catalog,
)


J3 = ROOT / "qualification/resource-profile-catalog-v1.json"
J7 = ROOT / "qualification/resource-profile-selection-v1.json"
J8 = ROOT / "config/allfather.m14-j-j8.validation.json"
J9 = ROOT / "config/allfather.m14-j-j9.validation.json"
J9_CATALOG = ROOT / "qualification/work-grant-scheduler-v1.json"


class QualificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationError(message)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"{path}: {exc}") from exc
    require(isinstance(value, dict), f"{path}: root must be an object")
    return value


def qualify() -> dict:
    resource_catalog = load_resource_profile_catalog(J3)
    frozen, loaded_catalog = load_work_scheduler_catalog(
        J9_CATALOG,
        resource_catalog=resource_catalog,
    )
    j7 = load(J7)
    j8 = load(J8)
    j9 = load(J9)

    require(
        frozen.catalog_id == "work-grant-scheduler-v1"
        and frozen.policy_id == WORK_SCHEDULER_POLICY,
        "J9 scheduler identity drift",
    )
    require(
        frozen.resource_catalog_id == resource_catalog.catalog_id
        and frozen.resource_catalog_digest == resource_catalog.digest,
        "J9 compatibility overlay no longer binds the frozen J3 catalog",
    )
    require(
        loaded_catalog.digest == resource_catalog.digest,
        "J9 loader substituted a different J3 catalog",
    )
    require(
        resource_catalog.selection_enabled is False,
        "J9 may not enable J3 runtime profile selection",
    )
    require(
        all(
            not resource_catalog.profile(profile_id).work_chunk_ids
            for profile_id in (
                "stockfish/anchor-engine-opt-v2",
                "stockfish/specialist-engine-opt-v2",
                "reckless/specialist-engine-opt-v2",
                "lc0/specialist-engine-opt-v2",
            )
        ),
        "J9 must not mutate J3 profile work_chunk_ids",
    )
    require(
        frozen.reservation_basis == RESERVATION_BASIS
        and frozen.cost_bound_claim is False,
        "legacy reservation estimates were promoted into cost bounds",
    )

    require(len(frozen.chunks) == 9, "J9 must freeze exactly nine chunks")
    expected = {
        (0, family, "EXPLORE", 16)
        for family in ("stockfish", "reckless", "lc0")
    } | {
        (1, family, "VERIFY", 16)
        for family in ("stockfish", "reckless", "lc0")
    } | {
        (2, family, "STAGED_VERIFY", 32)
        for family in ("stockfish", "reckless", "lc0")
    }
    actual = {
        (
            row.allocation_round,
            row.chunk.family,
            row.chunk.phase,
            row.chunk.native_limit.value,
        )
        for row in frozen.chunks
    }
    require(actual == expected, "J9 round/family/native-limit grid drift")
    for row in frozen.chunks:
        require(
            row.chunk.native_limit.semantics
            == f"{row.chunk.family}.uci_nodes",
            f"{row.chunk.chunk_id}: native work semantics drift",
        )
        require(
            row.chunk.reserved_gpu_ms == 0.0,
            f"{row.chunk.chunk_id}: J9 compatibility path gained GPU budget",
        )
        require(
            row.chunk.wall_bound_ms == 4000.0,
            f"{row.chunk.chunk_id}: parent-ceiling wall bound drift",
        )

    scheduler = WorkSchedulerSettings.from_config(j9.get("work_scheduler"))
    require(scheduler is not None, "J9 scheduler is disabled")
    require(
        scheduler.policy == frozen.policy_id
        and scheduler.round_count == frozen.round_count
        and scheduler.max_grants_per_round == frozen.max_grants_per_round,
        "runtime J9 scheduler differs from frozen catalog",
    )

    # J9 is a strict additive delta over J8: replay root, fixed VERIFY/staged
    # VERIFY and work_scheduler. Nothing else may silently change.
    stripped = copy.deepcopy(j9)
    scheduler_raw = stripped.pop("work_scheduler", None)
    verification = stripped.pop("verification", None)
    require(isinstance(scheduler_raw, dict), "J9 work_scheduler block missing")
    require(
        verification
        == {
            "enabled": True,
            "nomination_method": "owner_bestmove_union_v1",
            "dispatch_limit": {"nodes": 16},
            "staged_extension": {
                "enabled": True,
                "intervention": "same_process_staged_verify_v1",
                "dispatch_limit": {"nodes": 32},
            },
        },
        "J9 VERIFY compatibility sequence drift",
    )
    stripped["shadow"]["replay_root"] = j8["shadow"]["replay_root"]
    require(
        stripped == j8,
        "J9 runtime changes J8 outside replay root / VERIFY / scheduler surfaces",
    )
    require("work_scheduler" not in j8, "merged J8 config was retroactively modified")

    require(
        j9.get("hybrid_authority") is None
        and j9.get("refinement") is None
        and j9.get("crossfeed") is None
        and j9.get("counterfactual") is None,
        "J9 validation profile widened into later authority/evidence stages",
    )
    require(
        (j9.get("routing") or {}).get("policy") == "conservative_v1"
        and (j9.get("routing") or {}).get("max_stages_per_owner") == 1,
        "J9 must remain fixed conservative compatibility scheduling",
    )

    stockfish = [
        spec
        for spec in j9["instances"].values()
        if spec["family"] == "stockfish"
    ]
    require(
        all(spec["options"].get("Hash") == 16 for spec in stockfish),
        "J7 Stockfish Hash=32 leaked into J9 runtime",
    )
    lc0 = next(
        spec for spec in j9["instances"].values()
        if spec["family"] == "lc0"
    )
    require(
        lc0["options"].get("MaxPrefetch") == 8,
        "J7 LC0 MaxPrefetch=0 leaked into J9 runtime",
    )
    require(
        j7.get("authority")
        == {
            "runtime_authority": False,
            "resource_authorization": False,
            "outward_move": False,
        },
        "J7 authority boundary changed",
    )
    require(
        (j7.get("claim_boundary") or {}).get("runtime_profile_selection")
        is False
        and (j7.get("claim_boundary") or {}).get("adaptive_allocation")
        is False,
        "J9 may not consume J7 adaptive/profile-selection authority",
    )

    return {
        "schema_version": 1,
        "qualified": True,
        "policy": frozen.policy_id,
        "catalog_id": frozen.catalog_id,
        "catalog_digest": frozen.digest,
        "resource_catalog_id": resource_catalog.catalog_id,
        "resource_catalog_digest": resource_catalog.digest,
        "round_count": frozen.round_count,
        "max_grants_per_round": frozen.max_grants_per_round,
        "compatibility_chunks": 9,
        "reservation_basis": frozen.reservation_basis,
        "cost_bound_claim": False,
        "authority": {
            "work_grant_resource_authorization": True,
            "outward_move": False,
        },
        "claim_boundary": {
            "adaptive_allocation": False,
            "runtime_profile_selection": False,
            "hybrid_authority": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
        "next": "M14-J/J10 deterministic adaptive allocator",
    }


def main() -> int:
    report = qualify()
    output = ROOT / "build/test-results/work-grant-scheduler/report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (QualificationError, ValueError, RuntimeError, OSError) as exc:
        print(f"J9 WorkGrant scheduler qualification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
