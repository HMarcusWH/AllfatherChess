from __future__ import annotations
import statistics
from typing import Any


def summarize(rows: list[dict[str,Any]]) -> dict[str,Any]:
    if not rows:
        raise ValueError("cannot summarize empty engine-opt result set")
    walls=[float(row["metrics"]["wall_ms"]) for row in rows]
    completed=sum(bool(row["metrics"]["completed_before_deadline"]) for row in rows)
    moves=[row["metrics"]["bestmove"] for row in rows]
    return {
        "cases":len(rows),
        "completion_rate":completed/len(rows),
        "median_wall_ms":statistics.median(walls),
        "max_wall_ms":max(walls),
        "bestmoves":moves,
    }


def bestmove_agreement(left: list[dict[str,Any]], right: list[dict[str,Any]]) -> float:
    a={row["case_id"]:row["metrics"]["bestmove"] for row in left}
    b={row["case_id"]:row["metrics"]["bestmove"] for row in right}
    keys=sorted(set(a)&set(b))
    if not keys:
        raise ValueError("no overlapping engine-opt cases")
    return sum(a[k]==b[k] for k in keys)/len(keys)
