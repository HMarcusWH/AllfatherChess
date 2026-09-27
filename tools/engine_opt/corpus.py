from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PositionCase:
    case_id: str
    fen: str
    tags: tuple[str, ...] = ()


def load_epd(path: Path | str) -> tuple[PositionCase, ...]:
    rows: list[PositionCase] = []
    seen: set[str] = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise ValueError(f"malformed engine-opt corpus line: {raw!r}")
        case_id, fen = parts[:2]
        tags = tuple(tag for tag in (parts[2].split(",") if len(parts) > 2 else []) if tag)
        if not case_id or case_id in seen:
            raise ValueError(f"duplicate/empty engine-opt case id: {case_id!r}")
        if len(fen.split()) != 6:
            raise ValueError(f"{case_id}: FEN must contain six fields")
        seen.add(case_id)
        rows.append(PositionCase(case_id=case_id, fen=fen, tags=tags))
    if not rows:
        raise ValueError("engine-opt corpus is empty")
    return tuple(rows)
