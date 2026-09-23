"""Portable line-oriented UCI process session with hard timeouts."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable


class UciError(RuntimeError):
    pass


_OPTION_RE = re.compile(r"^option name (.+?) type ")


class UciSession:
    def __init__(
        self,
        binary: Path,
        *,
        cwd: Path,
        timeout: float = 10.0,
        args: list[str] | None = None,
    ):
        self.binary = binary
        self.cwd = cwd
        self.timeout = timeout
        self.args = list(args or [])
        self.proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self.options: set[str] = set()
        self.transcript: list[str] = []

    def __enter__(self) -> "UciSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self.proc is not None:
            raise UciError("session already started")
        if not self.binary.is_file():
            raise UciError(f"engine binary not found: {self.binary}")
        if not os.access(self.binary, os.X_OK):
            raise UciError(f"engine binary not executable: {self.binary}")

        self.proc = subprocess.Popen(
            [str(self.binary), *self.args],
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert self.proc.stdout is not None

        def reader() -> None:
            try:
                for raw in self.proc.stdout:
                    self._lines.put(raw.rstrip("\r\n"))
            finally:
                self._lines.put(None)

        self._reader = threading.Thread(target=reader, name=f"uci-reader-{self.binary.name}", daemon=True)
        self._reader.start()

        self.send("uci")
        lines = self.read_until(lambda line: line == "uciok", label="uciok")
        for line in lines:
            match = _OPTION_RE.match(line)
            if match:
                self.options.add(match.group(1))

    def send(self, command: str) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise UciError("session not started")
        if self.proc.poll() is not None:
            raise UciError(f"engine exited before command: {command}")
        self.transcript.append(f">> {command}")
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def read_until(
        self,
        predicate: Callable[[str], bool],
        *,
        label: str,
        timeout: float | None = None,
        observer: Callable[[str], None] | None = None,
    ) -> list[str]:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail = "\n".join(self.transcript[-80:])
                raise UciError(f"timeout waiting for {label}\n{tail}")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                tail = "\n".join(self.transcript[-80:])
                raise UciError(f"timeout waiting for {label}\n{tail}") from exc
            if line is None:
                rc = None if self.proc is None else self.proc.poll()
                tail = "\n".join(self.transcript[-80:])
                raise UciError(f"engine exited while waiting for {label}; rc={rc}\n{tail}")
            self.transcript.append(f"<< {line}")
            lines.append(line)
            if observer is not None:
                observer(line)
            if predicate(line):
                return lines

    def configure(self, options: dict[str, object]) -> None:
        missing = [name for name in options if name not in self.options]
        if missing:
            raise UciError(
                f"{self.binary.name}: required UCI options not exposed: {', '.join(sorted(missing))}"
            )
        for name, value in options.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
                self.send(f"setoption name {name} value {rendered}")
            elif value == "":
                self.send(f"setoption name {name} value")
            else:
                self.send(f"setoption name {name} value {value}")
        self.ready()

    def ready(self) -> None:
        self.send("isready")
        self.read_until(lambda line: line == "readyok", label="readyok")

    def new_game(self) -> None:
        self.send("ucinewgame")
        self.ready()

    def set_position(self, position: dict[str, object]) -> None:
        if "fen" in position:
            fen = str(position["fen"])
            moves = position.get("moves", [])
            command = f"position fen {fen}"
            if moves:
                command += " moves " + " ".join(str(x) for x in moves)
        elif "startpos_moves" in position:
            command = "position startpos"
            moves = position.get("startpos_moves", [])
            if moves:
                command += " moves " + " ".join(str(x) for x in moves)
        else:
            raise UciError(f"unsupported position specification: {position}")
        self.send(command)

    def search_nodes(
        self,
        nodes: int,
        *,
        searchmoves: list[str] | None = None,
        timeout: float = 20.0,
        observer: Callable[[str], None] | None = None,
    ) -> list[str]:
        command = f"go nodes {nodes}"
        if searchmoves is not None:
            command += " searchmoves"
            if searchmoves:
                command += " " + " ".join(searchmoves)
        self.send(command)
        return self.read_until(
            lambda line: line.startswith("bestmove "),
            label="bestmove",
            timeout=timeout,
            observer=observer,
        )

    def synchronous_command(self, command: str, *, timeout: float = 10.0) -> list[str]:
        self.send(command)
        self.send("isready")
        lines = self.read_until(lambda line: line == "readyok", label=f"{command} + readyok", timeout=timeout)
        return lines[:-1]

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self.send("quit")
            except (BrokenPipeError, UciError):
                pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        for handle in (proc.stdin, proc.stdout, proc.stderr):
            if handle is None:
                continue
            try:
                handle.close()
            except OSError:
                pass
        self.proc = None
