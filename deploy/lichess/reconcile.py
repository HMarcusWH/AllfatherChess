"""Pure authoritative-history reconciliation for ONLINE-4B.

A missing pending move in a snapshot NEVER establishes that the old HTTP
request cannot still commit. The only safe normal transitions are prefix
extension and positive matching server confirmation.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
import chess

STARTPOS = chess.STARTING_FEN


class ReconciliationError(ValueError):
    pass


def canonical_history(moves: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(moves, str):
        tokens = moves.split()
    elif isinstance(moves, (list, tuple)):
        tokens = list(moves)
    else:
        raise ReconciliationError("history must be a full UCI list")
    board = chess.Board()
    result = []
    for item in tokens:
        if not isinstance(item, str):
            raise ReconciliationError("non-string UCI history entry")
        try:
            move = chess.Move.from_uci(item)
        except ValueError as exc:
            raise ReconciliationError("malformed UCI history") from exc
        if move == chess.Move.null() or move not in board.legal_moves:
            raise ReconciliationError("illegal authoritative history entry")
        result.append(move.uci())
        board.push(move)
    return tuple(result)


def position_id(moves: str | list[str] | tuple[str, ...]) -> str:
    history = canonical_history(moves)
    payload = "standard|" + STARTPOS + "|" + " ".join(history)
    return "pos-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def is_prefix(prior: tuple[str, ...], newer: tuple[str, ...]) -> bool:
    return newer[:len(prior)] == prior


@dataclass(frozen=True)
class Reconciliation:
    outcome: str
    history: tuple[str, ...]


def classify_snapshot(*, previous_history: tuple[str, ...],
                      incoming_history: tuple[str, ...],
                      intent_history: tuple[str, ...] | None = None,
                      intent_move: str | None = None) -> Reconciliation:
    if not is_prefix(previous_history, incoming_history):
        raise ReconciliationError("server history regressed or conflicted")
    if intent_history is None:
        return Reconciliation("NO_PENDING_INTENT", incoming_history)
    if not is_prefix(intent_history, incoming_history):
        raise ReconciliationError("pending move belongs to another position")
    if intent_move is None:
        raise ReconciliationError("pending intent has no move")
    ply = len(intent_history)
    if len(incoming_history) <= ply:
        return Reconciliation("UNKNOWN_ACK", incoming_history)
    if incoming_history[ply] != intent_move:
        raise ReconciliationError("server accepted a conflicting move at pending ply")
    return Reconciliation("SERVER_CONFIRMED", incoming_history)
