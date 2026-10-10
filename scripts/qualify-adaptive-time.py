#!/usr/bin/env python3
"""Independent M14-J J8 adaptive outer-time qualification."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_position_command
from controller.adaptive_time import (
    ADAPTIVE_CLOCK_POLICY,
    ALLOCATOR_POLICY,
    FALLBACK_PROFILE,
    AdaptiveTimeSettings,
    build_move_resource_plan,
)
from controller.budget import ResourceEnvelope
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities
from controller.online_time import OnlineTimeSettings, make_time_plan
from controller.resource_profile_catalog import load_resource_profile_catalog


POLICY = ROOT / "qualification/adaptive-clock-v1.json"
BASELINE = ROOT / "config/allfather.online-engine-opt-v2.json"
RUNTIME = ROOT / "config/allfather.m14-j-j8.validation.json"
CATALOG_RUNTIME = ROOT / "config/allfather.online-hybrid-v2.validation.json"
CATALOG = ROOT / "qualification/resource-profile-catalog-v1.json"
ENGINE_SELECTION = ROOT / "qualification/engine-opt-v2-selection.json"
J7_SELECTION = ROOT / "qualification/resource-profile-selection-v1.json"


class J8QualificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise J8QualificationError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise J8QualificationError(f"{path}: {exc}") from exc
    require(isinstance(value, dict), f"{path}: root must be an object")
    return value


def host(
    cpus: int,
    *,
    quota: float | None = None,
    quota_unknown: bool = False,
    memory_mib: int = 8192,
) -> HostCapabilities:
    allowed = tuple(range(cpus))
    if quota_unknown:
        quota_status = "unknown"
        quota_value = None
    elif quota is None:
        quota_status = "unlimited"
        quota_value = None
    else:
        quota_status = "limited"
        quota_value = float(quota)
    memory_status = "limited"
    memory_bytes = memory_mib * 1024 * 1024
    capacity_complete = quota_status != "unknown"
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="j8-synthetic-host",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=cpus,
        affinity_cpus=allowed,
        cgroup_cpuset_effective=allowed,
        allowed_cpus=allowed,
        cpu_vendor_id=None,
        cpu_family=None,
        cpu_model=None,
        cpu_stepping=None,
        cpu_model_name=None,
        cpu_microcode=None,
        cpu_flags_intersection=(),
        cpu_feature_digest=None,
        cpu_identity_complete=False,
        cpu_quota_status=quota_status,
        cpu_quota_equivalents=quota_value,
        cpu_quota_observations=(),
        physical_core_count=None,
        smt_width=None,
        topology_complete=False,
        numa_nodes=(),
        numa_complete=False,
        physical_memory_bytes=max(memory_bytes, 1024 * 1024 * 1024),
        cgroup_memory_status=memory_status,
        cgroup_memory_limit_bytes=memory_bytes,
        effective_memory_limit_bytes=memory_bytes,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=capacity_complete,
        qualification_domain_complete=False,
        faults=(),
    )


def baseline_plan(config: dict[str, Any], command: str):
    settings = OnlineTimeSettings.from_config(config.get("online_time"))
    require(settings is not None, "baseline online_time is disabled")
    return make_time_plan(
        command=command,
        position=parse_position_command("position startpos"),
        generation=1,
        settings=settings,
        envelope=ResourceEnvelope.from_config(config.get("budget")),
        received_monotonic=10.0,
        controller_cpu_started_ns=100,
    )


def qualify() -> dict[str, Any]:
    policy = load(POLICY)
    baseline = load(BASELINE)
    runtime = load(RUNTIME)
    catalog_runtime = load(CATALOG_RUNTIME)
    engine_selection = load(ENGINE_SELECTION)
    j7 = load(J7_SELECTION)
    catalog = load_resource_profile_catalog(CATALOG)

    require(policy.get("schema_version") == 1, "J8 policy schema drift")
    require(policy.get("policy") == ADAPTIVE_CLOCK_POLICY, "J8 policy id drift")
    require(
        policy.get("baseline_clock_policy") == "clock_envelope_v1",
        "J8 baseline clock policy drift",
    )
    require(
        policy.get("allocator_policy_id") == ALLOCATOR_POLICY,
        "J8 allocator compatibility policy drift",
    )
    require(
        policy.get("fallback_profile") == FALLBACK_PROFILE,
        "J8 fallback profile drift",
    )
    require(
        policy.get("baseline_config") == "config/allfather.online-engine-opt-v2.json",
        "J8 baseline config drift",
    )
    require(
        policy.get("runtime_config") == "config/allfather.m14-j-j8.validation.json",
        "J8 runtime config drift",
    )
    require(
        policy.get("resource_catalog") == "qualification/resource-profile-catalog-v1.json",
        "J8 catalog path drift",
    )

    stripped = copy.deepcopy(runtime)
    orchestration = stripped.pop("orchestration", None)
    require(isinstance(orchestration, dict), "J8 runtime lacks orchestration block")
    stripped["shadow"]["replay_root"] = baseline["shadow"]["replay_root"]
    require(
        stripped == baseline,
        "J8 runtime changes frozen ENGINE-OPT-V2 behavior outside orchestration/replay path",
    )
    require(
        orchestration
        == {
            "enabled": True,
            "policy": "adaptive_clock_envelope_v1",
            "catalog": "qualification/resource-profile-catalog-v1.json",
            "composition_id": "composition/engine-opt-v2-exact-host",
            "fallback_clock_policy": "clock_envelope_v1",
            "fallback_profile": "engine-opt-v2",
            "allocator_policy_id": "legacy-fixed-stage-compat-v1",
            "concurrency": 1,
            "network_policy": "clock-envelope-v1",
        },
        "J8 runtime orchestration block drift",
    )
    settings = AdaptiveTimeSettings.from_config(orchestration)
    require(settings is not None, "J8 settings did not enable")

    require(catalog.selection_enabled is False, "J3 runtime selection became enabled")
    require(catalog.fallback_profile == "engine-opt-v2", "J3 fallback changed")
    require(
        catalog.default_composition_id == "composition/engine-opt-v2-exact-host",
        "J3 default composition changed",
    )
    # J3's frozen equivalence proof targets the qualified four-process hybrid
    # runtime that contains EXPLORE/VERIFY/STAGED_VERIFY limits. J8 itself
    # intentionally derives from the anchor-authoritative ENGINE-OPT-V2
    # runtime, so validate those two relationships separately rather than
    # pretending the J8 parent carries a verification block.
    catalog.validate_v2_equivalence(catalog_runtime, engine_selection)

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
        (j7.get("claim_boundary") or {}).get("runtime_profile_selection") is False
        and (j7.get("claim_boundary") or {}).get("adaptive_allocation") is False,
        "J8 may not consume J7 selections",
    )
    canonical_selected = engine_selection.get("selected") or {}
    canonical_lc0 = canonical_selected.get("lc0") or {}
    for name, spec in runtime["instances"].items():
        if spec["family"] == "stockfish":
            require(
                spec["options"].get("Hash") == (canonical_selected.get("stockfish") or {}).get("hash_mb"),
                f"{name}: runtime Stockfish Hash differs from canonical ENGINE-OPT selection",
            )
        if spec["family"] == "reckless":
            require(
                spec["options"].get("Hash") == (canonical_selected.get("reckless") or {}).get("hash_mb"),
                f"{name}: runtime Reckless Hash differs from canonical ENGINE-OPT selection",
            )
        if spec["family"] == "lc0":
            expected = {
                "NNCacheSize": canonical_lc0.get("nn_cache_size"),
                "MinibatchSize": canonical_lc0.get("minibatch_size"),
                "MaxPrefetch": canonical_lc0.get("max_prefetch"),
                "AdaptivePrefetch": canonical_lc0.get("adaptive_prefetch"),
                "DefectTelemetry": canonical_lc0.get("defect_telemetry"),
            }
            for option, value in expected.items():
                require(
                    spec["options"].get(option) == value,
                    f"{name}: runtime {option} differs from canonical ENGINE-OPT selection",
                )
            warmup = spec.get("warmup")
            warmup_nodes = canonical_lc0.get("warmup_nodes")
            require(
                (warmup is None if warmup_nodes is None else isinstance(warmup, dict) and warmup.get("nodes") == warmup_nodes),
                f"{name}: runtime warmup differs from canonical ENGINE-OPT selection",
            )

    composition = catalog.default_composition
    require(
        all(catalog.profile(binding.profile_id).accelerator.value == "cpu"
            for binding in composition.bindings),
        "J8 frozen composition is not CPU-only",
    )

    commands = {
        "30+1": "go wtime 1800000 btime 1800000 winc 1000 binc 1000",
        "10+5": "go wtime 600000 btime 600000 winc 5000 binc 5000",
        "3+2": "go wtime 180000 btime 180000 winc 2000 binc 2000",
        "movetime": "go movetime 1200",
        "movestogo": "go wtime 30000 btime 30000 movestogo 1",
    }
    scenarios: dict[str, Any] = {}
    for label, command in commands.items():
        base = baseline_plan(runtime, command)
        item = build_move_resource_plan(
            baseline=base,
            settings=settings,
            host=host(4),
            composition=composition,
            catalog_id=catalog.catalog_id,
            catalog_digest=catalog.digest,
        )
        require(item.disposition == "ADAPTIVE", f"{label}: adaptive path did not qualify")
        require(
            item.resource_envelope.wall_ms <= base.envelope.wall_ms
            and item.resource_envelope.cpu_ms <= base.envelope.cpu_ms
            and item.resource_envelope.gpu_ms == 0,
            f"{label}: adaptive plan exceeded baseline",
        )
        require(item.hard_ceiling_ms == base.hard_budget_ms, f"{label}: hard deadline changed")
        require(item.soft_budget_ms == base.soft_budget_ms, f"{label}: soft deadline changed")
        scenarios[label] = {
            "time_plan_id": base.as_dict()["plan_id"],
            "move_resource_plan_id": item.plan_id,
            "cpu_ms": item.resource_envelope.cpu_ms,
            "wall_ms": item.resource_envelope.wall_ms,
        }

    base = baseline_plan(runtime, "go movetime 1200")
    eight = build_move_resource_plan(
        baseline=base, settings=settings, host=host(8), composition=composition,
        catalog_id=catalog.catalog_id, catalog_digest=catalog.digest,
    )
    require(eight.effective_parallelism == 4.0, "8-CPU host escaped 4-slot composition")

    two = build_move_resource_plan(
        baseline=base, settings=settings, host=host(2), composition=composition,
        catalog_id=catalog.catalog_id, catalog_digest=catalog.digest,
    )
    require(two.effective_parallelism == 2.0, "2-CPU clamp is wrong")
    require(
        two.resource_envelope.cpu_ms <= two.resource_envelope.wall_ms * 2 + 1e-9,
        "2-CPU plan exceeded host clamp",
    )

    one_point_five = build_move_resource_plan(
        baseline=base, settings=settings, host=host(4, quota=1.5), composition=composition,
        catalog_id=catalog.catalog_id, catalog_digest=catalog.digest,
    )
    require(one_point_five.effective_parallelism == 1.5, "fractional quota clamp is wrong")
    require(
        one_point_five.resource_envelope.cpu_ms
        <= one_point_five.resource_envelope.wall_ms * 1.5 + 1e-9,
        "fractional quota plan exceeded host clamp",
    )

    unknown = build_move_resource_plan(
        baseline=base, settings=settings, host=host(4, quota_unknown=True), composition=composition,
        catalog_id=catalog.catalog_id, catalog_digest=catalog.digest,
    )
    require(unknown.disposition == "FALLBACK", "unknown quota must fall back")
    require(
        unknown.resource_envelope == base.envelope
        and unknown.host_capacity_claim is False,
        "fallback changed the frozen TimePlan envelope",
    )

    low_memory = build_move_resource_plan(
        baseline=base, settings=settings, host=host(4, memory_mib=512), composition=composition,
        catalog_id=catalog.catalog_id, catalog_digest=catalog.digest,
    )
    require(low_memory.disposition == "FALLBACK", "insufficient memory must fall back")

    huge_increment = baseline_plan(
        runtime,
        "go wtime 500 btime 500 winc 1000000 binc 1000000",
    )
    require(
        huge_increment.hard_budget_ms <= 400,
        "J8 baseline borrowed future increment into current move",
    )

    claim = policy.get("claim_boundary")
    require(
        claim
        == {
            "resource_plan": True,
            "resource_authorization": False,
            "runtime_profile_selection": False,
            "work_grant": False,
            "hybrid_authority": False,
            "outward_move": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
        "J8 claim boundary drift",
    )

    return {
        "schema_version": 1,
        "qualified": True,
        "policy": ADAPTIVE_CLOCK_POLICY,
        "catalog_id": catalog.catalog_id,
        "catalog_digest": catalog.digest,
        "composition_id": composition.composition_id,
        "scenarios": scenarios,
        "clamps": {
            "eight_cpu_effective_parallelism": eight.effective_parallelism,
            "two_cpu_effective_parallelism": two.effective_parallelism,
            "quota_1_5_effective_parallelism": one_point_five.effective_parallelism,
            "unknown_quota_disposition": unknown.disposition,
            "low_memory_disposition": low_memory.disposition,
        },
        "authority": {
            "resource_authorization": False,
            "outward_move": False,
        },
        "next": "M14-J/J9 WorkGrant scheduler",
    }


def main() -> int:
    report = qualify()
    output = ROOT / "build/test-results/adaptive-time/report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (J8QualificationError, ValueError, RuntimeError, OSError) as exc:
        print(f"J8 adaptive-time qualification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
