"""M14-J J11 independently sealed orchestration evidence.

J10 produces resource/allocation decisions while the controller is live.  J11
adds a post-run evidence plane that can be reconstructed without trusting
producer-written qualification booleans.  It never authorizes engine work and
never authorizes a chess move.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from common.search_request import parse_go_request
from controller.decision import (
    OrchestrationAuthorityProvenance,
    canonical_digest,
)
from controller.move_resource_plan import MoveResourcePlan
from controller.adaptive_time import verify_move_resource_plan_manifest
from controller.replay import (
    atomic_write_text,
    load_manifest,
    sha256_file,
    verify_bundle_integrity,
)
from controller.resource_allocator import (
    AllocationDecision,
    BUY_BUNDLE,
    load_allocation_policy,
)
from controller.verification import verify_verification_integrity
from controller.staged_verification import verify_staged_verification_integrity
from controller.work_grant import WorkGrant
from controller.work_scheduler import (
    WorkSchedulerSettings,
    build_work_scheduler,
)


ORCHESTRATION_INTEGRITY_SCHEMA_VERSION = 1
ORCHESTRATION_EVIDENCE_VERSION = "j11-orchestration-evidence-v1"
AUTHORITY_BINDING_VERSION = "j11-orchestration-authority-binding-v1"
RESOURCE_PLAN_PATH = "resource-plan.json"
ALLOCATION_TRACE_PATH = "resource/allocation.jsonl"
ORCHESTRATION_PATH = "orchestration.json"
ENGINE_BUNDLE_PATH = "engine-bundle.json"


class OrchestrationIntegrityError(RuntimeError):
    """Raised when J11 evidence cannot be sealed honestly."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OrchestrationIntegrityError(f"{label} must be an object")
    return dict(value)


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrchestrationIntegrityError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise OrchestrationIntegrityError(
            f"{label} must be finite and non-negative"
        )
    return number


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationIntegrityError(
            f"cannot load {label} {path}: {exc}"
        ) from exc
    return _mapping(value, label)


def _canonical_lines(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(
            dict(row),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
        for row in rows
    )


def allocation_trace_digest(route: Mapping[str, Any]) -> str:
    """Pre-output-stable identity available to future DecisionAuthorization."""

    scheduler = route.get("work_scheduler")
    scheduler_events = (
        scheduler.get("events", [])
        if isinstance(scheduler, Mapping)
        else []
    )
    decisions = route.get("allocation_decisions", [])
    contexts = route.get("allocation_contexts", [])
    if (
        not isinstance(decisions, list)
        or not isinstance(contexts, list)
        or not isinstance(scheduler_events, list)
    ):
        raise OrchestrationIntegrityError(
            "route allocation/context/work-grant evidence must be arrays"
        )
    return canonical_digest(
        {
            "allocation_decisions": decisions,
            "allocation_contexts": contexts,
            "work_grants": scheduler_events,
        }
    )


def _trace_rows(
    *,
    route: Mapping[str, Any],
    budget_journal: Sequence[Mapping[str, Any]],
    generation: int,
    position_id: str,
) -> list[dict[str, Any]]:
    decisions = route.get("allocation_decisions", [])
    contexts = route.get("allocation_contexts", [])
    scheduler = route.get("work_scheduler")
    work_events = (
        scheduler.get("events", [])
        if isinstance(scheduler, Mapping)
        else []
    )
    if (
        not isinstance(decisions, list)
        or not isinstance(contexts, list)
        or not isinstance(work_events, list)
    ):
        raise OrchestrationIntegrityError(
            "route allocation/context/work-grant evidence must be arrays"
        )
    rows: list[dict[str, Any]] = [
        {
            "record_type": "header",
            "schema_version": ORCHESTRATION_INTEGRITY_SCHEMA_VERSION,
            "run_id": route.get("run_id"),
            "generation": generation,
            "position_id": position_id,
        }
    ]
    rows.extend(
        {
            "record_type": "allocation_decision",
            "ordinal": index,
            "payload": value,
        }
        for index, value in enumerate(decisions)
    )
    rows.extend(
        {
            "record_type": "allocation_context",
            "ordinal": index,
            "payload": value,
        }
        for index, value in enumerate(contexts)
    )
    rows.extend(
        {
            "record_type": "work_grant_event",
            "ordinal": index,
            "payload": value,
        }
        for index, value in enumerate(work_events)
    )
    rows.extend(
        {
            "record_type": "budget_event",
            "ordinal": index,
            "payload": dict(value),
        }
        for index, value in enumerate(budget_journal)
    )
    return rows


def _terminal_grant_state(
    work_events: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    authorized: list[str] = []
    settled: list[str] = []
    released: list[str] = []
    unresolved: list[str] = []
    for raw in work_events:
        event = raw.get("event")
        grant_id = raw.get("grant_id")
        if not isinstance(grant_id, str):
            continue
        if event == "authorize" and raw.get("granted") is True:
            authorized.append(grant_id)
        elif event == "settle":
            settled.append(grant_id)
        elif event in ("release", "bundle_rollback"):
            released.append(grant_id)
        elif event == "finalize_unresolved":
            unresolved.append(grant_id)
    return authorized, settled, released, unresolved


def build_authority_binding(
    *,
    move_plan: MoveResourcePlan,
    route: Mapping[str, Any],
) -> dict[str, Any]:
    decisions = route.get("allocation_decisions")
    if not isinstance(decisions, list) or len(decisions) != 1:
        raise OrchestrationIntegrityError(
            "J11 requires exactly one J10 AllocationDecision"
        )
    decision = AllocationDecision.from_dict(decisions[0])
    scheduler = _mapping(route.get("work_scheduler"), "work_scheduler")
    events = scheduler.get("events")
    if not isinstance(events, list):
        raise OrchestrationIntegrityError("work_scheduler.events must be an array")
    authorized, settled, released, unresolved = _terminal_grant_state(events)
    if len(authorized) != len(set(authorized)):
        raise OrchestrationIntegrityError(
            "route contains duplicate authorized WorkGrant identities"
        )
    terminal_rows = [*settled, *released, *unresolved]
    if len(terminal_rows) != len(set(terminal_rows)):
        raise OrchestrationIntegrityError(
            "route contains multiple terminal events for one WorkGrant"
        )
    unknown_terminal = set(terminal_rows) - set(authorized)
    if unknown_terminal:
        raise OrchestrationIntegrityError(
            "route contains terminal WorkGrant events without authorization"
        )
    terminal = set(terminal_rows)
    open_ids = set(authorized) - terminal
    settlement_complete = bool(
        scheduler.get("settlement_complete") is True
        and not open_ids
        and not unresolved
    )
    host = move_plan.host_capabilities
    if host is None:
        raise OrchestrationIntegrityError(
            "J11 adaptive authority binding requires HostCapabilities"
        )
    return {
        "version": AUTHORITY_BINDING_VERSION,
        "move_resource_plan_id": move_plan.plan_id,
        "move_resource_plan_digest": move_plan.digest,
        "allocation_policy_digest": decision.allocation_policy_digest,
        "allocation_decision_digest": decision.digest,
        "allocation_trace_digest": allocation_trace_digest(route),
        "work_scheduler_catalog_digest": scheduler.get("catalog_digest"),
        "profile_catalog_digest": move_plan.catalog_digest,
        "composition_profile_digest": move_plan.composition.digest,
        "game_environment_digest": move_plan.game_environment.digest,
        "host_capabilities_digest": host.digest,
        "work_grant_settlement_complete": settlement_complete,
        "open_work_grant_reservations": len(open_ids),
        "authority": {
            "resource_evidence": True,
            "resource_authorization": False,
            "outward_move": False,
        },
    }


def seal_orchestration_evidence(
    run_dir: Path | str,
    *,
    move_plan: MoveResourcePlan,
    budget_journal: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Seal the non-circular J11 terminal evidence root after source finalization."""

    run_dir = Path(run_dir)
    route_path = run_dir / "route.json"
    resource_path = run_dir / "resource.json"
    replay_path = run_dir / "manifest.json"
    verification_path = run_dir / "verification" / "manifest.json"
    staged_path = run_dir / "staged_verification" / "manifest.json"
    repository_root = Path(__file__).resolve().parents[1]
    bundle_source_path = (
        repository_root / "build/online-engine-opt-v2/build-manifest.json"
    )
    for required in (
        replay_path,
        verification_path,
        staged_path,
        route_path,
        resource_path,
    ):
        if not required.is_file():
            raise OrchestrationIntegrityError(
                f"J11 terminal source is missing: {required.relative_to(run_dir)}"
            )
    if not bundle_source_path.is_file():
        raise OrchestrationIntegrityError(
            "J11 exact-head ENGINE-OPT-V2 build manifest is missing"
        )
    route = _load_json(route_path, "route")
    if route.get("run_id") is None:
        raise OrchestrationIntegrityError("route lacks run_id")
    if route.get("move_resource_plan") != move_plan.as_dict():
        raise OrchestrationIntegrityError(
            "route MoveResourcePlan differs from the live J11 parent"
        )
    bundle_path = run_dir / ENGINE_BUNDLE_PATH
    atomic_write_text(
        bundle_path,
        bundle_source_path.read_text(encoding="utf-8"),
    )
    bundle_manifest = _load_json(
        bundle_path,
        "ENGINE-OPT-V2 build manifest",
    )
    if (
        bundle_manifest.get("schema_version") != 1
        or bundle_manifest.get("profile_id") != "engine-opt-v2"
    ):
        raise OrchestrationIntegrityError(
            "J11 requires the exact ENGINE-OPT-V2 candidate build manifest"
        )

    plan_path = run_dir / RESOURCE_PLAN_PATH
    atomic_write_text(
        plan_path,
        json.dumps(move_plan.as_dict(), indent=2, sort_keys=True) + "\n",
    )

    rows = _trace_rows(
        route=route,
        budget_journal=budget_journal,
        generation=move_plan.generation,
        position_id=move_plan.position_id,
    )
    trace_path = run_dir / ALLOCATION_TRACE_PATH
    atomic_write_text(trace_path, _canonical_lines(rows))
    trace_digest = canonical_digest(rows)
    budget_digest = canonical_digest([dict(item) for item in budget_journal])

    binding = build_authority_binding(move_plan=move_plan, route=route)
    decision = AllocationDecision.from_dict(route["allocation_decisions"][0])
    scheduler = _mapping(route.get("work_scheduler"), "work_scheduler")
    work_events = scheduler.get("events") or []
    authorized, settled, released, unresolved = _terminal_grant_state(work_events)
    resource = _load_json(resource_path, "resource")

    core = {
        "schema_version": ORCHESTRATION_INTEGRITY_SCHEMA_VERSION,
        "evidence_version": ORCHESTRATION_EVIDENCE_VERSION,
        "run_id": route["run_id"],
        "generation": move_plan.generation,
        "position_id": move_plan.position_id,
        "replay": {
            "path": "manifest.json",
            "sha256": sha256_file(replay_path),
        },
        "verification": {
            "path": "verification/manifest.json",
            "sha256": sha256_file(verification_path),
        },
        "staged_verification": {
            "path": "staged_verification/manifest.json",
            "sha256": sha256_file(staged_path),
        },
        "engine_bundle": {
            "path": ENGINE_BUNDLE_PATH,
            "sha256": sha256_file(bundle_path),
            "source_commit": bundle_manifest.get("source_commit"),
            "source_tree": bundle_manifest.get("source_tree"),
        },
        "move_resource_plan": {
            "plan_id": move_plan.plan_id,
            "digest": move_plan.digest,
            "path": RESOURCE_PLAN_PATH,
            "sha256": sha256_file(plan_path),
        },
        "game_environment": {
            "environment_id": move_plan.game_environment.environment_id,
            "digest": move_plan.game_environment.digest,
        },
        "host_capabilities": {
            "host_id": move_plan.host_capabilities.capability_id
            if move_plan.host_capabilities is not None
            else None,
            "digest": move_plan.host_capabilities.digest
            if move_plan.host_capabilities is not None
            else None,
        },
        "composition_profile": {
            "composition_id": move_plan.composition.composition_id,
            "digest": move_plan.composition.digest,
        },
        "profile_catalog": {
            "catalog_id": move_plan.catalog_id,
            "digest": move_plan.catalog_digest,
        },
        "work_scheduler": {
            "policy_id": scheduler.get("policy_id"),
            "catalog_id": scheduler.get("catalog_id"),
            "catalog_digest": scheduler.get("catalog_digest"),
        },
        "allocation_policy": {
            "policy_id": decision.policy_id,
            "digest": decision.allocation_policy_digest,
        },
        "allocation_decision": {
            "allocation_id": decision.allocation_id,
            "digest": decision.digest,
            "action": decision.action,
            "selected_bundle_id": decision.selected_bundle_id,
        },
        "allocation_trace": {
            "path": ALLOCATION_TRACE_PATH,
            "sha256": sha256_file(trace_path),
            "digest": trace_digest,
            "authority_trace_digest": binding["allocation_trace_digest"],
            "budget_journal_digest": budget_digest,
            "budget_event_count": len(budget_journal),
        },
        "route": {
            "path": "route.json",
            "sha256": sha256_file(route_path),
        },
        "resource": {
            "path": "resource.json",
            "sha256": sha256_file(resource_path),
            "report_id": resource.get("report_id"),
        },
        "work_grants": {
            "authorized": len(authorized),
            "settled": len(settled),
            "released": len(released),
            "unresolved": len(unresolved),
            "settlement_complete": binding["work_grant_settlement_complete"],
            "open_reservations": binding["open_work_grant_reservations"],
        },
        "authority_binding": binding,
        "authority": {
            "resource_evidence": True,
            "resource_authorization": False,
            "outward_move": False,
        },
        "claim_boundary": {
            "allocation_provenance": True,
            "independent_reconstruction_required": True,
            "adaptive_stop_promoted": False,
            "hybrid_authority": False,
            "runtime_profile_selection": False,
            "generic_host_portability": False,
            "strength": False,
            "elo": False,
            "deployment": False,
        },
    }
    digest = canonical_digest(core)
    artifact = {
        **core,
        "evidence_id": f"orchestration/{digest}",
        "content_sha256": digest,
    }
    target = run_dir / ORCHESTRATION_PATH
    atomic_write_text(
        target,
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
    )
    return {
        "path": ORCHESTRATION_PATH,
        "sha256": sha256_file(target),
        "evidence_id": artifact["evidence_id"],
        "content_sha256": digest,
        "allocation_trace_digest": trace_digest,
        "authority_binding_digest": canonical_digest(binding),
    }


def _purpose_group(purpose: str) -> str:
    if purpose in ("solver", "anchor"):
        return "solver"
    if purpose in ("verify", "refine", "controller"):
        return purpose
    raise OrchestrationIntegrityError(
        f"budget journal has unknown purpose {purpose!r}"
    )


def replay_budget_journal(
    journal: Sequence[Mapping[str, Any]],
    *,
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently replay reservation affordability and settlement arithmetic."""

    cpu_limit = _finite_nonnegative(envelope.get("cpu_ms"), "envelope.cpu_ms")
    gpu_limit = _finite_nonnegative(envelope.get("gpu_ms"), "envelope.gpu_ms")
    caps = {
        "solver": (
            _finite_nonnegative(
                envelope.get("solver_cpu_ceiling_ms"),
                "solver_cpu_ceiling_ms",
            ),
            _finite_nonnegative(
                envelope.get("solver_gpu_ceiling_ms"),
                "solver_gpu_ceiling_ms",
            ),
        ),
        "verify": (
            _finite_nonnegative(
                envelope.get("verification_reserve_ms"),
                "verification_reserve_ms",
            ),
            _finite_nonnegative(
                envelope.get("verification_gpu_reserve_ms", 0.0),
                "verification_gpu_reserve_ms",
            ),
        ),
        "refine": (
            _finite_nonnegative(
                envelope.get("refinement_reserve_ms"),
                "refinement_reserve_ms",
            ),
            _finite_nonnegative(
                envelope.get("refinement_gpu_reserve_ms", 0.0),
                "refinement_gpu_reserve_ms",
            ),
        ),
        "controller": (
            _finite_nonnegative(
                envelope.get("controller_overhead_reserve_ms"),
                "controller_overhead_reserve_ms",
            ),
            0.0,
        ),
    }
    open_rows: dict[int, dict[str, Any]] = {}
    used_ids: set[int] = set()
    spent_cpu = {key: 0.0 for key in caps}
    spent_gpu = {key: 0.0 for key in caps}
    lane_spent: dict[str, dict[str, float]] = {}

    def totals() -> tuple[float, float]:
        reserved_cpu = sum(float(item["cpu_ms"]) for item in open_rows.values())
        reserved_gpu = sum(float(item["gpu_ms"]) for item in open_rows.values())
        return (
            reserved_cpu + sum(spent_cpu.values()),
            reserved_gpu + sum(spent_gpu.values()),
        )

    for index, raw in enumerate(journal, 1):
        row = _mapping(raw, f"budget journal[{index}]")
        if row.get("sequence") != index:
            raise OrchestrationIntegrityError(
                "budget journal sequence is not contiguous"
            )
        event = row.get("event")
        expected_keys = {
            "reserve": {
                "sequence",
                "event",
                "reservation_id",
                "lane",
                "purpose",
                "cpu_ms",
                "gpu_ms",
                "grant_id",
                "profile_id",
                "allocator_decision_digest",
            },
            "settle": {
                "sequence",
                "event",
                "reservation_id",
                "lane",
                "purpose",
                "declared_cpu_ms",
                "declared_gpu_ms",
                "actual_cpu_ms",
                "actual_gpu_ms",
                "cpu_source",
                "gpu_source",
                "grant_id",
                "profile_id",
                "allocator_decision_digest",
            },
            "release": {
                "sequence",
                "event",
                "reservation_id",
                "lane",
                "purpose",
                "cpu_ms",
                "gpu_ms",
                "grant_id",
                "profile_id",
                "allocator_decision_digest",
            },
            "controller_charge": {
                "sequence",
                "event",
                "lane",
                "purpose",
                "cpu_ms",
                "label",
            },
        }
        if event not in expected_keys or set(row) != expected_keys[event]:
            raise OrchestrationIntegrityError(
                f"budget journal event {event!r} has an invalid schema"
            )
        if event == "reserve":
            reservation_id = row.get("reservation_id")
            if (
                isinstance(reservation_id, bool)
                or not isinstance(reservation_id, int)
                or reservation_id <= 0
                or reservation_id in used_ids
            ):
                raise OrchestrationIntegrityError(
                    "budget journal reservation id is invalid or reused"
                )
            purpose = str(row.get("purpose"))
            group = _purpose_group(purpose)
            cpu = _finite_nonnegative(row.get("cpu_ms"), "reserve.cpu_ms")
            gpu = _finite_nonnegative(row.get("gpu_ms"), "reserve.gpu_ms")
            total_cpu, total_gpu = totals()
            group_reserved_cpu = sum(
                float(item["cpu_ms"])
                for item in open_rows.values()
                if item["group"] == group
            )
            group_reserved_gpu = sum(
                float(item["gpu_ms"])
                for item in open_rows.values()
                if item["group"] == group
            )
            if cpu > min(
                cpu_limit - total_cpu,
                caps[group][0] - spent_cpu[group] - group_reserved_cpu,
            ) + 1e-9:
                raise OrchestrationIntegrityError(
                    f"reservation {reservation_id} was not CPU-affordable when admitted"
                )
            if gpu > min(
                gpu_limit - total_gpu,
                caps[group][1] - spent_gpu[group] - group_reserved_gpu,
            ) + 1e-9:
                raise OrchestrationIntegrityError(
                    f"reservation {reservation_id} was not GPU-affordable when admitted"
                )
            used_ids.add(reservation_id)
            open_rows[reservation_id] = {
                **row,
                "cpu_ms": cpu,
                "gpu_ms": gpu,
                "group": group,
            }
            continue

        if event in ("settle", "release"):
            reservation_id = row.get("reservation_id")
            reservation = open_rows.pop(reservation_id, None)
            if reservation is None:
                raise OrchestrationIntegrityError(
                    f"{event} references a reservation that is not open"
                )
            for key in (
                "lane",
                "purpose",
                "grant_id",
                "profile_id",
                "allocator_decision_digest",
            ):
                if row.get(key) != reservation.get(key):
                    raise OrchestrationIntegrityError(
                        f"{event} provenance differs from reservation for {key}"
                    )
            if event == "release":
                released_cpu = _finite_nonnegative(
                    row.get("cpu_ms"),
                    "release.cpu_ms",
                )
                released_gpu = _finite_nonnegative(
                    row.get("gpu_ms"),
                    "release.gpu_ms",
                )
                if (
                    abs(released_cpu - float(reservation["cpu_ms"])) > 1e-9
                    or abs(released_gpu - float(reservation["gpu_ms"])) > 1e-9
                ):
                    raise OrchestrationIntegrityError(
                        "release amount differs from reservation"
                    )
            if event == "settle":
                declared_cpu = _finite_nonnegative(
                    row.get("declared_cpu_ms"),
                    "settle.declared_cpu_ms",
                )
                declared_gpu = _finite_nonnegative(
                    row.get("declared_gpu_ms"),
                    "settle.declared_gpu_ms",
                )
                if (
                    abs(declared_cpu - float(reservation["cpu_ms"])) > 1e-9
                    or abs(declared_gpu - float(reservation["gpu_ms"])) > 1e-9
                ):
                    raise OrchestrationIntegrityError(
                        "settlement declaration differs from reservation"
                    )
                actual_cpu = _finite_nonnegative(
                    row.get("actual_cpu_ms"),
                    "settle.actual_cpu_ms",
                )
                actual_gpu = _finite_nonnegative(
                    row.get("actual_gpu_ms"),
                    "settle.actual_gpu_ms",
                )
                valid_sources = {
                    "measured",
                    "estimated_fallback",
                    "declared_fallback",
                }
                if (
                    row.get("cpu_source") not in valid_sources
                    or row.get("gpu_source") not in valid_sources
                ):
                    raise OrchestrationIntegrityError(
                        "settlement source is unsupported"
                    )
                group = reservation["group"]
                spent_cpu[group] += actual_cpu
                spent_gpu[group] += actual_gpu
                lane = str(reservation.get("lane"))
                account = lane_spent.setdefault(
                    lane,
                    {
                        "spent_cpu_ms": 0.0,
                        "spent_gpu_ms": 0.0,
                        "settlements": 0.0,
                    },
                )
                account["spent_cpu_ms"] += actual_cpu
                account["spent_gpu_ms"] += actual_gpu
                account["settlements"] += 1.0
            continue

        if event == "controller_charge":
            group = _purpose_group(str(row.get("purpose")))
            if group != "controller":
                raise OrchestrationIntegrityError(
                    "controller_charge must use controller purpose"
                )
            cpu = _finite_nonnegative(
                row.get("cpu_ms"),
                "controller_charge.cpu_ms",
            )
            spent_cpu[group] += cpu
            lane = str(row.get("lane"))
            account = lane_spent.setdefault(
                lane,
                {
                    "spent_cpu_ms": 0.0,
                    "spent_gpu_ms": 0.0,
                    "settlements": 0.0,
                },
            )
            account["spent_cpu_ms"] += cpu
            continue

        raise OrchestrationIntegrityError(
            f"unsupported budget journal event {event!r}"
        )

    total_cpu, total_gpu = totals()
    return {
        "open_reservations": len(open_rows),
        "committed_cpu_ms": total_cpu,
        "committed_gpu_ms": total_gpu,
        "purpose_spent_cpu_ms": spent_cpu,
        "purpose_spent_gpu_ms": spent_gpu,
        "lanes": lane_spent,
        "within_envelope": total_cpu <= cpu_limit + 1e-9
        and total_gpu <= gpu_limit + 1e-9,
        "within_partition_caps": all(
            spent_cpu[key]
            + sum(
                float(item["cpu_ms"])
                for item in open_rows.values()
                if item["group"] == key
            )
            <= caps[key][0] + 1e-9
            and spent_gpu[key]
            + sum(
                float(item["gpu_ms"])
                for item in open_rows.values()
                if item["group"] == key
            )
            <= caps[key][1] + 1e-9
            for key in caps
        ),
    }


def _read_trace(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OrchestrationIntegrityError(
            f"cannot read allocation trace {path}: {exc}"
        ) from exc
    for number, line in enumerate(lines, 1):
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OrchestrationIntegrityError(
                f"allocation trace line {number} is invalid JSON: {exc}"
            ) from exc
        rows.append(_mapping(value, f"allocation trace line {number}"))
    return rows


def _approx_equal(left: Any, right: Any, tolerance: float = 0.002) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _stage_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    paths = (
        run_dir / "manifest.json",
        run_dir / "verification" / "manifest.json",
        run_dir / "staged_verification" / "manifest.json",
    )
    for path in paths:
        if not path.is_file():
            continue
        doc = _load_json(path, str(path))
        stages = doc.get("stages", [])
        if not isinstance(stages, list):
            continue
        for raw in stages:
            if not isinstance(raw, dict):
                continue
            search_id = raw.get("search_id")
            if isinstance(search_id, str):
                if search_id in result:
                    raise OrchestrationIntegrityError(
                        f"duplicate search_id across sealed stage manifests: {search_id}"
                    )
                result[search_id] = raw
    return result


def verify_orchestration_integrity(
    run_dir: Path | str,
    *,
    root: Path | str | None = None,
    expected_source_commit: str | None = None,
) -> list[str]:
    """Independently reconstruct J11 provenance. Empty list means valid."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    if root is None:
        problems.append(
            "J11 independent qualification requires the repository root for frozen policy reconstruction"
        )
    try:
        artifact = _load_json(run_dir / ORCHESTRATION_PATH, "orchestration evidence")
    except OrchestrationIntegrityError as exc:
        return [str(exc)]

    expected_top_level_keys = {
        "schema_version",
        "evidence_version",
        "run_id",
        "generation",
        "position_id",
        "replay",
        "verification",
        "staged_verification",
        "engine_bundle",
        "move_resource_plan",
        "game_environment",
        "host_capabilities",
        "composition_profile",
        "profile_catalog",
        "work_scheduler",
        "allocation_policy",
        "allocation_decision",
        "allocation_trace",
        "route",
        "resource",
        "work_grants",
        "authority_binding",
        "authority",
        "claim_boundary",
        "evidence_id",
        "content_sha256",
    }
    if set(artifact) != expected_top_level_keys:
        problems.append("orchestration top-level schema keys are invalid")

    expected_authority = {
        "resource_evidence": True,
        "resource_authorization": False,
        "outward_move": False,
    }
    expected_claim_boundary = {
        "allocation_provenance": True,
        "independent_reconstruction_required": True,
        "adaptive_stop_promoted": False,
        "hybrid_authority": False,
        "runtime_profile_selection": False,
        "generic_host_portability": False,
        "strength": False,
        "elo": False,
        "deployment": False,
    }
    if artifact.get("schema_version") != ORCHESTRATION_INTEGRITY_SCHEMA_VERSION:
        problems.append("unsupported orchestration schema_version")
    if artifact.get("evidence_version") != ORCHESTRATION_EVIDENCE_VERSION:
        problems.append("unsupported orchestration evidence_version")
    if artifact.get("authority") != expected_authority:
        problems.append("orchestration authority boundary is invalid")
    if artifact.get("claim_boundary") != expected_claim_boundary:
        problems.append("orchestration claim boundary is invalid")

    content_sha = artifact.get("content_sha256")
    evidence_id = artifact.get("evidence_id")
    core = {
        key: value
        for key, value in artifact.items()
        if key not in ("content_sha256", "evidence_id")
    }
    expected_content = canonical_digest(core)
    if content_sha != expected_content:
        problems.append("orchestration content digest mismatch")
    if evidence_id != f"orchestration/{expected_content}":
        problems.append("orchestration evidence_id mismatch")

    expected_source_paths = {
        "replay": "manifest.json",
        "verification": "verification/manifest.json",
        "staged_verification": "staged_verification/manifest.json",
        "engine_bundle": ENGINE_BUNDLE_PATH,
        "move_resource_plan": RESOURCE_PLAN_PATH,
        "allocation_trace": ALLOCATION_TRACE_PATH,
        "route": "route.json",
        "resource": "resource.json",
    }
    for section, expected_relative in expected_source_paths.items():
        row = artifact.get(section)
        if not isinstance(row, dict):
            problems.append(f"orchestration {section} section is missing")
            continue
        relative = row.get("path")
        stored_sha = row.get("sha256")
        if relative != expected_relative:
            problems.append(
                f"orchestration {section} path differs from the frozen J11 layout"
            )
            continue
        if (
            not isinstance(stored_sha, str)
            or len(stored_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in stored_sha)
        ):
            problems.append(f"orchestration {section} SHA is invalid")
            continue
        source = run_dir / expected_relative
        if not source.is_file():
            problems.append(f"orchestration source missing: {expected_relative}")
        elif sha256_file(source) != stored_sha:
            problems.append(
                f"orchestration source hash mismatch: {expected_relative}"
            )

    manifest: dict[str, Any] | None = None
    try:
        manifest = load_manifest(run_dir)
    except Exception as exc:
        problems.append(f"parent replay manifest cannot be loaded: {exc}")
    if manifest is not None:
        if manifest.get("outward_decision") is not None:
            problems.append(
                "J11 qualification is evidence-only and may not contain outward DecisionAuthorization"
            )
        if root is not None:
            frozen_config_path = (
                Path(root) / "config/allfather.m14-j-j10.validation.json"
            )
            try:
                frozen_config_sha = sha256_file(frozen_config_path)
                controller = manifest.get("controller")
                if not isinstance(controller, dict):
                    problems.append("parent replay controller identity is missing")
                elif controller.get("config_sha256") != frozen_config_sha:
                    problems.append(
                        "parent replay is not bound to the frozen J10 qualification config"
                    )
            except Exception as exc:
                problems.append(
                    f"frozen J10 config identity could not be reconstructed: {exc}"
                )
        if artifact.get("run_id") != manifest.get("run_id"):
            problems.append("orchestration run_id differs from parent replay")
        if artifact.get("generation") != manifest.get("generation"):
            problems.append("orchestration generation differs from parent replay")
        manifest_position = manifest.get("position")
        if (
            not isinstance(manifest_position, dict)
            or artifact.get("position_id") != manifest_position.get("position_id")
        ):
            problems.append("orchestration position differs from parent replay")
        for problem in verify_move_resource_plan_manifest(manifest):
            problems.append(problem)
        for problem in verify_bundle_integrity(run_dir):
            problems.append(f"parent replay integrity: {problem}")
        for problem in verify_verification_integrity(run_dir):
            problems.append(f"VERIFY integrity: {problem}")
        for problem in verify_staged_verification_integrity(run_dir):
            problems.append(f"staged VERIFY integrity: {problem}")

    engine_bundle: dict[str, Any] = {}
    try:
        engine_bundle = _load_json(
            run_dir / ENGINE_BUNDLE_PATH,
            "ENGINE-OPT-V2 build manifest",
        )
        if (
            engine_bundle.get("schema_version") != 1
            or engine_bundle.get("profile_id") != "engine-opt-v2"
        ):
            problems.append("sealed engine bundle has the wrong profile/schema")
        if expected_source_commit is not None:
            if (
                not isinstance(expected_source_commit, str)
                or len(expected_source_commit) not in (40, 64)
                or any(
                    ch not in "0123456789abcdef"
                    for ch in expected_source_commit
                )
            ):
                problems.append(
                    "expected source commit is not a lowercase Git object id"
                )
            elif engine_bundle.get("source_commit") != expected_source_commit:
                problems.append(
                    "sealed ENGINE-OPT-V2 candidate was not built from the expected source commit"
                )
        stored_bundle_summary = artifact.get("engine_bundle")
        expected_bundle_summary = {
            "path": ENGINE_BUNDLE_PATH,
            "sha256": sha256_file(run_dir / ENGINE_BUNDLE_PATH),
            "source_commit": engine_bundle.get("source_commit"),
            "source_tree": engine_bundle.get("source_tree"),
        }
        if stored_bundle_summary != expected_bundle_summary:
            problems.append("orchestration engine-bundle summary does not reconstruct")
        if root is not None:
            root_path = Path(root)
            contracts = _mapping(
                engine_bundle.get("contracts"),
                "ENGINE-OPT-V2 build contracts",
            )
            expected_contract_files = {
                "vendor_lock_sha256": "vendor.lock.json",
                "policy_sha256": "qualification/online-engine-opt-v2.json",
                "runtime_config_sha256": "config/allfather.online-engine-opt-v2.json",
                "selection_sha256": "qualification/engine-opt-v2-selection.json",
                "evidence_sha256": "qualification/engine-opt-v2-evidence.json",
                "derived_lock_sha256": "qualification/engine-derived-lock.json",
                "lc0_strength_lock_sha256": "qualification/lc0-strength.lock.json",
                "lc0_strength_profile_sha256": "qualification/lc0-strength-profile.json",
            }
            for key, relative in expected_contract_files.items():
                source = root_path / relative
                if contracts.get(key) != sha256_file(source):
                    problems.append(
                        f"sealed engine bundle contract {key} differs from repository source"
                    )
            derived = _load_json(
                root_path / "qualification/engine-derived-lock.json",
                "derived engine lock",
            )
            expected_trees = {
                family: (derived.get("engines") or {}).get(family, {}).get(
                    "derived_tree"
                )
                for family in ("stockfish", "reckless", "lc0")
            }
            if engine_bundle.get("derived_engine_trees") != expected_trees:
                problems.append(
                    "sealed engine bundle derived-engine trees differ from frozen lock"
                )
    except Exception as exc:
        problems.append(f"engine bundle reconstruction failed: {exc}")

    try:
        plan_raw = _load_json(run_dir / RESOURCE_PLAN_PATH, "resource plan")
        plan = MoveResourcePlan.from_dict(plan_raw)
    except Exception as exc:
        problems.append(f"MoveResourcePlan reconstruction failed: {exc}")
        plan = None

    try:
        route = _load_json(run_dir / "route.json", "route")
    except OrchestrationIntegrityError as exc:
        problems.append(str(exc))
        route = {}

    if route:
        if route.get("schema_version") != 2:
            problems.append("route schema_version differs from frozen J10 contract")
        if root is not None:
            try:
                from controller.routing import RoutingPolicy

                frozen_config = _load_json(
                    Path(root) / "config/allfather.m14-j-j10.validation.json",
                    "frozen J10 config",
                )
                expected_routing = RoutingPolicy.from_config(
                    _mapping(frozen_config.get("routing"), "frozen routing")
                )
                if route.get("policy") != expected_routing.policy_name:
                    problems.append(
                        "route policy differs from frozen J10 routing policy"
                    )
                if route.get("thresholds") != expected_routing.as_dict():
                    problems.append(
                        "route thresholds differ from frozen J10 routing policy"
                    )
            except Exception as exc:
                problems.append(
                    f"frozen J10 routing policy could not be reconstructed: {exc}"
                )

    if plan is not None:
        stored_plan = artifact.get("move_resource_plan") or {}
        expected_plan_summary = {
            "plan_id": plan.plan_id,
            "digest": plan.digest,
            "path": RESOURCE_PLAN_PATH,
            "sha256": sha256_file(run_dir / RESOURCE_PLAN_PATH),
        }
        if stored_plan != expected_plan_summary:
            problems.append(
                "orchestration MoveResourcePlan summary does not reconstruct"
            )
        if manifest is not None:
            if manifest.get("move_resource_plan") != plan.as_dict():
                problems.append(
                    "parent replay MoveResourcePlan differs from sealed resource plan"
                )
            time_plan = manifest.get("time_plan")
            if not isinstance(time_plan, dict):
                problems.append(
                    "parent replay lacks the TimePlan bound by MoveResourcePlan"
                )
            elif time_plan.get("plan_id") != plan.baseline_time_plan_id:
                problems.append(
                    "MoveResourcePlan baseline TimePlan identity differs from replay"
                )
        if stored_plan.get("plan_id") != plan.plan_id:
            problems.append("orchestration MoveResourcePlan id mismatch")
        if stored_plan.get("digest") != plan.digest:
            problems.append("orchestration MoveResourcePlan digest mismatch")
        if route.get("move_resource_plan") != plan.as_dict():
            problems.append("route MoveResourcePlan differs from sealed plan")
        if route.get("run_id") != artifact.get("run_id"):
            problems.append("route/orchestration run_id mismatch")
        if artifact.get("generation") != plan.generation:
            problems.append("orchestration generation differs from MoveResourcePlan")
        if artifact.get("position_id") != plan.position_id:
            problems.append("orchestration position differs from MoveResourcePlan")
        expected_environment = {
            "environment_id": plan.game_environment.environment_id,
            "digest": plan.game_environment.digest,
        }
        if artifact.get("game_environment") != expected_environment:
            problems.append("game environment identity does not reconstruct")
        expected_composition = {
            "composition_id": plan.composition.composition_id,
            "digest": plan.composition.digest,
        }
        if artifact.get("composition_profile") != expected_composition:
            problems.append("composition profile identity does not reconstruct")
        expected_catalog = {
            "catalog_id": plan.catalog_id,
            "digest": plan.catalog_digest,
        }
        if artifact.get("profile_catalog") != expected_catalog:
            problems.append("profile catalog identity does not reconstruct")
        host = plan.host_capabilities
        if host is None:
            problems.append("adaptive J11 evidence lacks HostCapabilities")
        else:
            expected_host = {
                "host_id": host.capability_id,
                "digest": host.digest,
            }
            if artifact.get("host_capabilities") != expected_host:
                problems.append("HostCapabilities identity does not reconstruct")
            allowed = host.allowed_cpus
            if allowed is None or len(allowed) < plan.composition.declared_cpu_slots:
                problems.append("composition CPU slots exceed observed allowed CPUs")
            if (
                plan.effective_parallelism is None
                or plan.effective_parallelism
                > float(len(allowed or ())) + 1e-9
            ):
                problems.append("MoveResourcePlan effective parallelism exceeds host visibility")
            if (
                host.effective_memory_limit_bytes is not None
                and plan.composition.expected_memory_mib * 1024**2
                > host.effective_memory_limit_bytes
            ):
                problems.append("composition resident memory exceeds observed host limit")

    decisions = route.get("allocation_decisions")
    decision: AllocationDecision | None = None
    if not isinstance(decisions, list) or len(decisions) != 1:
        problems.append("J11 route must contain exactly one AllocationDecision")
    else:
        try:
            decision = AllocationDecision.from_dict(decisions[0])
            expected_decision_summary = {
                "allocation_id": decision.allocation_id,
                "digest": decision.digest,
                "action": decision.action,
                "selected_bundle_id": decision.selected_bundle_id,
            }
            if artifact.get("allocation_decision") != expected_decision_summary:
                problems.append(
                    "orchestration AllocationDecision summary does not reconstruct"
                )
            expected_policy_summary = {
                "policy_id": decision.policy_id,
                "digest": decision.allocation_policy_digest,
            }
            if artifact.get("allocation_policy") != expected_policy_summary:
                problems.append(
                    "orchestration allocation policy summary does not reconstruct"
                )
            if plan is not None:
                if decision.move_resource_plan_id != plan.plan_id:
                    problems.append(
                        "AllocationDecision parent MoveResourcePlan mismatch"
                    )
                if decision.generation != plan.generation:
                    problems.append(
                        "AllocationDecision generation differs from sealed plan"
                    )
                if decision.position_id != plan.position_id:
                    problems.append(
                        "AllocationDecision position differs from sealed plan"
                    )
        except Exception as exc:
            problems.append(f"AllocationDecision reconstruction failed: {exc}")

    binding = artifact.get("authority_binding")
    if not isinstance(binding, dict):
        problems.append("orchestration authority_binding is missing")
    else:
        try:
            OrchestrationAuthorityProvenance.from_dict(binding)
        except Exception as exc:
            problems.append(
                f"orchestration authority binding schema is invalid: {exc}"
            )
    if isinstance(binding, dict) and plan is not None:
        try:
            reconstructed = build_authority_binding(move_plan=plan, route=route)
            if binding != reconstructed:
                problems.append("orchestration authority binding does not reconstruct")
            if reconstructed.get("work_grant_settlement_complete") is not True:
                problems.append("J11 requires complete WorkGrant settlement")
            if reconstructed.get("open_work_grant_reservations") != 0:
                problems.append("J11 requires zero open WorkGrant reservations")
        except Exception as exc:
            problems.append(f"orchestration authority binding failed: {exc}")

    try:
        trace_rows = _read_trace(run_dir / ALLOCATION_TRACE_PATH)
        if (artifact.get("allocation_trace") or {}).get("digest") != canonical_digest(trace_rows):
            problems.append("allocation trace canonical digest mismatch")
        header = trace_rows[0] if trace_rows else {}
        if header.get("record_type") != "header":
            problems.append("allocation trace header is missing")
        budget_journal = [
            row.get("payload")
            for row in trace_rows
            if row.get("record_type") == "budget_event"
        ]
        if any(not isinstance(item, dict) for item in budget_journal):
            problems.append("allocation trace contains malformed budget events")
            budget_journal = []
        work_rows = [
            row.get("payload")
            for row in trace_rows
            if row.get("record_type") == "work_grant_event"
        ]
        route_work = (
            (route.get("work_scheduler") or {}).get("events", [])
            if isinstance(route.get("work_scheduler"), dict)
            else []
        )
        if work_rows != route_work:
            problems.append("allocation trace WorkGrant events differ from route")
        allocation_rows = [
            row.get("payload")
            for row in trace_rows
            if row.get("record_type") == "allocation_decision"
        ]
        if allocation_rows != (decisions if isinstance(decisions, list) else []):
            problems.append("allocation trace decisions differ from route")
        context_rows = [
            row.get("payload")
            for row in trace_rows
            if row.get("record_type") == "allocation_context"
        ]
        route_contexts = route.get("allocation_contexts", [])
        if context_rows != (
            route_contexts if isinstance(route_contexts, list) else []
        ):
            problems.append("allocation trace contexts differ from route")
        expected_trace_rows = _trace_rows(
            route=route,
            budget_journal=budget_journal,
            generation=(
                plan.generation
                if plan is not None
                else artifact.get("generation")
            ),
            position_id=(
                plan.position_id
                if plan is not None
                else artifact.get("position_id")
            ),
        )
        if trace_rows != expected_trace_rows:
            problems.append(
                "allocation trace row schema/order/identity does not reconstruct"
            )
        trace_summary = artifact.get("allocation_trace") or {}
        budget_digest = canonical_digest(budget_journal)
        expected_trace_summary = {
            "path": ALLOCATION_TRACE_PATH,
            "sha256": sha256_file(run_dir / ALLOCATION_TRACE_PATH),
            "digest": canonical_digest(trace_rows),
            "authority_trace_digest": allocation_trace_digest(route),
            "budget_journal_digest": budget_digest,
            "budget_event_count": len(budget_journal),
        }
        if trace_summary != expected_trace_summary:
            problems.append(
                "orchestration allocation trace summary does not reconstruct"
            )
        if trace_summary.get("budget_journal_digest") != budget_digest:
            problems.append("budget journal digest mismatch")
        if trace_summary.get("budget_event_count") != len(budget_journal):
            problems.append("budget journal event count mismatch")
        if trace_summary.get("authority_trace_digest") != allocation_trace_digest(route):
            problems.append("authority allocation-trace digest mismatch")
    except OrchestrationIntegrityError as exc:
        problems.append(str(exc))
        budget_journal = []

    route_budget = route.get("budget")
    if plan is not None:
        expected_envelope = plan.resource_envelope.as_dict()
        if route.get("envelope") != expected_envelope:
            problems.append("route envelope differs from sealed MoveResourcePlan")
        if (
            not isinstance(route_budget, dict)
            or route_budget.get("envelope") != expected_envelope
        ):
            problems.append(
                "route budget envelope differs from sealed MoveResourcePlan"
            )
    replayed_budget: dict[str, Any] | None = None
    if isinstance(route_budget, dict) and budget_journal:
        try:
            replayed_budget = replay_budget_journal(
                budget_journal,
                envelope=_mapping(route_budget.get("envelope"), "route budget envelope"),
            )
        except Exception as exc:
            problems.append(f"budget journal replay failed: {exc}")
    elif not isinstance(route_budget, dict):
        problems.append("route budget snapshot is missing")

    if replayed_budget is not None and isinstance(route_budget, dict):
        if replayed_budget["open_reservations"] != route_budget.get("open_reservations"):
            problems.append("budget journal open reservation count differs from route")
        if not _approx_equal(
            replayed_budget["committed_cpu_ms"],
            route_budget.get("committed_cpu_ms"),
        ):
            problems.append("budget journal CPU total differs from route")
        if not _approx_equal(
            replayed_budget["committed_gpu_ms"],
            route_budget.get("committed_gpu_ms"),
        ):
            problems.append("budget journal GPU total differs from route")
        if replayed_budget["within_envelope"] != route_budget.get("within_envelope"):
            problems.append("budget journal envelope result differs from route")
        if replayed_budget["within_partition_caps"] != route_budget.get("within_partition_caps"):
            problems.append("budget journal partition result differs from route")
        if replayed_budget["open_reservations"] != 0:
            problems.append(
                "J11 qualification requires zero open BudgetLedger reservations"
            )
        purpose_totals = route_budget.get("purpose_totals") or {}
        for purpose, spent in replayed_budget["purpose_spent_cpu_ms"].items():
            stored = (purpose_totals.get(purpose) or {}).get("spent_cpu_ms")
            if not _approx_equal(spent, stored):
                problems.append(
                    f"budget journal {purpose} CPU spend differs from route"
                )

    route_contexts = route.get("allocation_contexts")
    if not isinstance(route_contexts, list) or len(route_contexts) != 1:
        problems.append("J11 route must contain exactly one allocation context")
    elif decision is not None and budget_journal:
        context = route_contexts[0]
        if not isinstance(context, dict):
            problems.append("allocation context is not an object")
        else:
            expected_context_keys = {
                "allocation_id",
                "budget_snapshot",
                "budget_snapshot_digest",
                "budget_journal_event_count",
            }
            if set(context) != expected_context_keys:
                problems.append("allocation context schema is invalid")
            snapshot = context.get("budget_snapshot")
            count = context.get("budget_journal_event_count")
            if context.get("allocation_id") != decision.allocation_id:
                problems.append(
                    "allocation context binds the wrong AllocationDecision"
                )
            if not isinstance(snapshot, dict):
                problems.append("allocation context budget snapshot is missing")
            else:
                if (
                    plan is not None
                    and snapshot.get("envelope")
                    != plan.resource_envelope.as_dict()
                ):
                    problems.append(
                        "allocation context envelope differs from sealed MoveResourcePlan"
                    )
                snapshot_digest = canonical_digest(snapshot)
                if context.get("budget_snapshot_digest") != snapshot_digest:
                    problems.append(
                        "allocation context budget snapshot digest mismatch"
                    )
                if decision.budget_snapshot_digest != snapshot_digest:
                    problems.append(
                        "AllocationDecision does not bind its sealed budget snapshot"
                    )
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or count > len(budget_journal)
            ):
                problems.append(
                    "allocation context budget journal cut is invalid"
                )
            elif isinstance(snapshot, dict):
                prefix = budget_journal[:count]
                try:
                    reconstructed_at_decision = replay_budget_journal(
                        prefix,
                        envelope=_mapping(
                            snapshot.get("envelope"),
                            "allocation budget envelope",
                        ),
                    )
                    if reconstructed_at_decision[
                        "open_reservations"
                    ] != snapshot.get("open_reservations"):
                        problems.append(
                            "allocation-time open reservation count does not reconstruct"
                        )
                    if not _approx_equal(
                        reconstructed_at_decision["committed_cpu_ms"],
                        snapshot.get("committed_cpu_ms"),
                    ):
                        problems.append(
                            "allocation-time committed CPU does not reconstruct"
                        )
                    if not _approx_equal(
                        reconstructed_at_decision["committed_gpu_ms"],
                        snapshot.get("committed_gpu_ms"),
                    ):
                        problems.append(
                            "allocation-time committed GPU does not reconstruct"
                        )
                    if reconstructed_at_decision[
                        "within_envelope"
                    ] != snapshot.get("within_envelope"):
                        problems.append(
                            "allocation-time envelope result does not reconstruct"
                        )
                    if reconstructed_at_decision[
                        "within_partition_caps"
                    ] != snapshot.get("within_partition_caps"):
                        problems.append(
                            "allocation-time partition result does not reconstruct"
                        )
                except Exception as exc:
                    problems.append(
                        f"allocation-time budget reconstruction failed: {exc}"
                    )

    route_scheduler = route.get("work_scheduler")
    if not isinstance(route_scheduler, dict):
        problems.append("route work_scheduler is not an object")
        route_scheduler = {}
    expected_scheduler_summary = {
        "policy_id": route_scheduler.get("policy_id"),
        "catalog_id": route_scheduler.get("catalog_id"),
        "catalog_digest": route_scheduler.get("catalog_digest"),
    }
    if artifact.get("work_scheduler") != expected_scheduler_summary:
        problems.append(
            "orchestration WorkGrant scheduler summary does not reconstruct"
        )

    scheduler_events = (
        (route.get("work_scheduler") or {}).get("events", [])
        if isinstance(route.get("work_scheduler"), dict)
        else []
    )
    if isinstance(scheduler_events, list):
        authorized_ids, settled_ids, released_ids, unresolved_ids = (
            _terminal_grant_state(scheduler_events)
        )
        terminal_ids = set(settled_ids) | set(released_ids) | set(unresolved_ids)
        open_ids = set(authorized_ids) - terminal_ids
        settlement_complete = bool(
            route_scheduler.get("settlement_complete") is True
            and not open_ids
            and not unresolved_ids
        )
        expected_work_grants = {
            "authorized": len(authorized_ids),
            "settled": len(settled_ids),
            "released": len(released_ids),
            "unresolved": len(unresolved_ids),
            "settlement_complete": settlement_complete,
            "open_reservations": len(open_ids),
        }
        if artifact.get("work_grants") != expected_work_grants:
            problems.append(
                "orchestration WorkGrant summary does not reconstruct"
            )
        if decision is not None and decision.action == BUY_BUNDLE:
            if (
                len(authorized_ids) != 9
                or len(settled_ids) != 9
                or released_ids
                or unresolved_ids
            ):
                problems.append(
                    "J11 BUY_BUNDLE qualification requires the complete 9-grant/9-settlement path"
                )
    authorized_events = [
        row
        for row in scheduler_events
        if isinstance(row, dict)
        and row.get("event") == "authorize"
        and row.get("granted") is True
    ]
    route_terminal_by_grant: dict[str, dict[str, Any]] = {}
    for row in scheduler_events:
        if (
            not isinstance(row, dict)
            or row.get("event")
            not in (
                "settle",
                "release",
                "bundle_rollback",
                "finalize_unresolved",
            )
        ):
            continue
        grant_id = row.get("grant_id")
        if not isinstance(grant_id, str):
            problems.append("terminal WorkGrant event is missing grant_id")
            continue
        if grant_id in route_terminal_by_grant:
            problems.append(
                f"WorkGrant {grant_id} has multiple route terminal events"
            )
        else:
            route_terminal_by_grant[grant_id] = row
    try:
        stage_by_id = _stage_map(run_dir)
    except OrchestrationIntegrityError as exc:
        problems.append(str(exc))
        stage_by_id = {}
    scheduler_obj = None
    policy_obj = None
    if root is not None and plan is not None:
        root = Path(root)
        try:
            scheduler_obj = build_work_scheduler(
                settings=WorkSchedulerSettings(),
                root=root,
            )
        except Exception as exc:
            problems.append(f"frozen WorkGrant scheduler could not load: {exc}")
        try:
            policy_obj = load_allocation_policy(
                root / "qualification/adaptive-resource-allocation-v1.json"
            )
        except Exception as exc:
            problems.append(f"frozen allocation policy could not load: {exc}")
        if (
            decision is not None
            and policy_obj is not None
            and decision.allocation_policy_digest != policy_obj.digest
        ):
            problems.append("AllocationDecision does not bind frozen policy")
        if scheduler_obj is not None:
            stored_scheduler = route.get("work_scheduler") or {}
            expected_scheduler_identity = {
                "policy_id": scheduler_obj.policy_id,
                "catalog_id": scheduler_obj.catalog.catalog_id,
                "catalog_digest": scheduler_obj.catalog.digest,
                "round_count": scheduler_obj.catalog.round_count,
                "max_grants_per_round": (
                    scheduler_obj.catalog.max_grants_per_round
                ),
            }
            actual_scheduler_identity = {
                key: stored_scheduler.get(key)
                for key in expected_scheduler_identity
            }
            if actual_scheduler_identity != expected_scheduler_identity:
                problems.append(
                    "route scheduler identity differs from frozen scheduler"
                )
            if (
                plan is not None
                and stored_scheduler.get("move_resource_plan_id")
                != plan.plan_id
            ):
                problems.append(
                    "route scheduler parent MoveResourcePlan identity mismatch"
                )

    reserve_by_id = {
        row.get("reservation_id"): row
        for row in budget_journal
        if isinstance(row, dict) and row.get("event") == "reserve"
    }
    journal_terminal_by_reservation: dict[int, dict[str, Any]] = {}
    for row in budget_journal:
        if (
            not isinstance(row, dict)
            or row.get("event") not in ("settle", "release")
        ):
            continue
        reservation_id = row.get("reservation_id")
        if not isinstance(reservation_id, int) or isinstance(
            reservation_id, bool
        ):
            continue
        if reservation_id in journal_terminal_by_reservation:
            problems.append(
                f"reservation {reservation_id} has multiple journal terminal events"
            )
        else:
            journal_terminal_by_reservation[reservation_id] = row
    reconstructed_grants: list[
        tuple[dict[str, Any], WorkGrant, dict[str, Any]]
    ] = []
    authorized_search_ids: list[str] = []
    for event in authorized_events:
        try:
            grant = WorkGrant.from_dict(event.get("grant") or {})
        except Exception as exc:
            problems.append(f"WorkGrant reconstruction failed: {exc}")
            continue
        if event.get("grant_id") != grant.grant_id:
            problems.append(
                f"WorkGrant {grant.grant_id} route event identity mismatch"
            )
        if plan is not None and grant.move_resource_plan_id != plan.plan_id:
            problems.append(f"WorkGrant {grant.grant_id} parent plan mismatch")
        scheduled = None
        profile = None
        expected_effective: dict[str, Any] | None = None
        if scheduler_obj is not None and plan is not None:
            try:
                expected = (
                    decision.digest
                    if decision is not None and grant.allocation_round == 2
                    else None
                )
                scheduler_obj.validate_grant(
                    move_plan=plan,
                    grant=grant,
                    expected_allocator_decision_digest=expected,
                )
                scheduled = scheduler_obj.catalog.chunk_for(
                    allocation_round=grant.allocation_round,
                    family=grant.owner,
                    phase=grant.phase,
                )
                profile = scheduler_obj.resource_catalog.profile(
                    scheduled.profile_id
                )
                expected_effective = (
                    scheduler_obj.resource_catalog.startup_options(
                        profile.profile_id
                    )
                )
                expected_effective.update(
                    scheduler_obj.resource_catalog.phase_options(
                        profile.profile_id
                    ).get(grant.phase, {})
                )
                expected_effective = dict(
                    sorted(expected_effective.items())
                )
                if (
                    grant.effective_options_digest
                    != canonical_digest(expected_effective)
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} effective-options digest "
                        "differs from frozen profile/phase options"
                    )

                # In the frozen J9/J10 grid every chunk wall bound is 4000 ms
                # while the J8 soft budget is strictly below the hard <=4000 ms
                # ceiling. Therefore the grant deadline reconstructs exactly to
                # the parent soft budget without needing producer timing.
                if (
                    float(plan.soft_budget_ms)
                    > float(scheduled.chunk.wall_bound_ms) + 1e-9
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} deadline cannot be "
                        "reconstructed from the frozen chunk bound"
                    )
                elif (
                    abs(
                        float(grant.wall_deadline_ms)
                        - float(plan.soft_budget_ms)
                    )
                    > 1e-9
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} deadline differs from "
                        "the frozen scheduler/MoveResourcePlan deadline"
                    )
            except Exception as exc:
                problems.append(
                    f"WorkGrant {grant.grant_id} frozen validation failed: {exc}"
                )
        reservation = reserve_by_id.get(event.get("reservation_id"))
        if reservation is None:
            problems.append(
                f"WorkGrant {grant.grant_id} lacks a matching budget reservation"
            )
        else:
            if reservation.get("grant_id") != grant.grant_id:
                problems.append(
                    f"WorkGrant {grant.grant_id} reservation grant provenance mismatch"
                )
            if reservation.get("profile_id") != grant.profile_id:
                problems.append(
                    f"WorkGrant {grant.grant_id} reservation profile provenance mismatch"
                )
            if reservation.get("allocator_decision_digest") != grant.allocator_decision_digest:
                problems.append(
                    f"WorkGrant {grant.grant_id} reservation allocation provenance mismatch"
                )

            terminal = route_terminal_by_grant.get(grant.grant_id)
            journal_terminal = journal_terminal_by_reservation.get(
                event.get("reservation_id")
            )
            if terminal is None:
                problems.append(
                    f"WorkGrant {grant.grant_id} lacks a route terminal event"
                )
            elif journal_terminal is None:
                problems.append(
                    f"WorkGrant {grant.grant_id} lacks a journal terminal event"
                )
            else:
                if terminal.get("reservation_id") != event.get(
                    "reservation_id"
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} terminal reservation identity mismatch"
                    )
                if terminal.get("search_id") != event.get("search_id"):
                    problems.append(
                        f"WorkGrant {grant.grant_id} terminal search identity mismatch"
                    )
                terminal_event = terminal.get("event")
                expected_journal_event = {
                    "settle": "settle",
                    "release": "release",
                    "bundle_rollback": "release",
                    "finalize_unresolved": "settle",
                }.get(terminal_event)
                if journal_terminal.get("event") != expected_journal_event:
                    problems.append(
                        f"WorkGrant {grant.grant_id} route/journal terminal semantics differ"
                    )
                if terminal_event == "settle":
                    if not _approx_equal(
                        journal_terminal.get("actual_gpu_ms"),
                        0.0,
                    ):
                        problems.append(
                            f"WorkGrant {grant.grant_id} CPU-only settlement recorded GPU spend"
                        )
                    if journal_terminal.get("gpu_source") != "declared_fallback":
                        problems.append(
                            f"WorkGrant {grant.grant_id} CPU-only settlement has unexpected GPU source"
                        )
                    if terminal.get("cpu_source") != journal_terminal.get(
                        "cpu_source"
                    ):
                        problems.append(
                            f"WorkGrant {grant.grant_id} settlement CPU source differs from journal"
                        )
                    if not _approx_equal(
                        terminal.get("actual_cpu_ms"),
                        journal_terminal.get("actual_cpu_ms"),
                    ):
                        problems.append(
                            f"WorkGrant {grant.grant_id} settlement CPU differs from journal"
                        )
                if terminal_event == "finalize_unresolved" and (
                    journal_terminal.get("cpu_source")
                    != "declared_fallback"
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} unresolved terminal is not declared-fallback spend"
                    )
        checkpoint_ms = event.get("checkpoint_ms")
        try:
            checkpoint_value = _finite_nonnegative(
                checkpoint_ms,
                f"WorkGrant {grant.grant_id} authorization checkpoint",
            )
            if checkpoint_value >= float(grant.wall_deadline_ms):
                problems.append(
                    f"WorkGrant {grant.grant_id} was authorized after its deadline"
                )
        except Exception as exc:
            problems.append(
                f"WorkGrant {grant.grant_id} authorization time is invalid: {exc}"
            )

        search_id = event.get("search_id")
        if not isinstance(search_id, str) or not search_id:
            problems.append(
                f"WorkGrant {grant.grant_id} authorization search_id is invalid"
            )
        else:
            authorized_search_ids.append(search_id)
        stage = stage_by_id.get(search_id)
        if stage is None:
            problems.append(
                f"WorkGrant {grant.grant_id} has no sealed replay stage"
            )
            continue
        if stage.get("instance") != grant.instance:
            problems.append(
                f"WorkGrant {grant.grant_id} stage instance differs from grant"
            )
        if stage.get("owner") != grant.owner:
            problems.append(
                f"WorkGrant {grant.grant_id} stage owner differs from grant"
            )
        if stage.get("phase") != grant.phase:
            problems.append(
                f"WorkGrant {grant.grant_id} stage phase differs from grant"
            )
        effective = stage.get("effective_options")
        if (
            not isinstance(effective, dict)
            or canonical_digest(effective)
            != grant.effective_options_digest
        ):
            problems.append(
                f"WorkGrant {grant.grant_id} effective options do not reconstruct"
            )
        if (
            expected_effective is not None
            and effective != expected_effective
        ):
            problems.append(
                f"WorkGrant {grant.grant_id} stage options differ from "
                "the frozen resource profile/phase"
            )

        try:
            dispatched_ms = _finite_nonnegative(
                stage.get("dispatched_ms"),
                f"WorkGrant {grant.grant_id} dispatched_ms",
            )
            if dispatched_ms >= float(grant.wall_deadline_ms):
                problems.append(
                    f"WorkGrant {grant.grant_id} stage dispatched after its deadline"
                )
        except Exception as exc:
            problems.append(
                f"WorkGrant {grant.grant_id} dispatch time is invalid: {exc}"
            )

        if (
            manifest is not None
            and scheduler_obj is not None
            and profile is not None
            and plan is not None
        ):
            engines = manifest.get("engines")
            engine_identity = (
                engines.get(grant.instance)
                if isinstance(engines, dict)
                else None
            )
            if not isinstance(engine_identity, dict):
                problems.append(
                    f"WorkGrant {grant.grant_id} instance is missing from replay engine identity"
                )
            else:
                process_identity = profile.process_identity
                bindings = [
                    item
                    for item in plan.composition.bindings
                    if item.instance == grant.instance
                ]
                expected_role = (
                    (
                        "anchor"
                        if bindings[0].role.value == "anchor"
                        else "shadow"
                    )
                    if len(bindings) == 1
                    else None
                )
                if engine_identity.get("engine") != profile.family:
                    problems.append(
                        f"WorkGrant {grant.grant_id} replay engine family differs from profile"
                    )
                if engine_identity.get("role") != expected_role:
                    problems.append(
                        f"WorkGrant {grant.grant_id} replay engine role differs from composition"
                    )
                bundle_engines = (
                    ((engine_bundle.get("artifacts") or {}).get("engines") or {})
                    if isinstance(engine_bundle, dict)
                    else {}
                )
                bundle_engine = (
                    bundle_engines.get(profile.family)
                    if isinstance(bundle_engines, dict)
                    else None
                )
                if (
                    not isinstance(bundle_engine, dict)
                    or engine_identity.get("binary_sha256")
                    != bundle_engine.get("sha256")
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} binary SHA differs from sealed exact-head ENGINE-OPT-V2 candidate"
                    )
                if engine_identity.get("args", []) != list(
                    process_identity.args
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} process argv differs from qualified profile"
                    )
                if dict(engine_identity.get("environment") or {}) != dict(
                    process_identity.environment
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} process environment differs from qualified profile"
                    )
                expected_startup = (
                    scheduler_obj.resource_catalog.startup_options(
                        profile.profile_id
                    )
                )
                if engine_identity.get("options") != expected_startup:
                    problems.append(
                        f"WorkGrant {grant.grant_id} startup options differ from frozen profile"
                    )
                expected_phase_options = (
                    scheduler_obj.resource_catalog.phase_options(
                        profile.profile_id
                    )
                )
                if dict(engine_identity.get("phase_options") or {}) != (
                    expected_phase_options
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} phase options differ from frozen profile"
                    )
                warmup = scheduler_obj.resource_catalog.warmup(
                    profile.profile_id
                )
                expected_warmup = (
                    None
                    if warmup is None
                    else {
                        "nodes": warmup["nodes"],
                        "position": warmup["position"],
                        "reset_after": warmup["reset_after"],
                    }
                )
                if engine_identity.get("warmup") != expected_warmup:
                    problems.append(
                        f"WorkGrant {grant.grant_id} warmup identity differs from frozen profile"
                    )
                if (
                    process_identity.backend is not None
                    and expected_startup.get("Backend")
                    != process_identity.backend
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} backend differs from qualified profile"
                    )
                actual_artifacts = engine_identity.get("artifacts")
                if not process_identity.artifacts and actual_artifacts not in (
                    None,
                    {},
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} replay carries unexpected process artifacts"
                    )
                if process_identity.artifacts and (
                    not isinstance(actual_artifacts, dict)
                    or set(actual_artifacts) != {"weights"}
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} replay artifact set differs from qualified profile"
                    )
                for artifact_identity in process_identity.artifacts:
                    if artifact_identity.name != "network":
                        problems.append(
                            f"WorkGrant {grant.grant_id} has unsupported frozen artifact identity"
                        )
                        continue
                    weights = (
                        actual_artifacts.get("weights")
                        if isinstance(actual_artifacts, dict)
                        else None
                    )
                    bundle_networks = (
                        ((engine_bundle.get("artifacts") or {}).get("networks") or {})
                        if isinstance(engine_bundle, dict)
                        else {}
                    )
                    bundle_network = (
                        bundle_networks.get(profile.family)
                        if isinstance(bundle_networks, dict)
                        else None
                    )
                    if (
                        not isinstance(weights, dict)
                        or not isinstance(bundle_network, dict)
                        or weights.get("sha256")
                        != bundle_network.get("sha256")
                    ):
                        problems.append(
                            f"WorkGrant {grant.grant_id} network SHA differs from sealed exact-head ENGINE-OPT-V2 candidate"
                        )

        try:
            request = parse_go_request(str(stage.get("command")))
            limit_rows = request.get("limits", [])
            if (
                not isinstance(limit_rows, list)
                or len(limit_rows) != 1
                or not isinstance(limit_rows[0], dict)
                or limit_rows[0].get("name") != grant.native_limit.kind.value
                or limit_rows[0].get("value") != grant.native_limit.value
                or request.get("unknown_tokens")
            ):
                problems.append(
                    f"WorkGrant {grant.grant_id} UCI request is not exactly the granted native limit plus legal searchmoves"
                )
        except Exception as exc:
            problems.append(
                f"WorkGrant {grant.grant_id} command cannot be reconstructed: {exc}"
            )
        reconstructed_grants.append((event, grant, stage))

    if decision is not None and decision.action == BUY_BUNDLE:
        if len(authorized_search_ids) != len(set(authorized_search_ids)):
            problems.append(
                "J11 BUY_BUNDLE qualification requires unique search_id per WorkGrant"
            )
        optional_stage_ids = {
            search_id
            for search_id, stage in stage_by_id.items()
            if (
                stage.get("phase")
                in ("EXPLORE", "VERIFY", "STAGED_VERIFY")
                and stage.get("owner")
                in ("stockfish", "reckless", "lc0")
            )
        }
        if set(authorized_search_ids) != optional_stage_ids:
            problems.append(
                "J11 BUY_BUNDLE requires every optional engine stage to be authorized by exactly one WorkGrant"
            )
        if scheduler_obj is not None:
            expected_grid = {
                (
                    row.allocation_round,
                    row.chunk.family,
                    row.chunk.phase,
                    row.profile_id,
                    row.chunk.chunk_id,
                )
                for row in scheduler_obj.catalog.chunks
            }
            actual_grid = {
                (
                    grant.allocation_round,
                    grant.owner,
                    grant.phase,
                    grant.profile_id,
                    grant.work_chunk_id,
                )
                for _event, grant, _stage in reconstructed_grants
            }
            if (
                len(reconstructed_grants) != len(expected_grid)
                or len(actual_grid) != len(expected_grid)
                or actual_grid != expected_grid
            ):
                problems.append(
                    "J11 BUY_BUNDLE WorkGrants do not match the exact frozen 3x3 scheduler grid"
                )

        authorized_grant_ids = {
            grant.grant_id
            for _event, grant, _stage in reconstructed_grants
        }
        journal_grant_ids = {
            row.get("grant_id")
            for row in budget_journal
            if (
                isinstance(row, dict)
                and row.get("event") == "reserve"
                and isinstance(row.get("grant_id"), str)
            )
        }
        if journal_grant_ids != authorized_grant_ids:
            problems.append(
                "J11 WorkGrant authorizations do not bijectively match grant-bearing BudgetLedger reservations"
            )

    expected_route_summary = {
        "path": "route.json",
        "sha256": sha256_file(run_dir / "route.json"),
    }
    if artifact.get("route") != expected_route_summary:
        problems.append("orchestration route summary does not reconstruct")

    try:
        resource_path = run_dir / "resource.json"
        resource = _load_json(resource_path, "resource")
        if resource.get("run_id") != artifact.get("run_id"):
            problems.append("resource report run_id differs from orchestration run")
        if resource.get("run_id") != route.get("run_id"):
            problems.append("resource report run_id differs from route")
        controller = _mapping(resource.get("controller"), "resource.controller")
        processes = _mapping(resource.get("processes"), "resource.processes")
        stages = resource.get("stages")
        if not isinstance(stages, list):
            raise OrchestrationIntegrityError("resource.stages must be an array")

        process_cpu = 0.0
        process_complete = bool(processes)
        for instance, raw_process in processes.items():
            process = _mapping(
                raw_process,
                f"resource.processes[{instance!r}]",
            )
            complete = process.get("complete") is True
            cpu = process.get("cpu_ms")
            if not complete or cpu is None:
                process_complete = False
                continue
            process_cpu += _finite_nonnegative(
                cpu,
                f"resource.processes[{instance!r}].cpu_ms",
            )

        stage_cpu = 0.0
        stage_complete = bool(stages)
        resource_stage_by_key: dict[str, dict[str, Any]] = {}
        for index, raw_stage in enumerate(stages):
            stage = _mapping(raw_stage, f"resource.stages[{index}]")
            key = stage.get("key")
            if isinstance(key, str):
                if key in resource_stage_by_key:
                    problems.append(
                        f"duplicate resource stage key: {key}"
                    )
                else:
                    resource_stage_by_key[key] = stage
            complete = stage.get("complete") is True
            cpu = stage.get("cpu_ms")
            if not complete or cpu is None:
                stage_complete = False
                continue
            stage_cpu += _finite_nonnegative(
                cpu,
                f"resource.stages[{index}].cpu_ms",
            )

        for event, grant, _stage in reconstructed_grants:
            terminal = route_terminal_by_grant.get(grant.grant_id)
            if not isinstance(terminal, dict):
                continue
            if terminal.get("event") != "settle":
                continue
            resource_stage = resource_stage_by_key.get(
                str(event.get("search_id"))
            )
            if terminal.get("cpu_source") == "measured":
                if (
                    not isinstance(resource_stage, dict)
                    or resource_stage.get("complete") is not True
                    or resource_stage.get("cpu_ms") is None
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} measured settlement lacks complete resource stage"
                    )
                elif not _approx_equal(
                    terminal.get("actual_cpu_ms"),
                    resource_stage.get("cpu_ms"),
                ):
                    problems.append(
                        f"WorkGrant {grant.grant_id} measured settlement CPU differs from resource stage"
                    )
            elif (
                decision is not None
                and decision.action == BUY_BUNDLE
            ):
                problems.append(
                    f"J11 BUY_BUNDLE WorkGrant {grant.grant_id} did not settle from physical measurement"
                )

        controller_cpu = _finite_nonnegative(
            controller.get("cpu_ms"),
            "resource.controller.cpu_ms",
        )
        if not _approx_equal(process_cpu, resource.get("engine_cpu_ms")):
            problems.append(
                "resource engine CPU total does not reconstruct from process endpoints"
            )
        # Each stored stage row is rounded to 3 decimals while the report's
        # aggregate is rounded only after summing the unrounded measurements.
        # Bound the legitimate serialization error by 0.5us per stage plus the
        # aggregate's own 0.5us rounding, rather than using a fixed tolerance.
        stage_rounding_tolerance = 0.0005 * (len(stages) + 1) + 1e-9
        if not _approx_equal(
            stage_cpu,
            resource.get("stage_engine_cpu_ms"),
            tolerance=stage_rounding_tolerance,
        ):
            problems.append(
                "resource stage CPU total does not reconstruct from stage evidence"
            )
        recomputed_physical = process_cpu + controller_cpu
        if not _approx_equal(
            recomputed_physical,
            resource.get("physical_cpu_ms"),
        ):
            problems.append("resource physical CPU total does not reconstruct")

        settings = _mapping(resource.get("settings"), "resource.settings")
        coverage = _mapping(resource.get("coverage"), "resource.coverage")
        expected_resource_settings = {
            "enabled": True,
            "provider": "linux-procfs-v1",
            "require_cpu_for_claim": True,
            "require_gpu_for_claim": False,
            "record_memory": True,
        }
        if settings != expected_resource_settings:
            problems.append(
                "resource settings differ from frozen J10 qualification policy"
            )
        if root is not None:
            try:
                frozen_config = _load_json(
                    Path(root) / "config/allfather.m14-j-j10.validation.json",
                    "frozen J10 config",
                )
                if frozen_config.get("resource_measurement") != settings:
                    problems.append(
                        "resource settings differ from source-controlled J10 config"
                    )
            except Exception as exc:
                problems.append(
                    f"frozen J10 resource policy could not be loaded: {exc}"
                )
        provider_available = (
            isinstance(resource.get("provider"), str)
            and bool(resource.get("provider"))
            and resource.get("provider_error") is None
        )
        cpu_complete = bool(
            settings.get("enabled") is True
            and provider_available
            and resource.get("interval_error") is None
            and process_complete
            and stage_complete
        )
        stored_cpu_coverage = _mapping(
            coverage.get("cpu"),
            "resource.coverage.cpu",
        )
        if stored_cpu_coverage.get("complete") is not cpu_complete:
            problems.append(
                "resource CPU coverage does not reconstruct from raw evidence"
            )
        if not cpu_complete:
            problems.append(
                "J11 qualification requires complete CPU measurement coverage"
            )

        cpu_required = settings.get("require_cpu_for_claim") is True
        gpu_required = settings.get("require_gpu_for_claim") is True
        gpu_complete = (
            _mapping(
                coverage.get("gpu"),
                "resource.coverage.gpu",
            ).get("complete")
            is True
        )
        resource_qualified = bool(
            settings.get("enabled") is True
            and (not cpu_required or cpu_complete)
            and (not gpu_required or gpu_complete)
        )
        if resource.get("qualified") is not resource_qualified:
            problems.append("resource qualified flag does not reconstruct")
        if not resource_qualified:
            problems.append(
                "J11 qualification requires complete physical resource evidence"
            )

        resource_core = {
            key: value
            for key, value in resource.items()
            if key != "report_id"
        }
        report_digest = hashlib.sha256(
            json.dumps(
                resource_core,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        if resource.get("report_id") != f"resource-{report_digest[:16]}":
            problems.append("resource report_id does not reconstruct")

        route_resource = route.get("resource_measurement") or {}
        if not isinstance(route_resource, dict):
            problems.append("route resource_measurement is not an object")
            route_resource = {}
        if route_resource.get("sha256") != sha256_file(resource_path):
            problems.append("route does not bind sealed resource.json")
        expected_resource_summary = {
            "path": "resource.json",
            "sha256": sha256_file(resource_path),
            "report_id": resource.get("report_id"),
        }
        if artifact.get("resource") != expected_resource_summary:
            problems.append(
                "orchestration resource summary does not reconstruct"
            )
        if route_resource.get("report_id") != resource.get("report_id"):
            problems.append("route resource report identity mismatch")
        if route_resource.get("qualified") is not resource_qualified:
            problems.append("route resource qualification differs from resource.json")
        if not _approx_equal(
            route_resource.get("physical_cpu_ms"),
            recomputed_physical,
        ):
            problems.append("route physical CPU summary differs from resource.json")

        claim = route.get("envelope_claim")
        if not isinstance(claim, dict):
            problems.append("route envelope_claim is not an object")
            claim = {}
        route_envelope = route.get("envelope")
        if not isinstance(route_envelope, dict):
            problems.append("route envelope is not an object")
            route_envelope = {}
        physical_cpu_within = recomputed_physical <= (
            _finite_nonnegative(
                route_envelope.get("cpu_ms"),
                "route envelope.cpu_ms",
            )
            + 1e-9
        )
        if claim.get("physical_measurement_qualified") is not resource_qualified:
            problems.append(
                "route physical measurement qualification does not reconstruct"
            )
        if claim.get("physical_cpu_within_envelope") is not physical_cpu_within:
            problems.append(
                "route physical CPU envelope result does not reconstruct"
            )
        clock_outcome = route.get("clock_outcome")
        clock_complete = bool(
            not isinstance(clock_outcome, dict)
            or clock_outcome.get("output_within_deadline") is True
        )
        expected_claim = bool(
            clock_complete
            and claim.get("anchor_request_bounded") is True
            and claim.get("anchor_cost_reserved") is True
            and claim.get("gpu_accounted") is True
            and claim.get("reservations_within_envelope") is True
            and claim.get("specialist_partitions_within_caps") is True
            and claim.get("specialist_settlement_complete") is True
            and claim.get("work_grant_settlement_complete") is True
            and claim.get("wall_within_envelope") is True
            and resource_qualified
            and physical_cpu_within
        )
        if claim.get("claimed") is not expected_claim:
            problems.append(
                "route envelope claimed flag does not reconstruct from source facts"
            )
    except Exception as exc:
        problems.append(f"resource evidence reconstruction failed: {exc}")

    if decision is not None and policy_obj is not None:
        if policy_obj.stop_promotion is False and decision.action != BUY_BUNDLE:
            problems.append(
                "unpromoted frozen J10 policy did not fail closed to BUY_BUNDLE"
            )

    return problems
