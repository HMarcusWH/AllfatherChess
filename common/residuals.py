"""Scale-free residual primitives for chess-native disagreement geometry.

Every function here is deliberately **structural**: it compares move identity,
set membership, rank order, or PV prefixes. None of them combines two engines'
numeric evaluations, because Stockfish centipawns, Reckless centipawns, and a
ScoreType-qualified LC0 score are not calibrated onto a common latent scale.

Attempting such a combination raises `ScaleMixingError`. That is the point: the
firewall is enforced by code, not by reviewer memory.

These are derived quantities. They must never be written into raw telemetry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


class ResidualError(RuntimeError):
    """Raised when a residual feature cannot be computed honestly."""


class ScaleMixingError(ResidualError):
    """Raised when two differently-tagged engine values would be combined."""


@dataclass(frozen=True)
class TaggedValue:
    """One engine value that keeps its semantic provenance attached."""

    value: float
    semantics: str
    kind: str = "cp"
    bound: str = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.semantics, str) or not self.semantics:
            raise ResidualError("tagged value requires non-empty semantics")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ResidualError("tagged value must be numeric")
        if not math.isfinite(float(self.value)):
            raise ResidualError("tagged value must be finite")


def require_same_semantics(left: TaggedValue, right: TaggedValue) -> str:
    """Return the shared semantics tag, or refuse the comparison."""
    if left.semantics != right.semantics:
        raise ScaleMixingError(
            "refusing to combine engine values with different semantics: "
            f"{left.semantics!r} and {right.semantics!r}; "
            "cross-engine comparison requires an explicit fitted calibration"
        )
    if left.kind != right.kind:
        raise ScaleMixingError(
            f"refusing to combine evaluation kinds {left.kind!r} and {right.kind!r}"
        )
    return left.semantics


@dataclass(frozen=True)
class Margin:
    """A within-engine separation between the primary line and its runner-up.

    The value is normalized so it is comparable *across positions for the same
    engine*. It is not comparable across engines and carries its semantics tag
    so that a consumer cannot forget.
    """

    value: float
    semantics: str

    @property
    def is_decisive(self) -> bool:
        return self.value >= 1.0


def within_engine_margin(primary: TaggedValue, runner_up: TaggedValue) -> Margin:
    """Normalized primary-vs-runner-up separation inside one engine's own scale.

    `(v1 - v2) / (|v1| + |v2| + 1)` keeps the sign, is bounded, and degrades
    gracefully near zero. The `+1` denominator term keeps small-magnitude
    differences from saturating the ratio.
    """
    semantics = require_same_semantics(primary, runner_up)
    numerator = float(primary.value) - float(runner_up.value)
    denominator = abs(float(primary.value)) + abs(float(runner_up.value)) + 1.0
    return Margin(value=numerator / denominator, semantics=semantics)


def _clean_sequence(moves: Iterable[str], *, label: str) -> tuple[str, ...]:
    result: list[str] = []
    for move in moves:
        if not isinstance(move, str) or not move:
            raise ResidualError(f"{label} must contain non-empty move strings")
        result.append(move)
    return tuple(result)


def leader_agreement(left: str | None, right: str | None) -> bool | None:
    """`None` when either side has not produced a leader yet."""
    if left is None or right is None:
        return None
    return left == right


def jaccard(left: Iterable[str], right: Iterable[str]) -> float | None:
    """Set overlap; `None` when both sides are empty (undefined, not zero)."""
    a, b = set(left), set(right)
    if not a and not b:
        return None
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def top_k_overlap(left: Sequence[str], right: Sequence[str], k: int) -> float | None:
    """Fraction of the smaller top-k prefix that both engines share."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ResidualError("top_k_overlap requires a positive integer k")
    a = _clean_sequence(left, label="left")[:k]
    b = _clean_sequence(right, label="right")[:k]
    if not a or not b:
        return None
    denominator = min(len(a), len(b))
    return len(set(a) & set(b)) / denominator


def rank_agreement(left: Sequence[str], right: Sequence[str]) -> float | None:
    """Kendall tau-b over the moves both engines ranked.

    Computed on the common support only. Fewer than two shared moves leaves the
    statistic undefined, which is reported as `None` rather than as agreement.
    """
    a = _clean_sequence(left, label="left")
    b = _clean_sequence(right, label="right")
    shared = [move for move in a if move in set(b)]
    if len(shared) < 2:
        return None
    rank_a = {move: index for index, move in enumerate(a)}
    rank_b = {move: index for index, move in enumerate(b)}

    concordant = 0
    discordant = 0
    for i in range(len(shared)):
        for j in range(i + 1, len(shared)):
            first, second = shared[i], shared[j]
            da = rank_a[first] - rank_a[second]
            db = rank_b[first] - rank_b[second]
            product = da * db
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total


def common_prefix_length(left: Sequence[str], right: Sequence[str]) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def pv_divergence(left: Sequence[str], right: Sequence[str]) -> float | None:
    """`1 - shared_prefix / longest_pv`. Zero means the PVs are identical."""
    a = _clean_sequence(left, label="left")
    b = _clean_sequence(right, label="right")
    if not a or not b:
        return None
    longest = max(len(a), len(b))
    return 1.0 - (common_prefix_length(a, b) / longest)


def leader_flip_count(leaders: Sequence[str | None]) -> int:
    """Number of times the observed leader changed across a trajectory."""
    flips = 0
    previous: str | None = None
    for leader in leaders:
        if leader is None:
            continue
        if previous is not None and leader != previous:
            flips += 1
        previous = leader
    return flips


def stabilization_index(leaders: Sequence[str | None]) -> int | None:
    """Earliest index from which the leader never changes again.

    `None` when no leader was ever observed. Index 0 means the first observed
    leader survived to the end.
    """
    observed = [(index, leader) for index, leader in enumerate(leaders) if leader is not None]
    if not observed:
        return None
    final = observed[-1][1]
    for index, leader in observed:
        if leader == final and all(later == final for _, later in observed if _ >= index):
            return index
    return observed[-1][0]


def stability_fraction(leaders: Sequence[str | None]) -> float | None:
    """Fraction of the trajectory already spent when the leader last changed."""
    index = stabilization_index(leaders)
    if index is None or len(leaders) <= 1:
        return None
    return index / (len(leaders) - 1)


def pv_persistence(pvs: Sequence[Sequence[str]]) -> float | None:
    """Mean shared-prefix ratio between consecutive principal variations."""
    usable = [tuple(pv) for pv in pvs if pv]
    if len(usable) < 2:
        return None
    ratios: list[float] = []
    for previous, current in zip(usable, usable[1:]):
        longest = max(len(previous), len(current))
        ratios.append(common_prefix_length(previous, current) / longest)
    return sum(ratios) / len(ratios)


def unresolved_set(
    ranked_moves: Sequence[str],
    margins: Sequence[Margin],
    *,
    decisive_margin: float,
) -> tuple[str, ...]:
    """Moves whose separation from the leader is not yet decisive.

    This is the chess reading of "effective unresolved decision dimension": the
    candidates that current evidence has not separated. It is computed strictly
    inside one engine's own evaluation scale.

    It is an observation, not a bound. It does not prove that an excluded move
    cannot become best under more compute.
    """
    if decisive_margin <= 0:
        raise ResidualError("decisive_margin must be positive")
    moves = _clean_sequence(ranked_moves, label="ranked_moves")
    if not moves:
        return ()
    if len(margins) != len(moves) - 1:
        raise ResidualError(
            "unresolved_set expects one margin per non-leader candidate: "
            f"{len(moves)} moves, {len(margins)} margins"
        )
    semantics = {margin.semantics for margin in margins}
    if len(semantics) > 1:
        raise ScaleMixingError(f"unresolved_set received mixed semantics: {sorted(semantics)}")
    unresolved = [moves[0]]
    for move, margin in zip(moves[1:], margins):
        if abs(margin.value) < decisive_margin:
            unresolved.append(move)
    return tuple(unresolved)
