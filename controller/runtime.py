"""Transactional three-backend runtime for the Generation 1 UCI shell."""

from __future__ import annotations

import glob
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from adapters.process import UciProcess, UciProcessError


class RuntimeError(RuntimeError):
    """Raised when the managed backend runtime cannot preserve its contract."""


_PERFT_ROOT_RE = re.compile(r"^([a-h][1-8][a-h][1-8][qrbn]?):\s+(\d+)$")
_PERFT_TOTAL_RE = re.compile(r"^Nodes searched:\s+(\d+)$")


@dataclass(frozen=True)
class BackendSpec:
    name: str
    binary: Path
    cwd: Path
    args: tuple[str, ...]
    options: dict[str, object]


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    root: Path
    mode: str
    anchor: str
    backends: dict[str, BackendSpec]


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _resolve_binary(root: Path, raw: dict[str, object], name: str) -> Path:
    binary_value = raw.get("binary")
    if not isinstance(binary_value, str) or not binary_value:
        raise RuntimeError(f"backend {name}: binary must be a non-empty string")
    direct = (root / binary_value).resolve()
    if direct.is_file():
        return direct

    fallback = raw.get("fallback_glob")
    if fallback is not None:
        if not isinstance(fallback, str) or not fallback:
            raise RuntimeError(f"backend {name}: fallback_glob must be a non-empty string")
        matches = sorted(
            Path(value).resolve()
            for value in glob.glob(str(root / fallback), recursive=True)
            if Path(value).is_file()
        )
        if matches:
            return matches[0]

    raise RuntimeError(f"backend {name}: binary not found: {direct}")


def load_runtime_config(path: Path) -> RuntimeConfig:
    path = path.resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load runtime config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("runtime config root must be an object")
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise RuntimeError(f"unsupported runtime config schema_version: {version!r}")

    root_value = data.get("root", "..")
    if not isinstance(root_value, str) or not root_value:
        raise RuntimeError("runtime config root must be a non-empty path string")
    root = (path.parent / root_value).resolve()

    mode = data.get("mode")
    if mode != "anchor":
        raise RuntimeError(f"PR9 supports only mode='anchor', got {mode!r}")
    anchor = data.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise RuntimeError("runtime config anchor must be a non-empty string")

    raw_backends = _require_object(data.get("backends"), "backends")
    expected = {"stockfish", "reckless", "lc0"}
    if set(raw_backends) != expected:
        raise RuntimeError(
            f"runtime config backends must be exactly {sorted(expected)}, got {sorted(raw_backends)}"
        )
    if anchor not in expected:
        raise RuntimeError(f"anchor backend is not configured: {anchor}")

    specs: dict[str, BackendSpec] = {}
    for name in ("stockfish", "reckless", "lc0"):
        raw = _require_object(raw_backends[name], f"backend {name}")
        cwd_value = raw.get("cwd", ".")
        if not isinstance(cwd_value, str) or not cwd_value:
            raise RuntimeError(f"backend {name}: cwd must be a non-empty string")
        args_value = raw.get("args", [])
        if not isinstance(args_value, list) or not all(isinstance(item, str) for item in args_value):
            raise RuntimeError(f"backend {name}: args must be an array of strings")
        options = _require_object(raw.get("options", {}), f"backend {name}.options")
        specs[name] = BackendSpec(
            name=name,
            binary=_resolve_binary(root, raw, name),
            cwd=(root / cwd_value).resolve(),
            args=tuple(args_value),
            options=dict(options),
        )

    return RuntimeConfig(
        path=path,
        root=root,
        mode=mode,
        anchor=anchor,
        backends=specs,
    )


class BackendManager:
    """Own all three backend processes; only the configured anchor searches."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.backends: dict[str, UciProcess] = {}
        self._lock = threading.RLock()
        self._started = False
        self._closing = False
        self._unhealthy_reason: str | None = None
        self._failure_handler: Callable[[str, int | None], None] | None = None

    @classmethod
    def from_path(cls, path: Path) -> "BackendManager":
        return cls(load_runtime_config(path))

    def set_failure_handler(self, handler: Callable[[str, int | None], None]) -> None:
        with self._lock:
            self._failure_handler = handler

    @property
    def healthy(self) -> bool:
        with self._lock:
            return (
                self._started
                and not self._closing
                and self._unhealthy_reason is None
                and len(self.backends) == 3
                and all(process.alive for process in self.backends.values())
            )

    @property
    def unhealthy_reason(self) -> str | None:
        with self._lock:
            return self._unhealthy_reason

    @property
    def anchor(self) -> UciProcess:
        try:
            return self.backends[self.config.anchor]
        except KeyError as exc:
            raise RuntimeError("anchor process is not available") from exc

    def _notify_failure(self, message: str, token: int | None) -> None:
        handler: Callable[[str, int | None], None] | None
        with self._lock:
            if self._unhealthy_reason is None:
                self._unhealthy_reason = message
            handler = self._failure_handler
        if handler is not None:
            handler(message, token)

    def _handle_exit(self, name: str, rc: int | None, active_token: int | None) -> None:
        with self._lock:
            if self._closing:
                return
        self._notify_failure(f"backend {name} exited unexpectedly; rc={rc}", active_token)

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("backend runtime already started")
            self._started = True
            self._closing = False
            self._unhealthy_reason = None

        try:
            for name in ("stockfish", "reckless", "lc0"):
                spec = self.config.backends[name]
                process = UciProcess(
                    name=name,
                    binary=spec.binary,
                    cwd=spec.cwd,
                    args=list(spec.args),
                    on_exit=self._handle_exit,
                )
                self.backends[name] = process
                process.start()
                process.configure(spec.options)
            self.ready_all()
        except Exception as exc:
            self.close()
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"backend startup failed: {exc}") from exc

    def _require_healthy(self) -> None:
        if not self.healthy:
            reason = self.unhealthy_reason or "backend runtime is not healthy"
            raise RuntimeError(reason)

    def ready_all(self) -> None:
        self._require_started()
        try:
            for process in self.backends.values():
                process.ready()
        except UciProcessError as exc:
            self._notify_failure(f"backend readiness failure: {exc}", None)
            raise RuntimeError(str(exc)) from exc
        self._require_healthy()

    def _require_started(self) -> None:
        with self._lock:
            if not self._started or self._closing:
                raise RuntimeError("backend runtime is not active")

    def set_chess960(self, enabled: bool) -> None:
        self._require_healthy()
        try:
            for process in self.backends.values():
                process.set_option("UCI_Chess960", enabled)
            self.ready_all()
        except UciProcessError as exc:
            self._notify_failure(f"UCI_Chess960 synchronization failed: {exc}", None)
            raise RuntimeError(str(exc)) from exc

    def new_game(self) -> None:
        self._require_healthy()
        try:
            for process in self.backends.values():
                process.new_game()
            self.ready_all()
        except UciProcessError as exc:
            self._notify_failure(f"ucinewgame synchronization failed: {exc}", None)
            raise RuntimeError(str(exc)) from exc

    def set_position(self, command: str) -> None:
        self._require_healthy()
        try:
            for process in self.backends.values():
                process.send_position(command)
        except UciProcessError as exc:
            self._notify_failure(f"position synchronization failed: {exc}", None)
            raise RuntimeError(str(exc)) from exc

    def legal_root_moves(self) -> tuple[str, ...]:
        """Return canonical legal root moves from Stockfish's depth-1 perft oracle."""
        self._require_healthy()
        if self.config.anchor != "stockfish":
            raise RuntimeError("root legal-move oracle requires Stockfish anchor mode")
        try:
            lines = self.anchor.run_idle_request(
                "go perft 1",
                lambda line: _PERFT_TOTAL_RE.fullmatch(line) is not None,
                label="Stockfish go perft 1",
                timeout=10.0,
            )
        except UciProcessError as exc:
            self._notify_failure(f"legal-root oracle process failure: {exc}", None)
            raise RuntimeError(str(exc)) from exc

        roots: list[str] = []
        seen: set[str] = set()
        total: int | None = None
        for line in lines:
            total_match = _PERFT_TOTAL_RE.fullmatch(line)
            if total_match is not None:
                total = int(total_match.group(1))
                continue
            root_match = _PERFT_ROOT_RE.fullmatch(line)
            if root_match is None:
                # Stockfish may emit unrelated informational material (for
                # example network-verification strings) before perft output.
                continue
            move, count_raw = root_match.groups()
            count = int(count_raw)
            if count != 1:
                self._notify_failure(
                    f"legal-root oracle returned depth-1 count {count} for {move}",
                    None,
                )
                raise RuntimeError(
                    f"Stockfish perft-1 root {move} reported count {count}, expected 1"
                )
            if move in seen:
                self._notify_failure(
                    f"legal-root oracle returned duplicate root {move}",
                    None,
                )
                raise RuntimeError(f"Stockfish perft-1 returned duplicate root {move}")
            seen.add(move)
            roots.append(move)

        if total is None:
            self._notify_failure("legal-root oracle omitted Nodes searched total", None)
            raise RuntimeError("Stockfish perft-1 omitted Nodes searched total")
        if total != len(roots):
            self._notify_failure(
                f"legal-root oracle total mismatch: total={total}, roots={len(roots)}",
                None,
            )
            raise RuntimeError(
                f"Stockfish perft-1 total mismatch: Nodes searched={total}, "
                f"parsed roots={len(roots)}"
            )
        return tuple(roots)

    def start_anchor_search(
        self,
        command: str,
        *,
        token: int,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> None:
        self._require_healthy()
        try:
            self.anchor.start_search(
                command,
                token=token,
                on_info=on_info,
                on_complete=on_complete,
            )
        except UciProcessError as exc:
            self._notify_failure(f"anchor search dispatch failed: {exc}", token)
            raise RuntimeError(str(exc)) from exc

    def stop_anchor(self) -> None:
        self._require_started()
        try:
            self.anchor.stop()
        except UciProcessError as exc:
            self._notify_failure(f"anchor stop failed: {exc}", self.anchor.active_token)
            raise RuntimeError(str(exc)) from exc

    def ponderhit_anchor(self) -> None:
        self._require_started()
        try:
            self.anchor.ponderhit()
        except UciProcessError as exc:
            self._notify_failure(f"anchor ponderhit failed: {exc}", self.anchor.active_token)
            raise RuntimeError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
        try:
            for name in ("lc0", "reckless", "stockfish"):
                process = self.backends.get(name)
                if process is not None:
                    process.close()
        finally:
            with self._lock:
                self._started = False
