"""Pure, offline verification for non-promoting Reckless build/runtime diagnostics."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

PROTOCOL_ID = "reckless-derived-pristine-repro-v1"
REPEATS = 5
NODES = 200000


class DiagnosticError(ValueError):
    """Malformed or incomplete diagnostic evidence; never a promotion decision."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticError(message)


def schedule(case_ids: list[str], *, repeats: int = REPEATS) -> list[dict[str, Any]]:
    require(repeats == REPEATS, "diagnostic repeat count drift")
    require(len(case_ids) == 8 and len(set(case_ids)) == 8, "expected eight distinct frozen positions")
    slots: list[dict[str, Any]] = []
    for block in range(repeats):
        for variant in ("derived-a", "derived-b"):
            for case_index, case_id in enumerate(case_ids):
                # ABBA and BAAB cancel linear first/last-mover drift.
                order = ([variant, "pristine", "pristine", variant]
                         if (block + case_index) % 2 == 0
                         else ["pristine", variant, variant, "pristine"])
                for order_index, binary in enumerate(order):
                    slots.append({
                        "repeat_index": block,
                        "variant": variant,
                        "case_index": case_index,
                        "case_id": case_id,
                        "order_index": order_index,
                        "binary": binary,
                    })
    return slots


def analyze(rows: list[dict[str, Any]], case_ids: list[str]) -> dict[str, Any]:
    expected = schedule(case_ids)
    require(len(rows) == len(expected), "diagnostic incomplete: planned case count differs")
    pairs: dict[tuple[int, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for index, (row, slot) in enumerate(zip(rows, expected)):
        require(isinstance(row, dict), f"row {index}: not a mapping")
        require(all(row.get(key) == value for key, value in slot.items()),
                f"row {index}: ordering/identity drift")
        metric = row.get("metrics")
        require(isinstance(metric, dict), f"row {index}: missing metrics")
        require(metric.get("completed_before_deadline") is True,
                f"row {index}: timed out")
        work = metric.get("native_work_value")
        require(type(work) is int and work >= NODES,
                f"row {index}: missing/insufficient native work")
        wall = metric.get("wall_ms")
        cpu = metric.get("cpu_ms")
        require(type(wall) in (int, float) and math.isfinite(wall) and wall > 0,
                f"row {index}: invalid wall")
        require(type(cpu) in (int, float) and math.isfinite(cpu) and cpu >= 0,
                f"row {index}: invalid cpu")
        require(isinstance(metric.get("bestmove"), str) and metric["bestmove"],
                f"row {index}: bestmove missing")
        pairs[(slot["repeat_index"],slot["variant"],slot["case_id"])][slot["binary"]].append(metric)

    out: dict[str, Any] = {}
    for variant in ("derived-a", "derived-b"):
        block_ratios = []
        mismatches = []
        work_mismatches = []
        for block in range(REPEATS):
            ratios = []
            for case in case_ids:
                group = pairs[(block, variant, case)]
                require(len(group[variant]) == 2 and len(group["pristine"]) == 2,
                        f"{variant}/{block}/{case}: incomplete pair")
                measured = group[variant] + group["pristine"]
                moves = {x["bestmove"] for x in measured}
                if len(moves) > 1:
                    mismatches.append(f"{block}/{case}")
                counts = {x["native_work_value"] for x in measured}
                if len(counts) > 1:
                    work_mismatches.append(f"{block}/{case}")
                left = statistics.median(x["wall_ms"] for x in group[variant])
                right = statistics.median(x["wall_ms"] for x in group["pristine"])
                ratios.append(math.log(left / right))
            block_ratios.append(math.exp(statistics.mean(ratios)))
        out[variant] = {
            "per_block_derived_over_pristine": block_ratios,
            "mean_log_ratio_backtransformed": math.exp(statistics.mean(map(math.log, block_ratios))),
            "minimum_block_ratio": min(block_ratios),
            "maximum_block_ratio": max(block_ratios),
            "bestmove_mismatches": mismatches,
            "native_work_mismatches": work_mismatches,
            "frozen_gate_applied": False,
            "qualification_claim": False,
        }
    return {"protocol_id": PROTOCOL_ID, "planned": len(expected),
            "observed": len(rows), "comparisons": out,
            "diagnostic_complete": True, "promotion_evidence": False}
