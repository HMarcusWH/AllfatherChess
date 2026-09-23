"""Pure nomination policy for bounded recursive REFINE.

M14-D keeps recursive branch nomination separate from orchestration, ownership
and resource authority.  A nomination means only that a clean restricted stage
ended on one child that may be worth another zoom.  It is not a correctness,
score-fusion or outward-decision statement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


RECURSIVE_NOMINATION_POLICY = "stage_terminal_bestmove_v1"
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class RefinementPolicyError(RuntimeError):
    """Raised when recursive nomination evidence is malformed."""


@dataclass(frozen=True)
class RefinementStageEvidence:
    expansion_id: str
    parent_prefix: tuple[str, ...]
    owner: str
    search_id: str
    child_moves: tuple[str, ...]
    bestmove: str | None
    disposition: str
    evidence_lossy: bool = False
    evidence_truncated: bool = False

    def __post_init__(self) -> None:
        if not self.expansion_id:
            raise RefinementPolicyError("expansion_id must be non-empty")
        if not self.owner:
            raise RefinementPolicyError("owner must be non-empty")
        if not self.search_id:
            raise RefinementPolicyError("search_id must be non-empty")
        if any(not isinstance(move, str) or not _MOVE_RE.fullmatch(move) for move in self.parent_prefix):
            raise RefinementPolicyError("parent_prefix contains non-canonical UCI move")
        if not self.child_moves:
            raise RefinementPolicyError("child_moves must be non-empty")
        if len(set(self.child_moves)) != len(self.child_moves):
            raise RefinementPolicyError("child_moves must be unique")
        if any(not isinstance(move, str) or not _MOVE_RE.fullmatch(move) for move in self.child_moves):
            raise RefinementPolicyError("child_moves contains non-canonical UCI move")
        if self.bestmove is not None and (
            not isinstance(self.bestmove, str) or not _MOVE_RE.fullmatch(self.bestmove)
        ):
            raise RefinementPolicyError("bestmove is not canonical UCI")


@dataclass(frozen=True)
class RefinementNomination:
    expansion_id: str
    prefix: tuple[str, ...]
    owner: str
    source_search_id: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "expansion_id": self.expansion_id,
            "prefix": list(self.prefix),
            "owner": self.owner,
            "source_search_id": self.source_search_id,
            "reason": self.reason,
        }


def evaluate_recursive_nominations(
    evidence: Iterable[RefinementStageEvidence],
    *,
    owner_order: tuple[str, ...],
    policy: str = RECURSIVE_NOMINATION_POLICY,
) -> tuple[RefinementNomination, ...]:
    """Return deterministic deeper-zoom nominations from clean stage terminals."""

    if policy != RECURSIVE_NOMINATION_POLICY:
        raise RefinementPolicyError(f"unsupported recursive refinement policy: {policy!r}")
    order = {owner: index for index, owner in enumerate(owner_order)}
    rows = sorted(
        tuple(evidence),
        key=lambda row: (
            row.parent_prefix,
            order.get(row.owner, len(order)),
            row.owner,
            row.search_id,
        ),
    )

    nominations: list[RefinementNomination] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        if row.disposition != "completed":
            continue
        if row.evidence_lossy or row.evidence_truncated:
            continue
        if row.bestmove is None or row.bestmove not in row.child_moves:
            continue
        prefix = row.parent_prefix + (row.bestmove,)
        if prefix in seen:
            continue
        seen.add(prefix)
        nominations.append(
            RefinementNomination(
                expansion_id=row.expansion_id,
                prefix=prefix,
                owner=row.owner,
                source_search_id=row.search_id,
                reason=(
                    "clean restricted REFINE stage ended on this owned child; "
                    "nomination is regional evidence only"
                ),
            )
        )
    return tuple(nominations)
