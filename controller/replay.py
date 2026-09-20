"""Run-level replay bundles for one external Allfather search.

A replay bundle answers exactly one question: **what experiment was executed?**

Telemetry answers a different question: what did each engine observe. The two
must not be merged. Consequently this module deliberately contains no residual
feature, no aggregate ranking, and no routing decision. Route decisions produced
by the active controller are written to a sibling `route.json` by
`controller/routing.py`, never into the raw manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from common.search_request import PositionRequest, parse_go_request
from common.telemetry import TelemetryError


REPLAY_SCHEMA_VERSION = 1

#: Bounded per-stream backlog. The reader thread must never block, so an
#: overflow is recorded as explicit truncation evidence instead of back-pressure.
_STREAM_QUEUE_MAXSIZE = 200_000

_SENTINEL = object()


class ReplayError(RuntimeError):
    """Raised when replay evidence cannot be recorded honestly."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class StageRecord:
    """One dispatched search on one engine instance.

    A stage is an orchestration fact. Why the stage was dispatched is policy and
    lives in `route.json`.
    """

    stage_index: int
    instance: str
    family: str
    role: str
    owner: str | None
    search_id: str
    command: str
    dispatched_roots: tuple[str, ...]
    dispatch_order: int
    dispatched_ms: float
    completed_ms: float | None = None
    completion_order: int | None = None
    disposition: str = "running"
    stop_reason: str | None = None
    bestmove: str | None = None
    failure: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "stage_index": self.stage_index,
            "instance": self.instance,
            "engine": self.family,
            "role": self.role,
            "owner": self.owner,
            "search_id": self.search_id,
            "command": self.command,
            "request": parse_go_request(self.command),
            "dispatched_roots": list(self.dispatched_roots),
            "dispatch_order": self.dispatch_order,
            "dispatched_ms": round(self.dispatched_ms, 3),
            "completed_ms": None if self.completed_ms is None else round(self.completed_ms, 3),
            "completion_order": self.completion_order,
            "disposition": self.disposition,
            "stop_reason": self.stop_reason,
            "bestmove": self.bestmove,
            "failure": self.failure,
        }


class TelemetryStreamWriter:
    """Own one instance's JSONL telemetry file.

    The engine's stdout reader thread only enqueues. A dedicated writer thread
    performs adapter translation and file IO, so telemetry capture can never
    stall or deadlock the single stdout reader.
    """

    def __init__(
        self,
        *,
        instance: str,
        family: str,
        role: str,
        path: Path,
        adapter_factory: Callable[[str], Any],
    ) -> None:
        self.instance = instance
        self.family = family
        self.role = role
        self.path = path
        self._adapter_factory = adapter_factory
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=_STREAM_QUEUE_MAXSIZE)
        self._lock = threading.Lock()
        self._adapter: Any | None = None
        self._search_ids: list[str] = []
        self._event_count = 0
        self._dropped = 0
        self._post_complete_lines = 0
        self._errors: list[str] = []
        self._closed = False
        self._queued_peak = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        self._thread = threading.Thread(
            target=self._drain,
            name=f"allfather-telemetry-{instance}",
            daemon=True,
        )
        self._thread.start()

    # -- producer side (runs on the engine stdout reader thread) -------------

    def begin_stage(
        self,
        *,
        search_id: str,
        position: dict[str, Any],
        request: dict[str, Any],
        controller: dict[str, Any] | None,
    ) -> None:
        self._enqueue(("begin", search_id, position, request, controller))

    def submit(self, line: str, observed_ms: float) -> None:
        self._enqueue(("line", line, observed_ms))

    def _enqueue(self, item: Any) -> None:
        with self._lock:
            if self._closed:
                return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return
        size = self._queue.qsize()
        if size > self._queued_peak:
            self._queued_peak = size

    # -- consumer side (dedicated writer thread) ----------------------------

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                return
            try:
                self._apply(item)
            except Exception as exc:  # pragma: no cover - writer isolation
                with self._lock:
                    if len(self._errors) < 32:
                        self._errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()

    def _apply(self, item: Any) -> None:
        kind = item[0]
        if kind == "begin":
            _, search_id, position, request, controller = item
            adapter = self._adapter_factory(search_id)
            event = adapter.start(position=position, request=request, controller=controller)
            self._adapter = adapter
            with self._lock:
                self._search_ids.append(search_id)
            self._write(event)
            return

        _, line, observed_ms = item
        adapter = self._adapter
        if adapter is None:
            with self._lock:
                if len(self._errors) < 32:
                    self._errors.append("telemetry line observed before search.started")
            return
        if adapter.stream.completed:
            with self._lock:
                self._post_complete_lines += 1
            return
        try:
            events = adapter.consume(line, observed_ms=observed_ms)
        except TelemetryError as exc:
            with self._lock:
                if len(self._errors) < 32:
                    self._errors.append(f"{type(exc).__name__}: {exc}")
            return
        for event in events:
            self._write(event)

    def _write(self, event: dict[str, Any]) -> None:
        self._handle.write(json.dumps(event, sort_keys=True) + "\n")
        self._event_count += 1

    # -- lifecycle -----------------------------------------------------------

    @property
    def completed(self) -> bool:
        adapter = self._adapter
        return adapter is not None and adapter.stream.completed

    def close(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except (OSError, ValueError):  # pragma: no cover - best-effort durability
            pass
        self._handle.close()

    def snapshot(self) -> dict[str, Any]:
        if not self._closed:
            raise ReplayError("telemetry stream snapshot requires a closed stream")
        with self._lock:
            errors = list(self._errors)
            dropped = self._dropped
            search_ids = list(self._search_ids)
            post_complete = self._post_complete_lines
        complete = self.completed
        return {
            "instance": self.instance,
            "engine": self.family,
            "role": self.role,
            "path": self.path.name,
            "search_ids": search_ids,
            "event_count": self._event_count,
            "sha256": sha256_file(self.path),
            "bytes": self.path.stat().st_size,
            "complete": complete,
            # A stream without search.complete is real evidence but is not a
            # valid telemetry v1 stream, so the contract validator must skip it.
            "contract_validatable": complete and not errors and dropped == 0,
            "dropped_events": dropped,
            "post_complete_lines": post_complete,
            "queued_peak": self._queued_peak,
            "adapter_errors": errors,
        }


@dataclass
class ReplayRun:
    """Accumulate the orchestration evidence for one external search."""

    run_id: str
    generation: int
    run_dir: Path
    mode: str
    telemetry_execution_mode: str
    position: PositionRequest
    external_go_command: str
    config_path: str
    config_sha256: str
    engine_identities: dict[str, Any]
    ledger_owners: tuple[str, ...]
    partition_method: str
    created_utc: str
    _streams: dict[str, TelemetryStreamWriter] = field(default_factory=dict)
    _stages: list[StageRecord] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _dispatch_counter: int = 0
    _completion_counter: int = 0
    pre_ledger_snapshot: dict[str, Any] | None = None
    post_ledger_snapshot: dict[str, Any] | None = None
    owner_roots: dict[str, list[str]] = field(default_factory=dict)
    shadow_health: dict[str, Any] = field(default_factory=dict)
    run_disposition: str = "running"
    run_stop_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    oracle_root_count: int | None = None
    oracle_instance: str | None = None
    terminal_universe: bool = False
    finalized: bool = False

    # -- streams -------------------------------------------------------------

    def register_stream(self, writer: TelemetryStreamWriter) -> None:
        with self._lock:
            if writer.instance in self._streams:
                raise ReplayError(f"stream already registered for {writer.instance!r}")
            self._streams[writer.instance] = writer

    def stream(self, instance: str) -> TelemetryStreamWriter | None:
        with self._lock:
            return self._streams.get(instance)

    # -- stages --------------------------------------------------------------

    def record_dispatch(
        self,
        *,
        instance: str,
        family: str,
        role: str,
        owner: str | None,
        search_id: str,
        command: str,
        dispatched_roots: tuple[str, ...],
        dispatched_ms: float,
        stage_index: int,
    ) -> StageRecord:
        with self._lock:
            self._dispatch_counter += 1
            record = StageRecord(
                stage_index=stage_index,
                instance=instance,
                family=family,
                role=role,
                owner=owner,
                search_id=search_id,
                command=command,
                dispatched_roots=tuple(dispatched_roots),
                dispatch_order=self._dispatch_counter,
                dispatched_ms=dispatched_ms,
            )
            self._stages.append(record)
            return record

    def record_completion(
        self,
        record: StageRecord,
        *,
        completed_ms: float,
        disposition: str,
        bestmove: str | None = None,
        stop_reason: str | None = None,
        failure: str | None = None,
    ) -> None:
        with self._lock:
            if record.disposition != "running":
                return
            self._completion_counter += 1
            record.completed_ms = completed_ms
            record.completion_order = self._completion_counter
            record.disposition = disposition
            record.bestmove = bestmove
            record.stop_reason = stop_reason
            record.failure = failure

    def note(self, message: str) -> None:
        with self._lock:
            if len(self.notes) < 64:
                self.notes.append(message)

    # -- finalization --------------------------------------------------------

    def finalize(self, *, disposition: str, stop_reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self.finalized:
                return json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.finalized = True
            self.run_disposition = disposition
            self.run_stop_reason = stop_reason
            streams = list(self._streams.values())
            stages = list(self._stages)

        # A stage still marked running at finalization is unresolved evidence.
        # It is never silently recorded as a completed observation.
        for record in stages:
            if record.disposition == "running":
                record.disposition = "unresolved"
                if record.stop_reason is None:
                    record.stop_reason = stop_reason or "run finalized before completion"

        for writer in streams:
            writer.close()

        stream_records = [writer.snapshot() for writer in streams]
        stream_records.sort(key=lambda item: item["instance"])

        manifest: dict[str, Any] = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "run_id": self.run_id,
            "generation": self.generation,
            "created_utc": self.created_utc,
            "controller": {
                "mode": self.mode,
                "telemetry_execution_mode": self.telemetry_execution_mode,
                "config_path": self.config_path,
                "config_sha256": self.config_sha256,
                "partition_method": self.partition_method,
            },
            "position": {
                "position_id": self.position.position_id,
                "variant": self.position.variant,
                "move_encoding": "uci" if self.position.variant == "standard" else "uci_chess960",
                "base_fen": self.position.base_fen,
                "moves": list(self.position.moves),
                "command": self.position.command(),
            },
            "external_request": {
                "command": self.external_go_command,
                "request": parse_go_request(self.external_go_command),
            },
            "engines": self.engine_identities,
            "legal_root_oracle": {
                "instance": self.oracle_instance,
                "root_count": self.oracle_root_count,
                "terminal_universe": self.terminal_universe,
            },
            "ledger": {
                "owners": list(self.ledger_owners),
                "owner_roots": {owner: list(moves) for owner, moves in sorted(self.owner_roots.items())},
                "pre_dispatch_snapshot": self.pre_ledger_snapshot,
                "post_run_snapshot": self.post_ledger_snapshot,
            },
            "stages": [record.snapshot() for record in stages],
            "streams": stream_records,
            "shadow_health": self.shadow_health,
            "disposition": {
                "run": self.run_disposition,
                "stop_reason": self.run_stop_reason,
            },
            "notes": list(self.notes),
        }

        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        (self.run_dir / "manifest.json").write_text(payload, encoding="utf-8")
        return manifest


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = Path(run_dir) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayError(f"cannot load replay manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ReplayError("replay manifest root must be an object")
    version = manifest.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != REPLAY_SCHEMA_VERSION:
        raise ReplayError(f"unsupported replay manifest schema_version: {version!r}")
    return manifest


def verify_bundle_integrity(run_dir: Path) -> list[str]:
    """Return a list of integrity problems; empty means the bundle verifies."""
    run_dir = Path(run_dir)
    manifest = load_manifest(run_dir)
    problems: list[str] = []
    for record in manifest.get("streams", []):
        path = run_dir / record["path"]
        if not path.is_file():
            problems.append(f"missing stream file: {record['path']}")
            continue
        actual = sha256_file(path)
        if actual != record["sha256"]:
            problems.append(
                f"stream hash mismatch for {record['path']}: "
                f"manifest={record['sha256']}, actual={actual}"
            )
        if path.stat().st_size != record["bytes"]:
            problems.append(f"stream size mismatch for {record['path']}")
    return problems
