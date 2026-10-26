"""Minimal single-process fake native SF for schema-v3 end-to-end smoke.

This is protocol evidence only, never actual chess strength.
"""
from __future__ import annotations
import sys
import threading
import time


def emit(x: str) -> None:
    print(x,flush=True)


def main()->int:
    for raw in sys.stdin:
        cmd=raw.strip()
        if cmd=="uci":
            emit("id name Native-S2-TEST-ONLY")
            emit("id author test")
            emit("option name Threads type spin default 1 min 1 max 8")
            emit("option name Hash type spin default 16 min 1 max 1024")
            emit("option name MultiPV type spin default 1 min 1 max 8")
            emit("option name Move Overhead type spin default 100 min 0 max 1000")
            emit("option name Ponder type check default false")
            emit("option name UCI_Chess960 type check default false")
            emit("uciok")
        elif cmd=="isready":
            emit("readyok")
        elif cmd=="ucinewgame" or cmd.startswith("position ") or cmd.startswith("setoption "):
            pass
        elif cmd.startswith("go "):
            emit("info string native-go-received " + cmd)
            # Any native clock text is accepted by the fake, but the supervisor
            # must record and forward it *without* turning it into movetime.
            emit("info depth 1 nodes 16 multipv 1 score cp 15 pv e2e4")
            emit("info depth 2 nodes 32 multipv 1 score cp 19 pv d2d4")
            emit("info depth 3 nodes 64 multipv 1 score cp 21 pv e2e4")
            emit("bestmove e2e4")
        elif cmd=="stop":
            pass
        elif cmd=="quit":
            break
    return 0


if __name__=="__main__":
    raise SystemExit(main())
