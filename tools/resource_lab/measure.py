"""UCI search evidence parsing and reconstructible physical measurements for J6."""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from adapters.resource.linux_proc import LinuxProcProvider, ProcessSnapshot
from .process_cpu import PROCESS_CPU_METHOD, ProcessCpuClockEvidence


_BESTMOVE = re.compile(r"^bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000|\(none\))")
_NODES = re.compile(r"(?:^| )nodes (\d+)(?: |$)")
_NPS = re.compile(r"(?:^| )nps (\d+)(?: |$)")
_SCORE = re.compile(r"(?:^| )score (cp|mate) (-?\d+)(?: (lowerbound|upperbound))?(?: |$)")
_WDL = re.compile(r"(?:^| )wdl (-?\d+) (-?\d+) (-?\d+)(?: |$)")
_PV = re.compile(r"(?:^| )pv ((?:[a-h][1-8][a-h][1-8][qrbn]?(?: |$))+)")


class ResourceMeasurementError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceMeasurementError(message)


@dataclass(frozen=True)
class Evaluation:
    kind: str
    value: int | tuple[int, int, int]
    bound: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "bound": self.bound}


@dataclass(frozen=True)
class PhysicalMeasurement:
    pid: int
    process_start_time_ticks: int
    wall_ms: float
    cpu_ms: float
    procfs_cpu_ms: float
    cpu_clock_method: str
    cpu_clock_resolution_ns: int
    start_rss_bytes: int | None
    end_rss_bytes: int | None
    vm_hwm_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "process_start_time_ticks": self.process_start_time_ticks,
            "wall_ms": round(self.wall_ms, 6),
            "cpu_ms": round(self.cpu_ms, 6),
            "procfs_cpu_ms": round(self.procfs_cpu_ms, 6),
            "cpu_clock_method": self.cpu_clock_method,
            "cpu_clock_resolution_ns": self.cpu_clock_resolution_ns,
            "start_rss_bytes": self.start_rss_bytes,
            "end_rss_bytes": self.end_rss_bytes,
            "vm_hwm_bytes": self.vm_hwm_bytes,
        }


@dataclass(frozen=True)
class SearchObservation:
    bestmove: str
    pv: tuple[str, ...]
    evaluation: Evaluation | None
    native_work_value: int
    native_work_semantics: str
    nps: int | None
    physical: PhysicalMeasurement

    def as_dict(self) -> dict[str, Any]:
        return {
            "bestmove": self.bestmove,
            "pv": list(self.pv),
            "evaluation": None if self.evaluation is None else self.evaluation.as_dict(),
            "native_work_value": self.native_work_value,
            "native_work_semantics": self.native_work_semantics,
            "nps": self.nps,
            **self.physical.as_dict(),
        }


def process_snapshot_from_dict(raw: Mapping[str, Any]) -> ProcessSnapshot:
    expected = {
        "pid",
        "start_time_ticks",
        "monotonic_ns",
        "user_cpu_ticks",
        "system_cpu_ticks",
        "rss_bytes",
        "vm_hwm_bytes",
    }
    require(isinstance(raw, Mapping) and set(raw) == expected, "process snapshot fields differ from schema")
    return ProcessSnapshot(
        pid=raw.get("pid"),
        start_time_ticks=raw.get("start_time_ticks"),
        monotonic_ns=raw.get("monotonic_ns"),
        user_cpu_ticks=raw.get("user_cpu_ticks"),
        system_cpu_ticks=raw.get("system_cpu_ticks"),
        rss_bytes=raw.get("rss_bytes"),
        vm_hwm_bytes=raw.get("vm_hwm_bytes"),
    )


def physical_primitives(
    identity_binding: ProcessSnapshot,
    before: ProcessSnapshot,
    after: ProcessSnapshot,
    cpu_clock: ProcessCpuClockEvidence,
) -> dict[str, Any]:
    return {
        "identity_binding": identity_binding.as_dict(),
        "proc_before": before.as_dict(),
        "proc_after": after.as_dict(),
        "process_cpu_clock": cpu_clock.as_dict(),
    }


def reconstruct_physical_measurement(
    primitives: Mapping[str, Any],
    *,
    clock_ticks_per_second: int,
    required_cpu_method: str,
    max_cpu_resolution_ns: int,
) -> PhysicalMeasurement:
    require(isinstance(primitives, Mapping), "physical primitives must be object")
    require(
        set(primitives)
        == {"identity_binding", "proc_before", "proc_after", "process_cpu_clock"},
        "physical primitive fields differ from schema",
    )
    identity_binding = process_snapshot_from_dict(primitives["identity_binding"])
    before = process_snapshot_from_dict(primitives["proc_before"])
    after = process_snapshot_from_dict(primitives["proc_after"])
    require(
        identity_binding.pid == before.pid == after.pid,
        "process identity binding PID mismatch",
    )
    require(
        identity_binding.start_time_ticks
        == before.start_time_ticks
        == after.start_time_ticks,
        "process identity binding start-time mismatch",
    )
    provider = LinuxProcProvider(clock_ticks=clock_ticks_per_second)
    delta = provider.delta(before, after)
    cpu_clock = ProcessCpuClockEvidence.from_dict(dict(primitives["process_cpu_clock"]))
    require(cpu_clock.method == required_cpu_method == PROCESS_CPU_METHOD, "CPU measurement method drift")
    require(cpu_clock.pid == before.pid == after.pid, "CPU clock/process PID mismatch")
    require(
        cpu_clock.resolution_ns <= max_cpu_resolution_ns,
        "CPU clock resolution exceeds frozen J6 maximum",
    )
    cpu_ms = cpu_clock.delta_ns / 1_000_000.0
    require(math.isfinite(cpu_ms) and cpu_ms >= 0, "high-resolution CPU delta invalid")
    return PhysicalMeasurement(
        pid=before.pid,
        process_start_time_ticks=before.start_time_ticks,
        wall_ms=float(delta.wall_ms),
        cpu_ms=cpu_ms,
        procfs_cpu_ms=float(delta.cpu_ms),
        cpu_clock_method=cpu_clock.method,
        cpu_clock_resolution_ns=cpu_clock.resolution_ns,
        start_rss_bytes=delta.start_rss_bytes,
        end_rss_bytes=delta.end_rss_bytes,
        vm_hwm_bytes=delta.vm_hwm_bytes,
    )


def _family_semantics(family: str) -> str:
    return {
        "stockfish": "stockfish.uci_nodes",
        "reckless": "reckless.uci_nodes",
        "lc0": "lc0.uci_nodes",
    }.get(family, f"{family}.uci_nodes")


def parse_search_observation(
    lines: Iterable[str],
    *,
    family: str,
    physical: PhysicalMeasurement,
) -> SearchObservation:
    bestmove: str | None = None
    nodes: int | None = None
    nps: int | None = None
    pv: tuple[str, ...] = ()
    evaluation: Evaluation | None = None

    for raw in lines:
        line = raw[3:] if raw.startswith("<< ") else raw
        best = _BESTMOVE.match(line)
        if best is not None:
            value = best.group(1).lower()
            bestmove = None if value in ("0000", "(none)") else value
            continue
        if not line.startswith("info"):
            continue
        node_match = _NODES.search(line)
        if node_match is not None:
            nodes = int(node_match.group(1))
        nps_match = _NPS.search(line)
        if nps_match is not None:
            nps = int(nps_match.group(1))
        pv_match = _PV.search(line)
        if pv_match is not None:
            pv = tuple(pv_match.group(1).strip().split())
        score_match = _SCORE.search(line)
        if score_match is not None:
            evaluation = Evaluation(
                kind=score_match.group(1),
                value=int(score_match.group(2)),
                bound=score_match.group(3),
            )
        else:
            wdl_match = _WDL.search(line)
            if wdl_match is not None:
                evaluation = Evaluation(
                    kind="wdl",
                    value=tuple(int(wdl_match.group(index)) for index in (1, 2, 3)),
                    bound=None,
                )

    require(bestmove is not None, "measured search produced no canonical bestmove")
    require(nodes is not None and nodes >= 0, "measured search produced no native-work counter")
    require(math.isfinite(physical.wall_ms) and physical.wall_ms >= 0, "wall measurement invalid")
    require(math.isfinite(physical.cpu_ms) and physical.cpu_ms >= 0, "CPU measurement invalid")
    return SearchObservation(
        bestmove=bestmove,
        pv=pv,
        evaluation=evaluation,
        native_work_value=nodes,
        native_work_semantics=_family_semantics(family),
        nps=nps,
        physical=physical,
    )


def percentile(values: Iterable[float], fraction: float) -> float:
    rows = sorted(float(value) for value in values)
    require(rows and 0 < fraction <= 1, "percentile requires values and 0<fraction<=1")
    require(all(math.isfinite(value) for value in rows), "percentile contains non-finite value")
    index = max(0, min(len(rows) - 1, math.ceil(fraction * len(rows)) - 1))
    return rows[index]


def summarize_numeric(values: Iterable[float]) -> dict[str, float]:
    rows = [float(value) for value in values]
    require(rows and all(math.isfinite(value) for value in rows), "summary values invalid")
    return {
        "median": round(statistics.median(rows), 6),
        "p95": round(percentile(rows, 0.95), 6),
        "max": round(max(rows), 6),
    }


def candidate_summary(
    rows: list[dict[str, Any]],
    *,
    case_ids: tuple[str, ...],
    repeats: int,
) -> dict[str, Any]:
    expected = {(repeat, case) for repeat in range(repeats) for case in case_ids}
    seen: set[tuple[int, str]] = set()
    complete_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    by_repeat_best: list[tuple[str, ...]] = []
    by_repeat_work: list[tuple[int, ...]] = []

    for row in rows:
        repeat = row.get("repeat_index")
        case = row.get("case_id")
        key = (repeat, case)
        if (
            isinstance(repeat, bool)
            or not isinstance(repeat, int)
            or repeat < 0
            or not isinstance(case, str)
            or not case
            or key in seen
        ):
            errors.append(f"invalid-or-duplicate-row:{key!r}")
            continue
        seen.add(key)
        if row.get("status") != "completed":
            errors.append(f"{repeat}:{case}:{row.get('error') or 'measurement-failed'}")
            continue
        measurement = row.get("measurement")
        if not isinstance(measurement, dict):
            errors.append(f"{repeat}:{case}:missing-measurement")
            continue
        complete_rows.append(row)

    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing:
        errors.append(f"missing:{missing}")
    if extra:
        errors.append(f"extra:{extra}")

    for repeat in range(repeats):
        batch = sorted(
            (
                row
                for row in complete_rows
                if row["repeat_index"] == repeat and row["case_id"] in case_ids
            ),
            key=lambda row: case_ids.index(row["case_id"]),
        )
        if len(batch) != len(case_ids):
            continue
        by_repeat_best.append(tuple(row["measurement"]["bestmove"] for row in batch))
        by_repeat_work.append(
            tuple(int(row["measurement"]["native_work_value"]) for row in batch)
        )

    valid = len(complete_rows) == len(expected) and not errors
    bestmove_repeatable = bool(by_repeat_best) and len(set(by_repeat_best)) == 1
    native_work_repeatable = bool(by_repeat_work) and len(set(by_repeat_work)) == 1

    summary: dict[str, Any] = {
        "complete": valid,
        "expected_measurements": len(expected),
        "completed_measurements": len(complete_rows),
        "errors": errors,
        "bestmove_repeatable": bestmove_repeatable,
        "native_work_repeatable": native_work_repeatable,
        "bestmove_vectors": [list(vector) for vector in by_repeat_best],
        "native_work_vectors": [list(vector) for vector in by_repeat_work],
    }
    if complete_rows:
        measurements = [row["measurement"] for row in complete_rows]
        cpu_values = [float(item["cpu_ms"]) for item in measurements]
        scope_complete = all(
            isinstance(row.get("process_cpu_scope"), dict)
            and row["process_cpu_scope"].get("complete") is True
            for row in complete_rows
        )
        positive_cpu = sum(value > 0.0 for value in cpu_values)
        summary.update(
            {
                "cpu_measurement_quality": {
                    "usable": positive_cpu > 0,
                    "positive_count": positive_cpu,
                    "zero_count": len(cpu_values) - positive_cpu,
                    "zero_fraction": round(
                        (len(cpu_values) - positive_cpu) / len(cpu_values),
                        6,
                    ),
                    "min_positive_cpu_ms": (
                        None
                        if positive_cpu == 0
                        else round(min(value for value in cpu_values if value > 0.0), 6)
                    ),
                },
                "process_cpu_scope_complete": scope_complete,
                "wall_ms": summarize_numeric(item["wall_ms"] for item in measurements),
                "cpu_ms": summarize_numeric(item["cpu_ms"] for item in measurements),
                "procfs_cpu_ms": summarize_numeric(
                    item["procfs_cpu_ms"] for item in measurements
                ),
                "cpu_clock_resolution_ns": max(
                    int(item["cpu_clock_resolution_ns"]) for item in measurements
                ),
                "vm_hwm_bytes": (
                    summarize_numeric(
                        item["vm_hwm_bytes"]
                        for item in measurements
                        if item.get("vm_hwm_bytes") is not None
                    )
                    if any(item.get("vm_hwm_bytes") is not None for item in measurements)
                    else None
                ),
            }
        )
    return summary
