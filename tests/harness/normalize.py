"""Normalize engine UCI search output into stable semantic fields."""

from __future__ import annotations

import re
from typing import Any, Iterable


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_NONE_MOVES = {"(none)", "none", "0000", "a1a1"}


def is_uci_move(value: str) -> bool:
    return bool(_MOVE_RE.fullmatch(value))


def normalize_bestmove(value: str) -> str | None:
    lowered = value.lower()
    if lowered in _NONE_MOVES:
        return None
    if not is_uci_move(lowered):
        raise ValueError(f"malformed UCI bestmove: {value}")
    return lowered


def _last_int(line: str, key: str) -> int | None:
    match = re.search(rf"(?:^|\s){re.escape(key)}\s+(-?\d+)(?:\s|$)", line)
    return int(match.group(1)) if match else None


def _parse_score(line: str) -> dict[str, Any] | None:
    match = re.search(r"(?:^|\s)score\s+(cp|mate)\s+(-?\d+)(?:\s|$)", line)
    if not match:
        return None
    result: dict[str, Any] = {"type": match.group(1), "value": int(match.group(2))}
    if " lowerbound" in line:
        result["bound"] = "lower"
    elif " upperbound" in line:
        result["bound"] = "upper"
    return result


def _parse_pv(line: str) -> list[str] | None:
    marker = " pv "
    if marker not in line:
        return None
    tail = line.split(marker, 1)[1].split()
    pv: list[str] = []
    for token in tail:
        token = token.lower()
        if not is_uci_move(token):
            break
        pv.append(token)
    return pv or None


def normalize_search(lines: Iterable[str]) -> dict[str, Any]:
    material = list(lines)
    best_line = next((line for line in reversed(material) if line.startswith("bestmove ")), None)
    if best_line is None:
        raise ValueError("search completed without bestmove")

    tokens = best_line.split()
    if len(tokens) < 2:
        raise ValueError(f"malformed bestmove line: {best_line}")
    result: dict[str, Any] = {"bestmove": normalize_bestmove(tokens[1]), "ponder": None}

    if "ponder" in tokens:
        idx = tokens.index("ponder")
        if idx + 1 < len(tokens):
            result["ponder"] = normalize_bestmove(tokens[idx + 1])

    useful: dict[str, Any] | None = None
    for line in material:
        if not line.startswith("info "):
            continue
        candidate: dict[str, Any] = {}
        for key in ("depth", "seldepth", "nodes", "multipv"):
            value = _last_int(line, key)
            if value is not None:
                candidate[key] = value
        score = _parse_score(line)
        if score is not None:
            candidate["score"] = score
        pv = _parse_pv(line)
        if pv is not None:
            candidate["pv"] = pv
        if "score" in candidate or "pv" in candidate:
            useful = candidate

    if useful:
        result.update(useful)
    return result
