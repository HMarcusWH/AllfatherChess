"""Long-lived UCI subprocess with a single stdout reader/demultiplexer."""

from __future__ import annotations

import collections
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class UciProcessError(RuntimeError):
    """Raised when a backend process violates the process/handshake contract."""


_OPTION_RE = re.compile(r"^option name (.+?) type ")


@dataclass
class _Waiter:
    predicate: Callable[[str], bool]
    label: str
    collect: bool = False
    event: threading.Event = field(default_factory=threading.Event)
    lines: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class _SearchSubscription:
    token: int
    on_info: Callable[[int, str], None]
    on_complete: Callable[[int, str], None]


class UciProcess:
    """Own one backend process and consume its stdout exactly once.

    Multiple protocol waiters (for example an isready barrier during an active
    search) observe the same reader stream without racing to dequeue lines.
    Search info/bestmove callbacks are demultiplexed by that same reader.
    """

    def __init__(
        self,
        *,
        name: str,
        binary: Path,
        cwd: Path,
        args: list[str] | None = None,
        timeout: float = 10.0,
        on_exit: Callable[[str, int | None, int | None], None] | None = None,
    ) -> None:
        self.name = name
        self.binary = binary
        self.cwd = cwd
        self.args = list(args or [])
        self.timeout = timeout
        self._on_exit = on_exit

        self.proc: subprocess.Popen[str] | None = None
        self.options: set[str] = set()

        self._state_lock = threading.RLock()
        self._stdin_lock = threading.Lock()
        self._command_gate = threading.Lock()
        self._waiters: list[_Waiter] = []
        self._search: _SearchSubscription | None = None
        self._closing = False
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._transcript: collections.deque[str] = collections.deque(maxlen=500)
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=200)
        self._callback_errors: collections.deque[str] = collections.deque(maxlen=20)

    @property
    def active_search(self) -> bool:
        with self._state_lock:
            return self._search is not None

    @property
    def active_token(self) -> int | None:
        with self._state_lock:
            return None if self._search is None else self._search.token

    @property
    def alive(self) -> bool:
        proc = self.proc
        return proc is not None and proc.poll() is None

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        """Snapshot recent backend stderr for qualification/diagnostics."""
        with self._state_lock:
            return tuple(self._stderr_tail)

    def _diagnostic_tail(self) -> str:
        transcript = "\n".join(self._transcript)
        stderr = "\n".join(self._stderr_tail)
        callback_errors = "\n".join(self._callback_errors)
        parts = []
        if transcript:
            parts.append("transcript:\n" + transcript)
        if stderr:
            parts.append("stderr:\n" + stderr)
        if callback_errors:
            parts.append("callback errors:\n" + callback_errors)
        return "\n".join(parts)

    def start(self) -> None:
        if self.proc is not None:
            raise UciProcessError(f"{self.name}: process already started")
        if not self.binary.is_file():
            raise UciProcessError(f"{self.name}: engine binary not found: {self.binary}")
        if not os.access(self.binary, os.X_OK):
            raise UciProcessError(f"{self.name}: engine binary not executable: {self.binary}")

        self.proc = subprocess.Popen(
            [str(self.binary), *self.args],
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None

        self._stdout_thread = threading.Thread(
            target=self._stdout_reader,
            name=f"allfather-stdout-{self.name}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader,
            name=f"allfather-stderr-{self.name}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        lines = self._request(
            "uci",
            lambda line: line == "uciok",
            label="uciok",
            collect=True,
        )
        for line in lines:
            match = _OPTION_RE.match(line)
            if match:
                self.options.add(match.group(1))

    def _stdout_reader(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                line = raw.rstrip("\r\n")
                self._transcript.append(f"<< {line}")
                info_callback: tuple[Callable[[int, str], None], int, str] | None = None
                complete_callback: tuple[Callable[[int, str], None], int, str] | None = None

                with self._state_lock:
                    for waiter in list(self._waiters):
                        if waiter.collect:
                            waiter.lines.append(line)
                        try:
                            matched = waiter.predicate(line)
                        except Exception as exc:  # pragma: no cover - defensive callback boundary
                            waiter.error = f"waiter predicate failed for {waiter.label}: {exc}"
                            matched = True
                        if matched:
                            if waiter in self._waiters:
                                self._waiters.remove(waiter)
                            waiter.event.set()

                    subscription = self._search
                    if subscription is not None:
                        if line.startswith("bestmove "):
                            self._search = None
                            complete_callback = (
                                subscription.on_complete,
                                subscription.token,
                                line,
                            )
                        elif line.startswith("info"):
                            info_callback = (
                                subscription.on_info,
                                subscription.token,
                                line,
                            )

                if info_callback is not None:
                    callback, token, payload = info_callback
                    self._safe_callback(callback, token, payload)
                if complete_callback is not None:
                    callback, token, payload = complete_callback
                    self._safe_callback(callback, token, payload)
        finally:
            self._handle_eof()

    def _stderr_reader(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for raw in self.proc.stderr:
            self._stderr_tail.append(raw.rstrip("\r\n"))

    def _safe_callback(self, callback: Callable[[int, str], None], token: int, line: str) -> None:
        try:
            callback(token, line)
        except Exception as exc:  # pragma: no cover - callbacks are isolated from process IO
            self._callback_errors.append(f"{type(exc).__name__}: {exc}")

    def _handle_eof(self) -> None:
        proc = self.proc
        rc = None if proc is None else proc.poll()
        if proc is not None and rc is None:
            try:
                rc = proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                rc = None

        with self._state_lock:
            active_token = None if self._search is None else self._search.token
            self._search = None
            for waiter in list(self._waiters):
                waiter.error = f"{self.name}: engine exited while waiting for {waiter.label}; rc={rc}"
                waiter.event.set()
            self._waiters.clear()
            closing = self._closing

        if not closing and self._on_exit is not None:
            try:
                self._on_exit(self.name, rc, active_token)
            except Exception as exc:  # pragma: no cover - owner callback isolation
                self._callback_errors.append(f"exit callback {type(exc).__name__}: {exc}")

    def send(self, command: str) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise UciProcessError(f"{self.name}: process not started")
        if proc.poll() is not None:
            raise UciProcessError(f"{self.name}: engine exited before command: {command}")
        with self._stdin_lock:
            self._transcript.append(f">> {command}")
            try:
                proc.stdin.write(command + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise UciProcessError(f"{self.name}: failed to send {command!r}: {exc}") from exc

    def _request(
        self,
        command: str,
        predicate: Callable[[str], bool],
        *,
        label: str,
        timeout: float | None = None,
        collect: bool = False,
    ) -> list[str]:
        waiter = _Waiter(predicate=predicate, label=label, collect=collect)
        with self._state_lock:
            self._waiters.append(waiter)
        try:
            self.send(command)
        except Exception:
            with self._state_lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise

        if not waiter.event.wait(self.timeout if timeout is None else timeout):
            with self._state_lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise UciProcessError(
                f"{self.name}: timeout waiting for {label}\n{self._diagnostic_tail()}"
            )
        if waiter.error is not None:
            raise UciProcessError(waiter.error + "\n" + self._diagnostic_tail())
        return waiter.lines

    def ready(self, *, timeout: float | None = None) -> None:
        self._request(
            "isready",
            lambda line: line == "readyok",
            label="readyok",
            timeout=timeout,
        )

    def run_idle_request(
        self,
        command: str,
        terminal_predicate: Callable[[str], bool],
        *,
        label: str,
        timeout: float | None = None,
    ) -> list[str]:
        """Run one synchronous non-search diagnostic while no search is active.

        The command gate is shared with start_search so an idle transaction and
        a search cannot begin concurrently. ready() intentionally remains
        independent because PR #9 permits isready during active search.
        """
        with self._command_gate:
            with self._state_lock:
                if self._search is not None:
                    raise UciProcessError(
                        f"{self.name}: idle request {label!r} is forbidden during active search"
                    )
            return self._request(
                command,
                terminal_predicate,
                label=label,
                timeout=timeout,
                collect=True,
            )

    def set_option(self, name: str, value: object) -> None:
        if name not in self.options:
            raise UciProcessError(f"{self.name}: required UCI option not exposed: {name}")
        if isinstance(value, bool):
            rendered = "true" if value else "false"
            self.send(f"setoption name {name} value {rendered}")
        elif value == "":
            self.send(f"setoption name {name} value")
        else:
            self.send(f"setoption name {name} value {value}")

    def configure(self, options: dict[str, object]) -> None:
        missing = sorted(name for name in options if name not in self.options)
        if missing:
            raise UciProcessError(
                f"{self.name}: required UCI options not exposed: {', '.join(missing)}"
            )
        for name, value in options.items():
            self.set_option(name, value)
        self.ready()

    def send_position(self, command: str) -> None:
        if not command.startswith("position "):
            raise UciProcessError(f"{self.name}: invalid position command: {command!r}")
        self.send(command)

    def new_game(self) -> None:
        self.send("ucinewgame")

    def start_search(
        self,
        command: str,
        *,
        token: int,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> None:
        if command != "go" and not command.startswith("go "):
            raise UciProcessError(f"{self.name}: invalid go command: {command!r}")
        with self._command_gate:
            with self._state_lock:
                if self._search is not None:
                    raise UciProcessError(f"{self.name}: search already active")
                self._search = _SearchSubscription(
                    token=token,
                    on_info=on_info,
                    on_complete=on_complete,
                )
            try:
                self.send(command)
            except Exception:
                with self._state_lock:
                    if self._search is not None and self._search.token == token:
                        self._search = None
                raise

    def stop(self) -> None:
        if self.active_search:
            self.send("stop")

    def ponderhit(self) -> None:
        if self.active_search:
            self.send("ponderhit")

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        with self._state_lock:
            self._closing = True
        if proc.poll() is None:
            try:
                self.send("quit")
            except UciProcessError:
                pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=1.0)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
