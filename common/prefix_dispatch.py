"""Compile one recursive move-prefix shard into ordinary UCI requests.

The compiler is syntactic only. It does not decide whether the prefix is legal
chess; the caller must obtain exact children from a qualified move oracle.

For prefix (m1, ..., mn):

    position = external_position + (m1, ..., m[n-1])
    go       = declared limit + searchmoves mn

Depth-1 prefixes therefore reduce exactly to the existing root-v1 restriction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from common.search_request import PositionRequest, SearchRequestError, build_go_command


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


@dataclass(frozen=True)
class PrefixDispatch:
    prefix: tuple[str, ...]
    position: PositionRequest
    searchmoves: tuple[str, ...]
    go_command: str

    @property
    def position_command(self) -> str:
        return self.position.command()


def compile_prefix_dispatch(
    base_position: PositionRequest,
    prefix: Sequence[str],
    *,
    limit: dict[str, Any],
) -> PrefixDispatch:
    """Compile a non-empty canonical prefix into descendant position + root.

    Existing game history in base_position.moves is preserved and extended; it
    is never replaced by the shard prefix.
    """

    if isinstance(prefix, (str, bytes)) or not isinstance(prefix, Sequence):
        raise SearchRequestError("prefix must be a move sequence")
    if not prefix:
        raise SearchRequestError("prefix must be non-empty")

    moves: list[str] = []
    for index, raw in enumerate(prefix):
        if not isinstance(raw, str) or not _MOVE_RE.fullmatch(raw):
            raise SearchRequestError(
                f"prefix[{index}] must be canonical lowercase UCI: {raw!r}"
            )
        moves.append(raw)

    compiled_position = PositionRequest(
        base_fen=base_position.base_fen,
        moves=tuple(base_position.moves) + tuple(moves[:-1]),
        variant=base_position.variant,
    )
    searchmoves = (moves[-1],)
    go_command = build_go_command(limit=dict(limit), searchmoves=searchmoves)
    return PrefixDispatch(
        prefix=tuple(moves),
        position=compiled_position,
        searchmoves=searchmoves,
        go_command=go_command,
    )
