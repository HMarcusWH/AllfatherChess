#!/usr/bin/env python3
"""Deterministic fake UCI engine for process/controller tests."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="FakeEngine")
    parser.add_argument("--exit-on", choices=["uci", "isready", "go"])
    args = parser.parse_args()

    write_lock = threading.Lock()
    stop_event = threading.Event()
    search_lock = threading.Lock()
    search_thread: threading.Thread | None = None

    def emit(line: str) -> None:
        with write_lock:
            print(line, flush=True)

    def maybe_exit(command: str) -> None:
        if args.exit_on == command:
            os._exit(7)

    def finish_search(*, infinite: bool) -> None:
        depth = 1
        if infinite:
            while not stop_event.wait(0.02):
                emit(f"info depth {depth} nodes {depth * 16} score cp 1 pv e2e4")
                depth += 1
        else:
            emit("info depth 1 nodes 64 score cp 1 pv e2e4")
        emit("bestmove e2e4")

    def start_search(command: str) -> None:
        nonlocal search_thread
        with search_lock:
            if search_thread is not None and search_thread.is_alive():
                emit("info string fake duplicate go")
                return
            stop_event.clear()
            infinite = " infinite" in f" {command}" or " ponder" in f" {command}"
            search_thread = threading.Thread(
                target=finish_search,
                kwargs={"infinite": infinite},
                daemon=True,
            )
            search_thread.start()

    for raw in sys.stdin:
        command = raw.strip()
        if not command:
            continue
        if command == "uci":
            maybe_exit("uci")
            emit(f"id name {args.name}")
            emit("id author Fake")
            emit("option name UCI_Chess960 type check default false")
            emit("uciok")
        elif command == "isready":
            maybe_exit("isready")
            emit("readyok")
        elif command.startswith("setoption "):
            pass
        elif command == "ucinewgame":
            pass
        elif command.startswith("position "):
            pass
        elif command == "go perft 1":
            maybe_exit("go")
            emit("e2e4: 1")
            emit("d2d4: 1")
            emit("g1f3: 1")
            emit("Nodes searched: 3")
        elif command == "go" or command.startswith("go "):
            maybe_exit("go")
            start_search(command)
        elif command == "stop":
            stop_event.set()
        elif command == "ponderhit":
            stop_event.set()
        elif command == "quit":
            stop_event.set()
            break

    stop_event.set()
    thread = search_thread
    if thread is not None:
        thread.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
