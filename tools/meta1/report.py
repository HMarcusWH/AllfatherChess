"""Descriptive META-1 result aggregation. No Elo or superiority inference."""

from __future__ import annotations


def empty_scores(arms: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {arm: {"W": 0, "D": 0, "L": 0, "games": 0} for arm in arms}


def record_result(scores: dict[str, dict[str, int]], white: str, black: str, result: str) -> None:
    for arm, is_white in ((white, True), (black, False)):
        if result == "1/2-1/2":
            outcome = "D"
        else:
            outcome = "W" if ((result == "1-0") == is_white) else "L"
        scores[arm][outcome] += 1
        scores[arm]["games"] += 1
