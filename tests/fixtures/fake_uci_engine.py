#!/usr/bin/env python3
"""Deterministic fake UCI engine for process/controller tests.

The fake is deliberately dumb about chess and precise about protocol. It honors
`searchmoves` restriction, node limits, and a scripted leader-reversal schedule
so shadow, replay, and routing behavior can be exercised without real engines.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time


DEFAULT_ROOTS = ("e2e4", "d2d4", "g1f3")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="FakeEngine")
    parser.add_argument("--exit-on", choices=["uci", "isready", "go"])
    parser.add_argument(
        "--exit-on-go-number",
        type=int,
        default=0,
        help="exit abruptly on the Nth search (1-based); 0 disables",
    )
    parser.add_argument(
        "--roots",
        default=",".join(DEFAULT_ROOTS),
        help="comma-separated legal roots reported by 'go perft 1'",
    )
    parser.add_argument(
        "--perft-delay-ms",
        type=int,
        default=0,
        help="stall this long inside 'go perft 1' so a test can mutate state mid-qualification",
    )
    parser.add_argument(
        "--leader-schedule",
        default="",
        help="comma-separated moves; the emitted leader advances one entry per info line",
    )
    parser.add_argument("--score-cp", type=int, default=1)
    parser.add_argument("--info-lines", type=int, default=1, help="info lines for a finite search")
    parser.add_argument("--info-delay-ms", type=int, default=20)
    parser.add_argument("--multipv", type=int, default=0, help="emit N MultiPV lines per iteration")
    parser.add_argument("--tag", default="", help="opaque marker so a test can identify its own processes")
    parser.add_argument(
        "--ignore-stop",
        action="store_true",
        help="do not honor `stop`, simulating a worker that misses the drain deadline",
    )
    parser.add_argument(
        "--omit-multipv-token",
        action="store_true",
        help="emit the primary PV without a multipv token, the way LC0 does at MultiPV=1",
    )
    args = parser.parse_args()

    roots = tuple(move for move in args.roots.split(",") if move)
    schedule = tuple(move for move in args.leader_schedule.split(",") if move)

    write_lock = threading.Lock()
    stop_event = threading.Event()
    search_lock = threading.Lock()
    search_thread: threading.Thread | None = None
    go_count = 0

    def emit(line: str) -> None:
        with write_lock:
            print(line, flush=True)

    def maybe_exit(command: str) -> None:
        if args.exit_on == command:
            os._exit(7)

    def parse_command(command: str) -> tuple[list[str] | None, int | None]:
        """Return (authorized roots, node limit) for one go command.

        `None` means no `searchmoves` token was present (unrestricted). An empty
        list means an explicitly empty restriction, which must produce no move.
        """
        tokens = command.split()
        allowed: list[str] | None = None
        nodes: int | None = None
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "searchmoves":
                allowed = [item.lower() for item in tokens[index + 1 :]]
                break
            if token == "nodes" and index + 1 < len(tokens):
                try:
                    nodes = int(tokens[index + 1])
                except ValueError:
                    nodes = None
                index += 2
                continue
            index += 1
        return allowed, nodes

    def leader_for(iteration: int, authorized: list[str] | None) -> str:
        """Pick the emitted leader, never leaving the authorized root set."""
        if schedule:
            candidate = schedule[min(iteration, len(schedule) - 1)]
            if not authorized or candidate in authorized:
                return candidate
        if authorized:
            return authorized[iteration % len(authorized)]
        return roots[0] if roots else "e2e4"

    def finish_search(*, infinite: bool, command: str) -> None:
        authorized, nodes = parse_command(command)
        if authorized is not None and not authorized:
            # An explicitly empty restricted-root set is fail-closed: no move.
            emit("bestmove (none)")
            return

        iteration = 0
        if infinite:
            while not stop_event.wait(args.info_delay_ms / 1000.0):
                move = leader_for(iteration, authorized)
                multipv = "" if args.omit_multipv_token else "multipv 1 "
                emit(
                    f"info depth {iteration + 1} {multipv}nodes {(iteration + 1) * 16} "
                    f"score cp {args.score_cp} pv {move}"
                )
                iteration += 1
        else:
            count = max(1, args.info_lines)
            node_total = nodes if nodes is not None else 64
            for iteration in range(count):
                if stop_event.is_set():
                    break
                move = leader_for(iteration, authorized)
                depth = iteration + 1
                observed = node_total if count == 1 else int(node_total * (iteration + 1) / count)
                if args.multipv > 0:
                    pool = authorized or list(roots)
                    for index in range(min(args.multipv, len(pool))):
                        alternative = pool[(iteration + index) % len(pool)]
                        emit(
                            f"info depth {depth} multipv {index + 1} nodes {observed} "
                            f"score cp {args.score_cp - index} pv {alternative}"
                        )
                else:
                    multipv = "" if args.omit_multipv_token else "multipv 1 "
                    emit(
                        f"info depth {depth} {multipv}nodes {observed} "
                        f"score cp {args.score_cp} pv {move}"
                    )
                if count > 1 and args.info_delay_ms > 0:
                    time.sleep(args.info_delay_ms / 1000.0)
            iteration = max(0, min(iteration, max(0, count - 1)))
        emit(f"bestmove {leader_for(iteration, authorized)}")

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
                kwargs={"infinite": infinite, "command": command},
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
            emit("option name Threads type spin default 1 min 1 max 8")
            emit("option name Hash type spin default 16 min 1 max 256")
            emit("option name MultiPV type spin default 1 min 1 max 8")
            emit("option name ScoreType type combo default centipawn var centipawn var WDL_mu")
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
            if args.perft_delay_ms:
                time.sleep(args.perft_delay_ms / 1000.0)
            for move in roots:
                emit(f"{move}: 1")
            emit(f"Nodes searched: {len(roots)}")
        elif command == "go" or command.startswith("go "):
            maybe_exit("go")
            go_count += 1
            if args.exit_on_go_number and go_count == args.exit_on_go_number:
                os._exit(9)
            start_search(command)
        elif command == "stop":
            if not args.ignore_stop:
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
