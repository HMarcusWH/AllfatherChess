"""Deterministic paired-opening and intervention summaries for META-1."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import fmean
from typing import Iterable

from tools.local_game.common import require
from .common import ARMS


LIVE_ARM = "allfather-orchestrated"
CONTROL_ARM = "allfather-anchor-control"


def _points(result: str, arm: str, white: str, black: str) -> float:
    require(arm in (white, black), f"{arm} missing from game pairing")
    if result == "1/2-1/2":
        return 0.5
    require(result in ("1-0", "0-1"), f"unsupported game result: {result!r}")
    winner = white if result == "1-0" else black
    return 1.0 if winner == arm else 0.0


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(float(ordered[index]), 6)


def paired_blocks(games: list[dict], plies: list[dict]) -> tuple[list[dict], dict]:
    by_opening: dict[int, list[dict]] = defaultdict(list)
    for game in games:
        by_opening[int(game["opening_index"])].append(game)
    require(len(by_opening) == 50, "META-1 paired report requires 50 opening blocks")

    ply_groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in plies:
        ply_groups[(str(row["block"]), int(row["game"]))].append(row)

    blocks: list[dict] = []
    live_higher = tied = control_higher = 0
    live_total = control_total = 0.0
    for opening_index in range(50):
        rows = sorted(by_opening.get(opening_index, []), key=lambda row: row["game"])
        require(len(rows) == 2, f"opening {opening_index} does not contain exactly two games")
        require(
            {rows[0]["white"], rows[0]["black"]} == set(ARMS)
            and {rows[1]["white"], rows[1]["black"]} == set(ARMS),
            f"opening {opening_index} has an invalid arm pairing",
        )
        require(
            rows[0]["white"] == rows[1]["black"]
            and rows[0]["black"] == rows[1]["white"],
            f"opening {opening_index} did not reverse colors",
        )
        live_points = sum(
            _points(row["result"], LIVE_ARM, row["white"], row["black"])
            for row in rows
        )
        control_points = sum(
            _points(row["result"], CONTROL_ARM, row["white"], row["black"])
            for row in rows
        )
        delta = live_points - control_points
        if delta > 0:
            live_higher += 1
        elif delta < 0:
            control_higher += 1
        else:
            tied += 1
        live_total += live_points
        control_total += control_points

        game_details = []
        for row in rows:
            gp = ply_groups.get((row["block"], row["game"]), [])
            hybrid = [p for p in gp if p.get("arm") == LIVE_ARM and p.get("override")]
            suppressed = [
                p
                for p in gp
                if p.get("arm") == CONTROL_ARM
                and p.get("suppressed_authorized_non_anchor")
            ]
            game_details.append(
                {
                    "game": row["game"],
                    "white": row["white"],
                    "black": row["black"],
                    "result": row["result"],
                    "hybrid_interventions": len(hybrid),
                    "control_suppressions": len(suppressed),
                    "first_hybrid_intervention_ply": (
                        min(int(p["ply"]) for p in hybrid) if hybrid else None
                    ),
                    "first_control_suppression_ply": (
                        min(int(p["ply"]) for p in suppressed) if suppressed else None
                    ),
                }
            )
        blocks.append(
            {
                "opening_index": opening_index,
                "block": rows[0]["block"],
                "live_points": live_points,
                "control_points": control_points,
                "point_delta": delta,
                "games": game_details,
                "hybrid_interventions": sum(
                    row["hybrid_interventions"] for row in game_details
                ),
                "control_suppressions": sum(
                    row["control_suppressions"] for row in game_details
                ),
            }
        )

    summary = {
        "blocks": 50,
        "live_higher": live_higher,
        "tied": tied,
        "control_higher": control_higher,
        "live_points": live_total,
        "control_points": control_total,
        "sum_point_delta": live_total - control_total,
        "mean_point_delta": (live_total - control_total) / 50.0,
    }
    return blocks, summary


def arm_telemetry(plies: Iterable[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    for row in plies:
        arm = row.get("arm")
        if arm in grouped:
            grouped[arm].append(row)

    result: dict[str, dict] = {}
    for arm, rows in grouped.items():
        response = [
            float(row["response_ms"])
            for row in rows
            if isinstance(row.get("response_ms"), (int, float))
            and not isinstance(row.get("response_ms"), bool)
        ]
        physical = [
            float(row["physical_cpu_ms"])
            for row in rows
            if isinstance(row.get("physical_cpu_ms"), (int, float))
            and not isinstance(row.get("physical_cpu_ms"), bool)
        ]
        route_counts = Counter(
            str(row.get("route_action"))
            for row in rows
            if row.get("route_action") is not None
        )
        result[arm] = {
            "plies": len(rows),
            "authorization_grants": sum(
                row.get("authorization_granted") is True for row in rows
            ),
            "authorized_non_anchor_proposals": sum(
                bool(row.get("authorized_non_anchor")) for row in rows
            ),
            "hybrid_interventions": sum(bool(row.get("override")) for row in rows),
            "suppressed_authorized_non_anchor": sum(
                bool(row.get("suppressed_authorized_non_anchor")) for row in rows
            ),
            "work_grants_authorized": sum(
                int(row.get("work_grants_authorized") or 0) for row in rows
            ),
            "work_grants_settled": sum(
                int(row.get("work_grants_settled") or 0) for row in rows
            ),
            "physical_cpu_ms": round(sum(physical), 3),
            "physical_cpu_ms_missing": len(rows) - len(physical),
            "response_ms": {
                "count": len(response),
                "mean": round(fmean(response), 6) if response else None,
                "p50": _percentile(response, 0.50),
                "p95": _percentile(response, 0.95),
                "max": round(max(response), 6) if response else None,
            },
            "route_actions": dict(sorted(route_counts.items())),
        }
    return result
