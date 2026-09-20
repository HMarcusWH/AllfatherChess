"""Externally visible AllfatherChess UCI frontend for anchor mode."""

from __future__ import annotations

import re
import sys
import threading
from enum import Enum
from typing import TextIO

from .runtime import BackendManager, RuntimeError


_SETOPTION_RE = re.compile(r"^setoption\s+name\s+(.+?)(?:\s+value(?:\s+(.*))?)?$")


class ShellState(str, Enum):
    READY = "ready"
    SEARCHING = "searching"
    UNHEALTHY = "unhealthy"
    SHUTDOWN = "shutdown"


class UciFrontend:
    """One external UCI identity with Stockfish as the transparent PR9 anchor."""

    def __init__(self, runtime: BackendManager, *, output: TextIO | None = None) -> None:
        self.runtime = runtime
        self.output = sys.stdout if output is None else output
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._state = ShellState.READY if runtime.healthy else ShellState.UNHEALTHY
        self._generation = 0
        self._active_generation: int | None = None
        self.runtime.set_failure_handler(self._runtime_failed)

    @property
    def state(self) -> ShellState:
        with self._state_lock:
            return self._state

    def _write(self, line: str) -> None:
        with self._write_lock:
            self.output.write(line + "\n")
            self.output.flush()

    def _diagnostic(self, message: str) -> None:
        sanitized = " ".join(str(message).splitlines())
        self._write(f"info string Allfather {sanitized}")

    def _runtime_failed(self, message: str, token: int | None) -> None:
        with self._state_lock:
            if self._state == ShellState.SHUTDOWN:
                return
            searching = self._state == ShellState.SEARCHING
            active = self._active_generation
            if token is not None and searching and active is not None and token != active:
                return
            self._state = ShellState.UNHEALTHY
            self._active_generation = None

        self._diagnostic(f"runtime failure: {message}")
        if searching:
            self._write("bestmove 0000")
            try:
                if self.runtime.anchor.alive:
                    self.runtime.anchor.stop()
            except Exception:
                pass

    def _on_search_info(self, token: int, line: str) -> None:
        with self._state_lock:
            if self._state != ShellState.SEARCHING or self._active_generation != token:
                return
        self._write(line)

    def _on_search_complete(self, token: int, line: str) -> None:
        with self._state_lock:
            if self._state != ShellState.SEARCHING or self._active_generation != token:
                return
            self._active_generation = None
            self._state = ShellState.READY if self.runtime.healthy else ShellState.UNHEALTHY
        self._write(line)

    def _reject_while_searching(self, command: str) -> None:
        self._diagnostic(f"rejected {command!r} while anchor search is active")

    def _handle_setoption(self, command: str) -> None:
        with self._state_lock:
            if self._state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return
            if self._state != ShellState.READY:
                return

        match = _SETOPTION_RE.fullmatch(command)
        if match is None:
            self._diagnostic("ignored malformed setoption command")
            return
        name = match.group(1).strip()
        value = (match.group(2) or "").strip()

        # PR9 deliberately exposes no resource-routing knobs. Unknown GUI
        # options are ignored rather than leaked into one constituent backend.
        if name != "UCI_Chess960":
            return
        lowered = value.lower()
        if lowered not in {"true", "false"}:
            self._diagnostic("UCI_Chess960 expects true or false")
            return
        try:
            self.runtime.set_chess960(lowered == "true")
        except RuntimeError as exc:
            if self.state != ShellState.UNHEALTHY:
                self._runtime_failed(str(exc), None)

    def _handle_ready(self) -> None:
        if self.state == ShellState.UNHEALTHY:
            self._diagnostic(self.runtime.unhealthy_reason or "runtime is unhealthy")
            return
        try:
            self.runtime.ready_all()
        except RuntimeError as exc:
            if self.state != ShellState.UNHEALTHY:
                self._runtime_failed(str(exc), None)
            return
        self._write("readyok")

    def _handle_go(self, command: str) -> None:
        with self._state_lock:
            if self._state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return
            if self._state != ShellState.READY or not self.runtime.healthy:
                self._diagnostic("cannot start search while runtime is unhealthy")
                return
            self._generation += 1
            token = self._generation
            self._active_generation = token
            self._state = ShellState.SEARCHING

        try:
            self.runtime.start_anchor_search(
                command,
                token=token,
                on_info=self._on_search_info,
                on_complete=self._on_search_complete,
            )
        except RuntimeError as exc:
            with self._state_lock:
                already_failed = self._state == ShellState.UNHEALTHY
            if not already_failed:
                self._runtime_failed(f"search dispatch failed: {exc}", token)

    def handle_command(self, raw: str) -> bool:
        command = raw.strip()
        if not command:
            return True

        if command == "uci":
            self._write("id name AllfatherChess")
            self._write("id author AllfatherChess Project")
            self._write("option name UCI_Chess960 type check default false")
            self._write("uciok")
            return True

        if command == "isready":
            self._handle_ready()
            return True

        if command.startswith("setoption "):
            self._handle_setoption(command)
            return True

        if command == "ucinewgame":
            if self.state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return True
            if self.state != ShellState.READY:
                return True
            try:
                self.runtime.new_game()
            except RuntimeError as exc:
                if self.state != ShellState.UNHEALTHY:
                    self._runtime_failed(str(exc), None)
            return True

        if command.startswith("position "):
            if self.state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return True
            if self.state != ShellState.READY:
                return True
            if not (command.startswith("position startpos") or command.startswith("position fen ")):
                self._diagnostic("unsupported position command")
                return True
            try:
                self.runtime.set_position(command)
            except RuntimeError as exc:
                if self.state != ShellState.UNHEALTHY:
                    self._runtime_failed(str(exc), None)
            return True

        if command == "go" or command.startswith("go "):
            self._handle_go(command)
            return True

        if command == "stop":
            if self.state == ShellState.SEARCHING:
                try:
                    self.runtime.stop_anchor()
                except RuntimeError as exc:
                    if self.state != ShellState.UNHEALTHY:
                        self._runtime_failed(str(exc), self._active_generation)
            return True

        if command == "ponderhit":
            if self.state == ShellState.SEARCHING:
                try:
                    self.runtime.ponderhit_anchor()
                except RuntimeError as exc:
                    if self.state != ShellState.UNHEALTHY:
                        self._runtime_failed(str(exc), self._active_generation)
            return True

        if command == "quit":
            with self._state_lock:
                self._state = ShellState.SHUTDOWN
                self._active_generation = None
            self.runtime.close()
            return False

        # UCI permits engines to ignore commands/options they do not implement.
        return True

    def run(self, input_stream: TextIO | None = None) -> None:
        stream = sys.stdin if input_stream is None else input_stream
        try:
            for raw in stream:
                if not self.handle_command(raw):
                    return
        finally:
            if self.state != ShellState.SHUTDOWN:
                with self._state_lock:
                    self._state = ShellState.SHUTDOWN
                    self._active_generation = None
                self.runtime.close()
