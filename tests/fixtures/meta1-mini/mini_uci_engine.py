#!/usr/bin/env python3
"""Tiny legal UCI engine used only by the META-1 Fastchess smoke test."""

from __future__ import annotations

import sys

import chess


def board_from_position(command: str) -> chess.Board:
    tokens = command.split()
    if tokens[:2] == ["position", "startpos"]:
        board = chess.Board()
        if "moves" in tokens:
            index = tokens.index("moves") + 1
            for token in tokens[index:]:
                board.push_uci(token)
        return board
    if len(tokens) >= 3 and tokens[:2] == ["position", "fen"]:
        if "moves" in tokens:
            index = tokens.index("moves")
            fen = " ".join(tokens[2:index])
            moves = tokens[index + 1 :]
        else:
            fen = " ".join(tokens[2:])
            moves = []
        board = chess.Board(fen)
        for token in moves:
            board.push_uci(token)
        return board
    raise RuntimeError(f"unsupported position command: {command!r}")


def choose(board: chess.Board) -> chess.Move:
    legal = sorted(board.legal_moves, key=lambda move: move.uci())
    if not legal:
        raise RuntimeError("search requested in terminal position")
    for move in legal:
        probe = board.copy(stack=False)
        probe.push(move)
        if probe.is_checkmate():
            return move
    return legal[0]


def main() -> int:
    board = chess.Board()
    for raw in sys.stdin:
        command = raw.strip()
        if not command:
            continue
        if command == "uci":
            print("id name META1Mini", flush=True)
            print("id author AllfatherChess", flush=True)
            print("uciok", flush=True)
        elif command == "isready":
            print("readyok", flush=True)
        elif command == "ucinewgame":
            board = chess.Board()
        elif command.startswith("position "):
            board = board_from_position(command)
        elif command.startswith("go"):
            move = choose(board)
            print(f"info depth 1 score cp 0 pv {move.uci()}", flush=True)
            print(f"bestmove {move.uci()}", flush=True)
        elif command in ("stop", "ponderhit"):
            pass
        elif command == "quit":
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
