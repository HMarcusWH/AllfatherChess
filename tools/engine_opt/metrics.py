from __future__ import annotations
import re
from dataclasses import dataclass, asdict

_INFO_NODES = re.compile(r"(?:^| )nodes (\d+)(?: |$)")
_INFO_NPS = re.compile(r"(?:^| )nps (\d+)(?: |$)")
_BESTMOVE = re.compile(r"^bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000|\(none\))")


@dataclass(frozen=True)
class SearchMetrics:
    bestmove: str | None
    wall_ms: float
    native_work_value: int | None
    native_work_semantics: str
    nps: int | None
    completed_before_deadline: bool

    def as_dict(self) -> dict:
        return asdict(self)


def parse_search(lines: list[str], *, family: str, wall_ms: float, deadline_ms: float) -> SearchMetrics:
    nodes = None
    nps = None
    bestmove = None
    for line in lines:
        m = _INFO_NODES.search(line)
        if m:
            nodes = int(m.group(1))
        m = _INFO_NPS.search(line)
        if m:
            nps = int(m.group(1))
        m = _BESTMOVE.match(line)
        if m:
            value = m.group(1).lower()
            bestmove = None if value in {"0000", "(none)"} else value
    semantics = {
        "stockfish":"stockfish.uci_nodes",
        "reckless":"reckless.uci_nodes",
        "lc0":"lc0.uci_nodes",
    }.get(family, f"{family}.uci_nodes")
    return SearchMetrics(
        bestmove=bestmove,
        wall_ms=round(float(wall_ms), 3),
        native_work_value=nodes,
        native_work_semantics=semantics,
        nps=nps,
        completed_before_deadline=wall_ms <= deadline_ms,
    )
