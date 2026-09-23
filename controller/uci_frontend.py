"""Externally visible AllfatherChess UCI frontend."""

from __future__ import annotations

import re
import sys
import threading
from enum import Enum
from typing import TYPE_CHECKING, TextIO

from .runtime import BackendManager, RuntimeError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .shadow import ShadowRunCoordinator


_SETOPTION_RE = re.compile(r"^setoption\s+name\s+(.+?)(?:\s+value(?:\s+(.*))?)?$")


class ShellState(str, Enum):
    READY = "ready"
    SEARCHING = "searching"
    UNHEALTHY = "unhealthy"
    SHUTDOWN = "shutdown"


class UciFrontend:
    """One external UCI identity with Stockfish as the transparent anchor.

    Shadow workers, when configured, observe the same synchronized state but can
    never write to this stream: only the anchor's `bestmove` leaves the process.
    """

    def __init__(
        self,
        runtime: BackendManager,
        *,
        output: TextIO | None = None,
        shadow: "ShadowRunCoordinator | None" = None,
    ) -> None:
        self.runtime = runtime
        self.shadow = shadow
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

        # Avoid unsolicited UCI output while idle. An unhealthy idle runtime
        # reports its stored reason on the next readiness request. During an
        # active search we must unblock the GUI immediately and fail closed.
        if searching:
            self._shadow_cancel("anchor_failure", active)
            self._diagnostic(f"runtime failure: {message}")
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
        if self.shadow is not None:
            try:
                self.shadow.note_anchor_complete(token, line)
            except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
                self._diagnostic(f"shadow release failed: {exc}")
        with self._state_lock:
            if self._state != ShellState.SEARCHING or self._active_generation != token:
                return
            self._active_generation = None
            self._state = ShellState.READY if self.runtime.healthy else ShellState.UNHEALTHY
        self._write(line)

    def _shadow_cancel(self, reason: str, generation: int | None = None) -> None:
        """Cancel shadow observation without ever waiting on it.

        Every call here is on an authority path -- the command loop or a
        runtime-failure handler -- and cancellation writes `stop` to each
        dispatched shadow's stdin, which can block on a full pipe. `detach`
        keeps that write off this thread entirely; the coordinator's quiesce
        barrier joins the detached writer before any state change, so a late
        `stop` can never reach the next generation.
        """
        if self.shadow is None:
            return
        try:
            self.shadow.cancel(generation, reason=reason, detach=True)
        except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
            self._diagnostic(f"shadow cancel failed: {exc}")

    def _shadow_quiesce(self) -> None:
        """Hard barrier before any state mutation.

        A prior shadow generation must never be able to observe the next
        position, so state synchronization waits for the previous run to drain.

        A worker that misses the drain deadline is recorded as a shadow failure
        by the coordinator, which removes it from synchronization and dispatch.
        The mutation that follows therefore never reaches a process still
        executing the previous generation.
        """
        if self.shadow is None:
            return
        try:
            if not self.shadow.quiesce():
                self._diagnostic(
                    "previous shadow generation did not drain; the affected shadow "
                    "workers are recorded as failed and excluded from this state change"
                )
        except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
            self._diagnostic(f"shadow quiesce failed: {exc}")

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
        self._shadow_quiesce()
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
            self.runtime.ready_authority()
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

        prepared = False
        if self.shadow is not None:
            try:
                prepared = self.shadow.prepare_run(generation=token, go_command=command)
            except Exception as exc:  # pragma: no cover - shadow setup is non-authoritative
                self._diagnostic(f"shadow run preparation failed: {exc}")

        try:
            # The outward anchor always starts first. Shadow qualification and
            # dispatch happen afterwards on a worker thread so no observational
            # work can delay the decision authority.
            self.runtime.start_anchor_search(
                command,
                token=token,
                on_info=self._on_search_info,
                on_complete=self._on_search_complete,
            )
        except RuntimeError as exc:
            if prepared and self.shadow is not None:
                try:
                    self.shadow.abort_run(token, reason="anchor_dispatch_failed")
                except Exception:  # pragma: no cover
                    pass
            with self._state_lock:
                already_failed = self._state == ShellState.UNHEALTHY
            if not already_failed:
                self._runtime_failed(f"search dispatch failed: {exc}", token)
            return

        if prepared and self.shadow is not None:
            try:
                self.shadow.start_shadow_work(token)
            except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
                self._diagnostic(f"shadow dispatch failed: {exc}")

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
            self._shadow_quiesce()
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
            self._shadow_quiesce()
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
                # AUTHORITY FIRST. Cancelling shadows ahead of this sent `stop`
                # to every dispatched observational process, so one blocked
                # shadow stdin meant `stop_anchor()` was never reached and the
                # anchor's already-computed `bestmove` was never requested. The
                # GUI's `stop` is a decision-authority command; observation is
                # torn down afterwards and off this thread.
                try:
                    self.runtime.stop_anchor()
                except RuntimeError as exc:
                    if self.state != ShellState.UNHEALTHY:
                        self._runtime_failed(str(exc), self._active_generation)
                self._shadow_cancel("stop", self._active_generation)
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
            self._shutdown()
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
                self._shutdown()

    def _shutdown(self) -> None:
        """Close shadow work before the processes so no run is left orphaned."""
        if self.shadow is not None:
            try:
                self.shadow.close()
            except Exception as exc:  # pragma: no cover - shutdown is best effort
                self._diagnostic(f"shadow shutdown failed: {exc}")
        self.runtime.close()
