"""Concurrent whole-machine interference experiments for J6 Stage B."""

from __future__ import annotations

import concurrent.futures
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from adapters.resource.linux_affinity import LinuxAffinityProvider
from adapters.resource.linux_proc import LinuxProcProvider
from controller.host_pressure import discover_host_pressure
from tests.harness.uci_session import UciSession

from tools.engine_opt.corpus import PositionCase

from .candidate_matrix import Candidate, CompositionCandidate
from .measure import parse_search_observation, percentile


class CompositionRunError(RuntimeError):
    pass


def _pressure() -> dict[str, Any]:
    try:
        return {"status": "completed", "observation": discover_host_pressure().as_dict()}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def run_composition_batch(
    *,
    composition: CompositionCandidate,
    candidate_map: dict[str, Candidate],
    bundle_root: Path,
    case: PositionCase,
    repeat_index: int,
    deadline_ms: float,
) -> dict[str, Any]:
    sessions: dict[str, UciSession] = {}
    proc = LinuxProcProvider()
    affinity = LinuxAffinityProvider()
    before_pressure = _pressure()
    ready_barrier = threading.Barrier(len(composition.members))
    start_barrier = threading.Barrier(len(composition.members))

    try:
        for member in composition.members:
            candidate = candidate_map[member.candidate_id]
            session = UciSession(
                bundle_root / candidate.binary_relpath,
                cwd=bundle_root.parents[1],
                timeout=max(10.0, deadline_ms / 1000.0 + 5.0),
                args=list(candidate.args),
                environment=dict(candidate.environment),
                start_new_session=True,
            )
            session.start()
            session.configure(candidate.execution_options(bundle_root))
            session.new_game()
            if candidate.warmup_nodes is not None:
                session.set_position({"startpos_moves": []})
                session.search_nodes(
                    candidate.warmup_nodes,
                    timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
                )
                session.new_game()
            session.set_position({"fen": case.fen, "moves": []})
            sessions[member.instance] = session

        batch_started = time.monotonic()

        def worker(member):
            candidate = candidate_map[member.candidate_id]
            session = sessions[member.instance]
            if session.proc is None:
                raise CompositionRunError(f"{member.instance}: process missing")
            pid = session.proc.pid
            affinity_before = affinity.inspect_tree_affinity(pid).as_dict()
            ready_barrier.wait(timeout=max(5.0, deadline_ms / 1000.0))
            start = proc.snapshot(pid)
            start_barrier.wait(timeout=max(5.0, deadline_ms / 1000.0))
            lines = session.search_nodes(
                candidate.nodes,
                timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
            )
            end = proc.snapshot(pid)
            affinity_after = affinity.inspect_tree_affinity(pid).as_dict()
            delta = proc.delta(start, end)
            observation = parse_search_observation(
                lines,
                family=candidate.family,
                delta=delta,
            )
            return {
                "instance": member.instance,
                "role": member.role,
                "candidate_id": candidate.candidate_id,
                "candidate_digest": candidate.digest,
                "status": "completed",
                "measurement": observation.as_dict(),
                "affinity_before": affinity_before,
                "affinity_after": affinity_after,
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
                    rows.append(
                        {
                            "instance": member.instance,
                            "role": member.role,
                            "candidate_id": member.candidate_id,
                            "candidate_digest": member.candidate_digest,
                            "status": "error",
                            "measurement": None,
                            "error": f"{type(exc).__name__}: {exc}",
                            "transcript": [] if session is None else list(session.transcript),
                        }
                    )
        batch_wall_ms = (time.monotonic() - batch_started) * 1000.0
        return {
            "schema_version": 1,
            "composition_id": composition.composition_id,
            "composition_digest": composition.digest,
            "repeat_index": repeat_index,
            "case_id": case.case_id,
            "attempt_index": 0,
            "status": "completed" if all(row["status"] == "completed" for row in rows) else "error",
            "members": sorted(rows, key=lambda row: row["instance"]),
            "batch_wall_ms": round(batch_wall_ms, 3),
            "host_pressure_before": before_pressure,
            "host_pressure_after": _pressure(),
        }
    finally:
        for session in sessions.values():
            session.close()


def summarize_composition_interference(
    *,
    rows: list[dict[str, Any]],
    isolated_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    isolated: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in isolated_rows:
        if row.get("status") != "completed":
            continue
        key = (row["candidate_id"], row["case_id"])
        isolated.setdefault(key, []).append(row["measurement"])

    summaries: list[dict[str, Any]] = []
    composition_ids = sorted({row["composition_id"] for row in rows})
    for composition_id in composition_ids:
        batch_rows = [row for row in rows if row["composition_id"] == composition_id]
        member_metrics: dict[str, dict[str, list[float] | int]] = {}
        batch_errors = 0
        for batch in batch_rows:
            if batch.get("status") != "completed":
                batch_errors += 1
            for member in batch.get("members", []):
                item = member_metrics.setdefault(
                    member["instance"],
                    {
                        "wall_ratio": [],
                        "cpu_ratio": [],
                        "bestmove_drift": 0,
                        "native_work_drift": 0,
                        "completed": 0,
                        "errors": 0,
                    },
                )
                if member.get("status") != "completed":
                    item["errors"] = int(item["errors"]) + 1
                    continue
                reference = isolated.get((member["candidate_id"], batch["case_id"]), [])
                if not reference:
                    item["errors"] = int(item["errors"]) + 1
                    continue
                wall_ref = statistics.median(float(row["wall_ms"]) for row in reference)
                cpu_ref = statistics.median(float(row["cpu_ms"]) for row in reference)
                measurement = member["measurement"]
                if wall_ref > 0:
                    item["wall_ratio"].append(float(measurement["wall_ms"]) / wall_ref)
                if cpu_ref > 0:
                    item["cpu_ratio"].append(float(measurement["cpu_ms"]) / cpu_ref)
                if measurement["bestmove"] != reference[0]["bestmove"]:
                    item["bestmove_drift"] = int(item["bestmove_drift"]) + 1
                if measurement["native_work_value"] != reference[0]["native_work_value"]:
                    item["native_work_drift"] = int(item["native_work_drift"]) + 1
                item["completed"] = int(item["completed"]) + 1

        members: dict[str, Any] = {}
        for instance, item in sorted(member_metrics.items()):
            wall_ratios = list(item["wall_ratio"])
            cpu_ratios = list(item["cpu_ratio"])
            members[instance] = {
                "completed": item["completed"],
                "errors": item["errors"],
                "median_wall_slowdown_ratio": (
                    None if not wall_ratios else round(statistics.median(wall_ratios), 6)
                ),
                "p95_wall_slowdown_ratio": (
                    None if not wall_ratios else round(percentile(wall_ratios, 0.95), 6)
                ),
                "median_cpu_inflation_ratio": (
                    None if not cpu_ratios else round(statistics.median(cpu_ratios), 6)
                ),
                "p95_cpu_inflation_ratio": (
                    None if not cpu_ratios else round(percentile(cpu_ratios, 0.95), 6)
                ),
                "bestmove_drift_count": item["bestmove_drift"],
                "native_work_drift_count": item["native_work_drift"],
            }
        summaries.append(
            {
                "composition_id": composition_id,
                "batches": len(batch_rows),
                "batch_errors": batch_errors,
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
