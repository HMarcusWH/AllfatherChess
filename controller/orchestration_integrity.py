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
from controller.decision import canonical_digest
from controller.move_resource_plan import MoveResourcePlan
from controller.replay import atomic_write_text, load_manifest, sha256_file
from controller.resource_allocator import (
    AllocationDecision,
    BUY_BUNDLE,
    load_allocation_policy,
)
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
        elif event == "release":
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
    """Seal the non-circular J11 evidence DAG before manifest finalization."""

    run_dir = Path(run_dir)
    route_path = run_dir / "route.json"
    resource_path = run_dir / "resource.json"
    route = _load_json(route_path, "route")
    if route.get("run_id") is None:
        raise OrchestrationIntegrityError("route lacks run_id")
    if route.get("move_resource_plan") != move_plan.as_dict():
        raise OrchestrationIntegrityError(
            "route MoveResourcePlan differs from the live J11 parent"
        )
    if not resource_path.is_file():
        raise OrchestrationIntegrityError(
            "J11 requires sealed physical resource evidence"
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
            "host_id": move_plan.host_capabilities.host_id
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
                result[search_id] = raw
    return result


def verify_orchestration_integrity(
    run_dir: Path | str,
    *,
    root: Path | str | None = None,
) -> list[str]:
    """Independently reconstruct J11 provenance. Empty list means valid."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    try:
        artifact = _load_json(run_dir / ORCHESTRATION_PATH, "orchestration evidence")
    except OrchestrationIntegrityError as exc:
        return [str(exc)]

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

    for section in ("move_resource_plan", "allocation_trace", "route", "resource"):
        row = artifact.get(section)
        if not isinstance(row, dict):
            problems.append(f"orchestration {section} section is missing")
            continue
        relative = row.get("path")
        stored_sha = row.get("sha256")
        if not isinstance(relative, str):
            problems.append(f"orchestration {section} path is invalid")
            continue
        source = run_dir / relative
        if not source.is_file():
            problems.append(f"orchestration source missing: {relative}")
        elif sha256_file(source) != stored_sha:
            problems.append(f"orchestration source hash mismatch: {relative}")

    manifest: dict[str, Any] | None = None
    try:
        manifest = load_manifest(run_dir)
    except Exception as exc:
        problems.append(f"parent replay manifest cannot be loaded: {exc}")
    if manifest is not None:
        summary = manifest.get("orchestration_evidence")
        if not isinstance(summary, dict):
            problems.append("parent replay does not bind orchestration evidence")
        else:
            if summary.get("sha256") != sha256_file(run_dir / ORCHESTRATION_PATH):
                problems.append("parent replay orchestration SHA mismatch")
            if summary.get("evidence_id") != evidence_id:
                problems.append("parent replay orchestration evidence_id mismatch")

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

    if plan is not None:
        stored_plan = artifact.get("move_resource_plan") or {}
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
        if (artifact.get("game_environment") or {}).get("digest") != plan.game_environment.digest:
            problems.append("game environment digest mismatch")
        if (artifact.get("composition_profile") or {}).get("digest") != plan.composition.digest:
            problems.append("composition profile digest mismatch")
        if (artifact.get("profile_catalog") or {}).get("digest") != plan.catalog_digest:
            problems.append("profile catalog digest mismatch")
        host = plan.host_capabilities
        if host is None:
            problems.append("adaptive J11 evidence lacks HostCapabilities")
        else:
            if (artifact.get("host_capabilities") or {}).get("digest") != host.digest:
                problems.append("HostCapabilities digest mismatch")
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
        except Exception as exc:
            problems.append(f"AllocationDecision reconstruction failed: {exc}")

    binding = artifact.get("authority_binding")
    if not isinstance(binding, dict):
        problems.append("orchestration authority_binding is missing")
    elif plan is not None:
        try:
            reconstructed = build_authority_binding(move_plan=plan, route=route)
            if binding != reconstructed:
                problems.append("orchestration authority binding does not reconstruct")
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
        budget_digest = canonical_digest(budget_journal)
        if (artifact.get("allocation_trace") or {}).get("budget_journal_digest") != budget_digest:
            problems.append("budget journal digest mismatch")
    except OrchestrationIntegrityError as exc:
        problems.append(str(exc))
        budget_journal = []

    route_budget = route.get("budget")
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
            snapshot = context.get("budget_snapshot")
            count = context.get("budget_journal_event_count")
            if context.get("allocation_id") != decision.allocation_id:
                problems.append(
                    "allocation context binds the wrong AllocationDecision"
                )
            if not isinstance(snapshot, dict):
                problems.append("allocation context budget snapshot is missing")
            else:
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
                if context.get("budget_journal_digest") != canonical_digest(prefix):
                    problems.append(
                        "allocation context budget journal digest mismatch"
                    )
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

    scheduler_events = (
        (route.get("work_scheduler") or {}).get("events", [])
        if isinstance(route.get("work_scheduler"), dict)
        else []
    )
    authorized_events = [
        row
        for row in scheduler_events
        if isinstance(row, dict)
        and row.get("event") == "authorize"
        and row.get("granted") is True
    ]
    stage_by_id = _stage_map(run_dir)
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
            if stored_scheduler.get("catalog_digest") != scheduler_obj.catalog.digest:
                problems.append("route scheduler catalog digest differs from frozen catalog")

    reserve_by_id = {
        row.get("reservation_id"): row
        for row in budget_journal
        if isinstance(row, dict) and row.get("event") == "reserve"
    }
    for event in authorized_events:
        try:
            grant = WorkGrant.from_dict(event.get("grant") or {})
        except Exception as exc:
            problems.append(f"WorkGrant reconstruction failed: {exc}")
            continue
        if plan is not None and grant.move_resource_plan_id != plan.plan_id:
            problems.append(f"WorkGrant {grant.grant_id} parent plan mismatch")
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
        search_id = event.get("search_id")
        stage = stage_by_id.get(search_id)
        if stage is None:
            problems.append(
                f"WorkGrant {grant.grant_id} has no sealed replay stage"
            )
            continue
        effective = stage.get("effective_options")
        if not isinstance(effective, dict) or canonical_digest(effective) != grant.effective_options_digest:
            problems.append(
                f"WorkGrant {grant.grant_id} effective options do not reconstruct"
            )
        try:
            request = parse_go_request(str(stage.get("command")))
            limits = {
                item.get("name"): item.get("value")
                for item in request.get("limits", [])
                if isinstance(item, dict)
            }
            if limits.get(grant.native_limit.kind.value) != grant.native_limit.value:
                problems.append(
                    f"WorkGrant {grant.grant_id} UCI native limit differs from grant"
                )
        except Exception as exc:
            problems.append(
                f"WorkGrant {grant.grant_id} command cannot be reconstructed: {exc}"
            )

    try:
        resource_path = run_dir / "resource.json"
        resource = _load_json(resource_path, "resource")
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
        for index, raw_stage in enumerate(stages):
            stage = _mapping(raw_stage, f"resource.stages[{index}]")
            complete = stage.get("complete") is True
            cpu = stage.get("cpu_ms")
            if not complete or cpu is None:
                stage_complete = False
                continue
            stage_cpu += _finite_nonnegative(
                cpu,
                f"resource.stages[{index}].cpu_ms",
            )

        controller_cpu = _finite_nonnegative(
            controller.get("cpu_ms"),
            "resource.controller.cpu_ms",
        )
        if not _approx_equal(process_cpu, resource.get("engine_cpu_ms")):
            problems.append(
                "resource engine CPU total does not reconstruct from process endpoints"
            )
        if not _approx_equal(stage_cpu, resource.get("stage_engine_cpu_ms")):
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
