"""Deterministic reconstruction of UCI position/go requests for replay evidence.

These helpers are syntactic only. They assign no chess meaning to a position and
no cross-engine meaning to a limit; they exist so a replay manifest and a
telemetry `search.started` event describe the same request without inventing
controller state.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from common.telemetry import STARTPOS_FEN, TelemetryError


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

#: `go` tokens that take a non-negative numeric argument.
_NUMERIC_LIMITS = (
    "wtime",
    "btime",
    "winc",
    "binc",
    "movestogo",
    "depth",
    "nodes",
    "mate",
    "movetime",
)

#: `go` tokens that are boolean flags.
_FLAG_LIMITS = ("infinite", "ponder")


class SearchRequestError(TelemetryError):
    """Raised when a position or go command cannot be reconstructed."""


@dataclass(frozen=True)
class PositionRequest:
    base_fen: str
    moves: tuple[str, ...]
    variant: str

    @property
    def position_id(self) -> str:
        payload = f"{self.variant}|{self.base_fen}|{' '.join(self.moves)}"
        return "pos-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @property
    def side_to_move(self) -> str:
        """Side after replaying the supplied history, without discarding FEN side.

        This is turn accounting, not a chess-legality oracle. Illegal game
        histories remain the backend/rules layer's responsibility.
        """
        fields = self.base_fen.split()
        if len(fields) != 6 or fields[1] not in ("w", "b"):
            raise SearchRequestError("clock position requires a six-field FEN with w/b side")
        side = fields[1]
        return side if len(self.moves) % 2 == 0 else ("b" if side == "w" else "w")

    def telemetry_position(self) -> dict[str, Any]:
        return {"base_fen": self.base_fen, "moves": list(self.moves)}

    def command(self) -> str:
        if self.base_fen == STARTPOS_FEN:
            command = "position startpos"
        else:
            command = f"position fen {self.base_fen}"
        if self.moves:
            command += " moves " + " ".join(self.moves)
        return command


def parse_position_command(command: str, *, variant: str = "standard") -> PositionRequest:
    """Reconstruct a `position` command into an explicit base FEN plus moves."""
    if not isinstance(command, str) or not command.startswith("position "):
        raise SearchRequestError(f"not a position command: {command!r}")
    if variant not in ("standard", "chess960"):
        raise SearchRequestError(f"unsupported variant: {variant!r}")

    rest = command[len("position ") :].strip()
    if rest == "startpos" or rest.startswith("startpos "):
        base_fen = STARTPOS_FEN
        rest = rest[len("startpos") :].strip()
    elif rest.startswith("fen "):
        rest = rest[len("fen ") :].strip()
        head, separator, tail = rest.partition(" moves")
        base_fen = head.strip()
        rest = ("moves" + tail).strip() if separator else ""
        if not base_fen:
            raise SearchRequestError("position fen command is missing a FEN")
    else:
        raise SearchRequestError(f"unsupported position command: {command!r}")

    moves: tuple[str, ...] = ()
    if rest:
        if rest != "moves" and not rest.startswith("moves "):
            raise SearchRequestError(f"unexpected trailing position material: {rest!r}")
        tokens = rest.split()[1:]
        for token in tokens:
            lowered = token.lower()
            if not _MOVE_RE.fullmatch(lowered):
                raise SearchRequestError(f"invalid move token in position command: {token!r}")
            moves += (lowered,)

    return PositionRequest(base_fen=base_fen, moves=moves, variant=variant)


def parse_go_request(command: str) -> dict[str, Any]:
    """Reconstruct a `go` command into a telemetry v1 `request` object.

    Unrecognized tokens are preserved verbatim under `unknown_tokens` so replay
    never silently discards part of the external request.
    """
    if not isinstance(command, str) or (command != "go" and not command.startswith("go ")):
        raise SearchRequestError(f"not a go command: {command!r}")

    tokens = command.split()[1:]
    limits: list[dict[str, Any]] = []
    root_moves: list[str] = []
    unknown: list[str] = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _NUMERIC_LIMITS:
            if index + 1 >= len(tokens):
                unknown.append(token)
                index += 1
                continue
            try:
                value = int(tokens[index + 1])
            except ValueError:
                unknown.extend(tokens[index : index + 2])
                index += 2
                continue
            if value < 0:
                unknown.extend(tokens[index : index + 2])
                index += 2
                continue
            limits.append({"name": token, "value": value, "semantics": f"uci.go.{token}"})
            index += 2
            continue
        if token in _FLAG_LIMITS:
            limits.append({"name": token, "value": True, "semantics": f"uci.go.{token}"})
            index += 1
            continue
        if token == "searchmoves":
            index += 1
            while index < len(tokens) and _MOVE_RE.fullmatch(tokens[index].lower()):
                root_moves.append(tokens[index].lower())
                index += 1
            continue
        unknown.append(token)
        index += 1

    request: dict[str, Any] = {"limits": limits, "raw": command}
    if root_moves:
        request["root_moves"] = root_moves
    if unknown:
        request["unknown_tokens"] = unknown
    return request


def build_go_command(*, limit: dict[str, Any], searchmoves: tuple[str, ...] | list[str] | None) -> str:
    """Build a restricted `go` command with one canonical token order.

    All vendored backends accept ordinary UCI option ordering; keeping
    `searchmoves` last remains a deterministic serialization choice rather
    than a hidden backend-parser requirement.
    """
    parts = ["go"]
    for name in sorted(limit):
        value = limit[name]
        if name in _FLAG_LIMITS:
            if value:
                parts.append(name)
            continue
        if name not in _NUMERIC_LIMITS:
            raise SearchRequestError(f"unsupported dispatch limit: {name!r}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SearchRequestError(f"dispatch limit {name!r} must be a positive integer")
        parts.extend([name, str(value)])
    if searchmoves is not None:
        moves = list(searchmoves)
        if not moves:
            raise SearchRequestError("refusing to dispatch an empty searchmoves region")
        seen: set[str] = set()
        for move in moves:
            lowered = str(move).lower()
            if not _MOVE_RE.fullmatch(lowered):
                raise SearchRequestError(f"invalid searchmoves entry: {move!r}")
            if lowered in seen:
                raise SearchRequestError(f"duplicate searchmoves entry: {move!r}")
            seen.add(lowered)
        parts.append("searchmoves")
        parts.extend(str(move).lower() for move in moves)
    if len(parts) == 1:
        raise SearchRequestError("refusing to dispatch an unlimited shadow search")
    return " ".join(parts)
