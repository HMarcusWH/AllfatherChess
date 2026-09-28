from __future__ import annotations
import statistics
from typing import Any


def _median_optional(values: list[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def _max_optional(values: list[float]) -> float | None:
    return None if not values else float(max(values))


def summarize(rows: list[dict[str,Any]]) -> dict[str,Any]:
    if not rows:
        raise ValueError("cannot summarize empty engine-opt result set")
    walls=[float(row["metrics"]["wall_ms"]) for row in rows]
    cpus=[
        float(row["metrics"]["cpu_ms"])
        for row in rows
        if isinstance(row["metrics"].get("cpu_ms"),(int,float))
        and not isinstance(row["metrics"].get("cpu_ms"),bool)
    ]
    rss=[
        float(row["metrics"]["rss_kib_end"])
        for row in rows
        if isinstance(row["metrics"].get("rss_kib_end"),(int,float))
        and not isinstance(row["metrics"].get("rss_kib_end"),bool)
    ]
    native=[row["metrics"].get("native_work_value") for row in rows]
    semantics=sorted({
        str(row["metrics"].get("native_work_semantics"))
        for row in rows
        if row["metrics"].get("native_work_semantics") is not None
    })
    completed=sum(bool(row["metrics"]["completed_before_deadline"]) for row in rows)
    moves=[row["metrics"]["bestmove"] for row in rows]
    return {
        "cases":len(rows),
        "completion_rate":completed/len(rows),
        "median_wall_ms":statistics.median(walls),
        "max_wall_ms":max(walls),
        "median_cpu_ms":_median_optional(cpus),
        "max_cpu_ms":_max_optional(cpus),
        "median_rss_kib":_median_optional(rss),
        "max_rss_kib":_max_optional(rss),
        "bestmoves":moves,
        "native_work_values":native,
        "native_work_semantics":semantics,
    }


def bestmove_agreement(left: list[dict[str,Any]], right: list[dict[str,Any]]) -> float:
    a={row["case_id"]:row["metrics"]["bestmove"] for row in left}
    b={row["case_id"]:row["metrics"]["bestmove"] for row in right}
    keys=sorted(set(a)&set(b))
    if not keys:
        raise ValueError("no overlapping engine-opt cases")
    return sum(a[k]==b[k] for k in keys)/len(keys)
