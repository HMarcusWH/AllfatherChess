"""Deliberately weak but deterministic, standard-chess-only offline opponent."""
from __future__ import annotations
import chess

OPENINGS = {
    chess.WHITE: ("f2f3", "g2g4", "a2a3", "h2h3"),
    chess.BLACK: ("f7f6", "g7g5", "a7a6", "h7h6"),
}

def choose(board: chess.Board) -> chess.Move:
    """Follow a reckless pawn script, then choose a stable legal move."""
    if board.is_game_over(claim_draw=False):
        raise ValueError("no opponent move after game termination")
    moves = list(board.legal_moves)
    own_plies = sum(
        1 for i in range(len(board.move_stack))
        if (i % 2 == 0) == (board.turn == chess.WHITE)
    )
    # This opponent is always deliberately cooperative but does not control the engine.
    if own_plies < len(OPENINGS[board.turn]):
        pick = chess.Move.from_uci(OPENINGS[board.turn][own_plies])
        if pick in moves:
            return pick
    # Prefer advancing flank pawns / risky piece moves; stable across repeated runs.
    def score(m: chess.Move) -> tuple[int, str]:
        piece = board.piece_at(m.from_square)
        kind = piece.piece_type if piece is not None else 0
        file = chess.square_file(m.from_square)
        risky = kind == chess.PAWN and file in (0, 1, 6, 7)
        return (0 if risky else 1, m.uci())
    return sorted(moves, key=score)[0]
