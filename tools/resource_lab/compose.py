"""Concurrent whole-machine interference experiments for J6 Stage B."""

from __future__ import annotations

import concurrent.futures
import statistics
import threading
from pathlib import Path
from typing import Any

from adapters.resource.linux_affinity import LinuxAffinityProvider
from adapters.resource.linux_proc import LinuxProcProvider
from controller.host_pressure import discover_host_pressure
from tests.harness.uci_session import UciSession

from tools.engine_opt.corpus import PositionCase

from .candidate_matrix import Candidate, CompositionCandidate
from .measure import (
    classify_failure,
    parse_search_observation,
    percentile,
    physical_primitives,
    reconstruct_physical_measurement,
)
from .observe import observe_affinity, process_cpu_scope
from .process_cpu import ProcessCpuClock


class CompositionRunError(RuntimeError):
    pass


def _pressure() -> dict[str, Any]:
    try:
        return {
            "status": "completed",
            "observation": discover_host_pressure().as_dict(),
        }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _aggregate_completed(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed_rows = [
        row
        for row in rows
        if row.get("status") == "completed"
        and isinstance(row.get("measurement"), dict)
    ]
    completed = [row["measurement"] for row in completed_rows]
    scope_complete = bool(completed_rows) and all(
        isinstance(row.get("process_cpu_scope"), dict)
        and row["process_cpu_scope"].get("complete") is True
        for row in completed_rows
    )
    return {
        "completed_members": len(completed),
        "process_scope_complete": scope_complete,
        "sum_cpu_ms": (
            round(sum(float(item["cpu_ms"]) for item in completed), 6)
            if scope_complete
            else None
        ),
        "sum_end_rss_bytes": (
            sum(int(item["end_rss_bytes"]) for item in completed)
            if scope_complete
            and completed
            and all(item.get("end_rss_bytes") is not None for item in completed)
            else None
        ),
        "sum_member_vm_hwm_bytes": (
            sum(int(item["vm_hwm_bytes"]) for item in completed)
            if scope_complete
            and completed
            and all(item.get("vm_hwm_bytes") is not None for item in completed)
            else None
        ),
    }


def run_composition_batch(
    *,
    composition: CompositionCandidate,
    candidate_map: dict[str, Candidate],
    bundle_root: Path,
    case: PositionCase,
    repeat_index: int,
    deadline_ms: float,
    clock_ticks_per_second: int,
    required_cpu_method: str,
    max_cpu_resolution_ns: int,
    affinity_max_attempts: int,
    block_index: int,
    order_index: int,
    attempt_ordinal: int,
) -> dict[str, Any]:
    sessions: dict[str, UciSession] = {}
    proc = LinuxProcProvider(clock_ticks=clock_ticks_per_second)
    affinity = LinuxAffinityProvider()
    before_pressure = _pressure()
    ready_barrier = threading.Barrier(len(composition.members))
    start_barrier = threading.Barrier(len(composition.members))
    setup_stage = "process_start"

    try:
        for member in composition.members:
            candidate = candidate_map[member.candidate_id]
            setup_stage = "process_start"
            session = UciSession(
                bundle_root / candidate.binary_relpath,
                cwd=bundle_root.parents[1],
                timeout=max(10.0, deadline_ms / 1000.0 + 5.0),
                args=list(candidate.args),
                environment=dict(candidate.environment),
                start_new_session=True,
            )
            session.start()

            setup_stage = "engine_configure"
            session.configure(candidate.execution_options(bundle_root))
            session.new_game()
            if candidate.warmup_nodes is not None:
                setup_stage = "warmup"
                session.set_position({"startpos_moves": []})
                session.search_nodes(
                    candidate.warmup_nodes,
                    timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
                )
                session.new_game()
            setup_stage = "engine_configure"
            session.set_position({"fen": case.fen, "moves": []})
            sessions[member.instance] = session

        def worker(member):
            candidate = candidate_map[member.candidate_id]
            session = sessions[member.instance]
            fault_stage = "physical_snapshot"
            try:
                if session.proc is None:
                    raise CompositionRunError(
                        f"{member.instance}: process missing"
                    )
                pid = session.proc.pid

                identity_binding = proc.snapshot(pid)

                fault_stage = "cpu_clock_init"
                cpu_clock = ProcessCpuClock(
                    pid,
                    max_resolution_ns=max_cpu_resolution_ns,
                )

                fault_stage = "affinity_observation"
                affinity_before = observe_affinity(
                    affinity,
                    pid,
                    max_attempts=affinity_max_attempts,
                )

                ready_barrier.wait(
                    timeout=max(5.0, deadline_ms / 1000.0)
                )
                start_barrier.wait(
                    timeout=max(5.0, deadline_ms / 1000.0)
                )

                fault_stage = "physical_snapshot"
                proc_before = proc.snapshot(pid)
                if (
                    proc_before.pid != identity_binding.pid
                    or proc_before.start_time_ticks
                    != identity_binding.start_time_ticks
                ):
                    raise CompositionRunError(
                        f"{member.instance}: process identity changed before search"
                    )
                cpu_before_ns = cpu_clock.sample_ns()

                fault_stage = "search"
                lines = session.search_nodes(
                    candidate.nodes,
                    timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
                )

                fault_stage = "physical_snapshot"
                cpu_after_ns = cpu_clock.sample_ns()
                proc_after = proc.snapshot(pid)

                fault_stage = "affinity_observation"
                affinity_after = observe_affinity(
                    affinity,
                    pid,
                    max_attempts=affinity_max_attempts,
                )
                scope = process_cpu_scope(
                    affinity_before,
                    affinity_after,
                )

                fault_stage = "physical_reconstruction"
                cpu_evidence = cpu_clock.evidence(
                    cpu_before_ns,
                    cpu_after_ns,
                )
                primitives = physical_primitives(
                    identity_binding,
                    proc_before,
                    proc_after,
                    cpu_evidence,
                )
                physical = reconstruct_physical_measurement(
                    primitives,
                    clock_ticks_per_second=clock_ticks_per_second,
                    required_cpu_method=required_cpu_method,
                    max_cpu_resolution_ns=max_cpu_resolution_ns,
                )

                fault_stage = "transcript_parse"
                observation = parse_search_observation(
                    lines,
                    family=candidate.family,
                    physical=physical,
                )
                return {
                    "instance": member.instance,
                    "role": member.role,
                    "candidate_id": candidate.candidate_id,
                    "candidate_digest": candidate.digest,
                    "status": "completed",
                    "fault_stage": None,
                    "fault_class": None,
                    "measurement": observation.as_dict(),
                    "physical_primitives": primitives,
                    "process_cpu_scope": scope,
                    "affinity_observation_before": affinity_before.as_dict(),
                    "affinity_observation_after": affinity_after.as_dict(),
                    "transcript": list(session.transcript),
                }
            except Exception as exc:
                stage, fault_class = classify_failure(fault_stage, exc)
                return {
                    "instance": member.instance,
                    "role": member.role,
                    "candidate_id": candidate.candidate_id,
                    "candidate_digest": candidate.digest,
                    "status": "error",
                    "fault_stage": stage,
                    "fault_class": fault_class,
                    "measurement": None,
                    "physical_primitives": None,
                    "process_cpu_scope": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "transcript": list(session.transcript),
                }

        rows: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(composition.members),
            thread_name_prefix="resource-lab-stage-b",
        ) as pool:
            future_map = {
                pool.submit(worker, member): member
                for member in composition.members
            }
            for future, member in future_map.items():
                try:
                    rows.append(future.result())
                except Exception as exc:
                    session = sessions.get(member.instance)
                    stage, fault_class = classify_failure("unknown", exc)
                    rows.append(
                        {
                            "instance": member.instance,
                            "role": member.role,
                            "candidate_id": member.candidate_id,
                            "candidate_digest": member.candidate_digest,
                            "status": "error",
                            "fault_stage": stage,
                            "fault_class": fault_class,
                            "measurement": None,
                            "physical_primitives": None,
                            "process_cpu_scope": None,
                            "error": f"{type(exc).__name__}: {exc}",
                            "transcript": (
                                []
                                if session is None
                                else list(session.transcript)
                            ),
                        }
                    )

        completed = [
            row["measurement"]
            for row in rows
            if row.get("status") == "completed"
            and isinstance(row.get("measurement"), dict)
        ]
        batch_wall_ms = (
            None
            if not completed
            else round(
                max(float(item["wall_ms"]) for item in completed),
                6,
            )
        )
        return {
            "schema_version": 1,
            "composition_id": composition.composition_id,
            "composition_digest": composition.digest,
            "repeat_index": repeat_index,
            "case_id": case.case_id,
            "attempt_index": 0,
            "block_index": block_index,
            "order_index": order_index,
            "attempt_ordinal": attempt_ordinal,
            "status": (
                "completed"
                if all(row["status"] == "completed" for row in rows)
                else "error"
            ),
            "members": sorted(rows, key=lambda row: row["instance"]),
            "batch_wall_ms": batch_wall_ms,
            "aggregate_resource": _aggregate_completed(rows),
            "host_pressure_before": before_pressure,
            "host_pressure_after": _pressure(),
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        stage, fault_class = classify_failure(setup_stage, exc)
        rows = []
        for member in composition.members:
            session = sessions.get(member.instance)
            rows.append(
                {
                    "instance": member.instance,
                    "role": member.role,
                    "candidate_id": member.candidate_id,
                    "candidate_digest": member.candidate_digest,
                    "status": "error",
                    "fault_stage": stage,
                    "fault_class": fault_class,
                    "measurement": None,
                    "physical_primitives": None,
                    "process_cpu_scope": None,
                    "error": f"batch-setup-failure: {error}",
                    "transcript": (
                        [] if session is None else list(session.transcript)
                    ),
                }
            )
        return {
            "schema_version": 1,
            "composition_id": composition.composition_id,
            "composition_digest": composition.digest,
            "repeat_index": repeat_index,
            "case_id": case.case_id,
            "attempt_index": 0,
            "block_index": block_index,
            "order_index": order_index,
            "attempt_ordinal": attempt_ordinal,
            "status": "error",
            "members": sorted(rows, key=lambda row: row["instance"]),
            "batch_wall_ms": None,
            "aggregate_resource": None,
            "host_pressure_before": before_pressure,
            "host_pressure_after": _pressure(),
            "fault_stage": stage,
            "fault_class": fault_class,
            "error": error,
        }
    finally:
        for session in sessions.values():
            session.close()


def _isolated_reference(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    completed_rows = [
        row
        for row in rows
        if row.get("status") == "completed"
        and isinstance(row.get("measurement"), dict)
    ]
    completed = [row["measurement"] for row in completed_rows]
    if len(completed) < 2:
        return {"stable": False, "completed": completed}
    bestmoves = {row["bestmove"] for row in completed}
    work = {int(row["native_work_value"]) for row in completed}
    return {
        "stable": len(bestmoves) == 1 and len(work) == 1,
        "completed": completed,
        "cpu_scope_complete": all(
            isinstance(row.get("process_cpu_scope"), dict)
            and row["process_cpu_scope"].get("complete") is True
            for row in completed_rows
        ),
        "bestmove": next(iter(bestmoves)) if len(bestmoves) == 1 else None,
        "native_work_value": next(iter(work)) if len(work) == 1 else None,
    }


def summarize_composition_interference(
    *,
    rows: list[dict[str, Any]],
    isolated_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    isolated: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in isolated_rows:
        key = (row["candidate_id"], row["case_id"])
        isolated.setdefault(key, []).append(row)

    summaries: list[dict[str, Any]] = []
    composition_ids = sorted({row["composition_id"] for row in rows})
    for composition_id in composition_ids:
        batch_rows = [
            row for row in rows if row["composition_id"] == composition_id
        ]
        member_metrics: dict[str, dict[str, Any]] = {}
        batch_errors = 0
        completed_batch_wall: list[float] = []
        completed_sum_cpu: list[float] = []
        completed_sum_rss: list[float] = []
        completed_sum_hwm: list[float] = []

        for batch in batch_rows:
            if batch.get("status") != "completed":
                batch_errors += 1
            else:
                if batch.get("batch_wall_ms") is not None:
                    completed_batch_wall.append(float(batch["batch_wall_ms"]))
                aggregate = batch.get("aggregate_resource") or {}
                if aggregate.get("sum_cpu_ms") is not None:
                    completed_sum_cpu.append(float(aggregate["sum_cpu_ms"]))
                if aggregate.get("sum_end_rss_bytes") is not None:
                    completed_sum_rss.append(
                        float(aggregate["sum_end_rss_bytes"])
                    )
                if aggregate.get("sum_member_vm_hwm_bytes") is not None:
                    completed_sum_hwm.append(
                        float(aggregate["sum_member_vm_hwm_bytes"])
                    )

            for member in batch.get("members", []):
                item = member_metrics.setdefault(
                    member["instance"],
                    {
                        "wall_ratio": [],
                        "cpu_ratio": [],
                        "bestmove_drift": 0,
                        "native_work_drift": 0,
                        "behavior_evaluated": 0,
                        "reference_unstable": 0,
                        "cpu_scope_incomplete": 0,
                        "completed": 0,
                        "errors": 0,
                    },
                )
                if member.get("status") != "completed":
                    item["errors"] += 1
                    continue

                reference_rows = isolated.get(
                    (member["candidate_id"], batch["case_id"]),
                    [],
                )
                reference = _isolated_reference(reference_rows)
                completed_ref = reference["completed"]
                if not completed_ref:
                    item["errors"] += 1
                    continue

                measurement = member["measurement"]
                wall_ref = statistics.median(
                    float(row["wall_ms"]) for row in completed_ref
                )
                cpu_ref = statistics.median(
                    float(row["cpu_ms"]) for row in completed_ref
                )
                member_scope_complete = (
                    isinstance(member.get("process_cpu_scope"), dict)
                    and member["process_cpu_scope"].get("complete") is True
                )
                if wall_ref > 0:
                    item["wall_ratio"].append(
                        float(measurement["wall_ms"]) / wall_ref
                    )
                if (
                    cpu_ref > 0
                    and reference.get("cpu_scope_complete") is True
                    and member_scope_complete
                ):
                    item["cpu_ratio"].append(
                        float(measurement["cpu_ms"]) / cpu_ref
                    )
                else:
                    item["cpu_scope_incomplete"] += 1

                if reference["stable"]:
                    item["behavior_evaluated"] += 1
                    if measurement["bestmove"] != reference["bestmove"]:
                        item["bestmove_drift"] += 1
                    if (
                        int(measurement["native_work_value"])
                        != int(reference["native_work_value"])
                    ):
                        item["native_work_drift"] += 1
                else:
                    item["reference_unstable"] += 1
                item["completed"] += 1

        members: dict[str, Any] = {}
        for instance, item in sorted(member_metrics.items()):
            wall_ratios = list(item["wall_ratio"])
            cpu_ratios = list(item["cpu_ratio"])
            members[instance] = {
                "completed": item["completed"],
                "errors": item["errors"],
                "behavior_evaluated_count": item["behavior_evaluated"],
                "reference_unstable_count": item["reference_unstable"],
                "cpu_scope_incomplete_count": item["cpu_scope_incomplete"],
                "median_wall_slowdown_ratio": (
                    None
                    if not wall_ratios
                    else round(statistics.median(wall_ratios), 6)
                ),
                "p95_wall_slowdown_ratio": (
                    None
                    if not wall_ratios
                    else round(percentile(wall_ratios, 0.95), 6)
                ),
                "median_cpu_inflation_ratio": (
                    None
                    if not cpu_ratios
                    else round(statistics.median(cpu_ratios), 6)
                ),
                "p95_cpu_inflation_ratio": (
                    None
                    if not cpu_ratios
                    else round(percentile(cpu_ratios, 0.95), 6)
                ),
                "bestmove_drift_count": item["bestmove_drift"],
                "native_work_drift_count": item["native_work_drift"],
            }

        summaries.append(
            {
                "composition_id": composition_id,
                "batches": len(batch_rows),
                "batch_errors": batch_errors,
                "batch_wall_ms": (
                    None
                    if not completed_batch_wall
                    else {
                        "median": round(
                            statistics.median(completed_batch_wall),
                            6,
                        ),
                        "p95": round(
                            percentile(completed_batch_wall, 0.95),
                            6,
                        ),
                    }
                ),
                "sum_cpu_ms": (
                    None
                    if not completed_sum_cpu
                    else {
                        "median": round(
                            statistics.median(completed_sum_cpu),
                            6,
                        ),
                        "p95": round(
                            percentile(completed_sum_cpu, 0.95),
                            6,
                        ),
                    }
                ),
                "sum_end_rss_bytes": (
                    None
                    if not completed_sum_rss
                    else {
                        "median": round(
                            statistics.median(completed_sum_rss),
                            3,
                        ),
                        "p95": round(
                            percentile(completed_sum_rss, 0.95),
                            3,
                        ),
                    }
                ),
                "sum_member_vm_hwm_bytes": (
                    None
                    if not completed_sum_hwm
                    else {
                        "median": round(
                            statistics.median(completed_sum_hwm),
                            3,
                        ),
                        "p95": round(
                            percentile(completed_sum_hwm, 0.95),
                            3,
                        ),
                    }
                ),
                "members": members,
            }
        )

    return {
        "schema_version": 1,
        "kind": "resource-lab-stage-b-interference",
        "compositions": summaries,
        "claim_boundary": {
            "interference_measurement": True,
            "profile_selection": False,
            "strength": False,
            "elo": False,
            "deployment": False,
        },
    }
