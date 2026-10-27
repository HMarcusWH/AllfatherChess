"""Raw UCI incumbent/PV parsing; no averaged cross-engine scores.

A comparable MultiPV frame requires both exact, unbounded scores at the
same completed depth, fresh roots, and one source engine perspective.
S2 records frames only; no comparator process is started.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .contracts import StrengthContractError, require, MOVE

INT = re.compile(r"[+-]?[0-9]+\Z")


@dataclass(frozen=True)
class UciFrame:
    move: str
    depth: int
    multipv: int
    nodes: int
    cp: int | None
    mate: int | None
    exact: bool
    bound: str | None
    raw: str


def parse_info(line: str) -> UciFrame | None:
    """Return one eligible PV report; unrelated UCI strings are not evidence."""
    if not isinstance(line, str) or len(line) > 4096 or not line.startswith("info "):
        return None
    t = line.split()
    if "string" in t[1:3] or "pv" not in t:
        return None
    try:
        pv_pos = t.index("pv")
        root = t[pv_pos + 1]
        require(MOVE.fullmatch(root) is not None, "malformed UCI PV root")
        def int_after(key: str, default: int | None = None) -> int | None:
            if key not in t[:pv_pos]:
                return default
            n = t.index(key)
            require(n+1 < pv_pos and INT.fullmatch(t[n+1]) is not None,
                    "malformed numeric info field")
            return int(t[n+1])
        depth = int_after("depth")
        if depth is None or depth < 1:
            return None
        mpv = int_after("multipv", 1)
        nodes = int_after("nodes", 0)
        require(mpv is not None and 1 <= mpv <= 16, "unsupported MultiPV rank")
        require(nodes is not None and 0 <= nodes <= 2**63 - 1, "bad native node count")
        cp = mate = None
        exact = False
        bound = "lowerbound" if "lowerbound" in t[:pv_pos] else ("upperbound" if "upperbound" in t[:pv_pos] else None)
        require(not ("lowerbound" in t[:pv_pos] and "upperbound" in t[:pv_pos]),
                "contradictory score bound flags")
        if "score" in t[:pv_pos]:
            s = t.index("score")
            require(s + 2 < pv_pos and t[s+1] in ("cp", "mate")
                    and INT.fullmatch(t[s+2]) is not None, "malformed UCI score")
            val = int(t[s+2])
            require(abs(val) < 1000000, "unbounded UCI score")
            if t[s+1] == "cp":
                cp = val if bound is None else None
            else:
                mate = val if bound is None else None
            exact = bound is None
        return UciFrame(root,depth,mpv,nodes,cp,mate,exact,bound,line)
    except (IndexError, ValueError, StrengthContractError):
        return None


class PairFrames:
    """Select the *latest complete* same-depth exact rank-1/rank-2 frame.

    Intended for S3 experiments. Not a proof, and never grants S2 authority.
    """
    def __init__(self) -> None:
        self._by_depth: dict[int, dict[int, UciFrame]] = {}

    def observe(self, line: str) -> tuple[UciFrame, UciFrame] | None:
        f = parse_info(line)
        if f is None or f.multipv not in (1,2):
            return None
        row = self._by_depth.setdefault(f.depth,{})
        # A later bounded/inexact update invalidates that rank at this depth.
        if not f.exact:
            row.pop(f.multipv, None)
        else:
            row[f.multipv] = f
        # Bound memory even for a nonterminating anomalous engine.
        if len(self._by_depth) > 128:
            for depth in sorted(self._by_depth)[:-64]:
                del self._by_depth[depth]
        one,two = row.get(1),row.get(2)
        if one is None or two is None or one.move == two.move:
            return None
        if not ((one.cp is not None and two.cp is not None)
                or (one.mate is not None and two.mate is not None)):
            return None
        return one,two
