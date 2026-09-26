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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from common.search_request import PositionRequest, parse_go_request
from common.telemetry import TelemetryError


REPLAY_SCHEMA_VERSION = 1

#: Bounded per-stream backlog. The reader thread must never block, so an
#: overflow is recorded as explicit truncation evidence instead of back-pressure.
_STREAM_QUEUE_MAXSIZE = 200_000

#: Upper bound on the in-memory live event view used by active routing. The
#: JSONL file is unaffected; only the live view is capped.
_TRACKED_EVENT_LIMIT = 50_000

_SENTINEL = object()


class ReplayError(RuntimeError):
    """Raised when replay evidence cannot be recorded honestly."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_bestmove(value: Any) -> Any:
    """Normalize UCI null-move spellings for manifest/telemetry comparison."""
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"0000", "(none)", "none", "a1a1"}:
            return None
        return lowered
    return value


def atomic_write_text(path: Path, payload: str) -> None:
    """Durably replace a text artifact without exposing a partial final file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


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
        track_events: bool = False,
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
        # Active mode reconstructs live features from the *same* events that are
        # written to disk, so an online decision and an offline analysis can
        # never disagree about what the engine reported.
        self._track_events = track_events
        self._events: list[dict[str, Any]] = []
        self._events_truncated = False
        # Enqueued-vs-applied watermark. Engine lines are timestamped on the
        # stdout reader thread and translated on the writer thread, so a line
        # can be received and carry an earlier `observed_ms` than a routing
        # checkpoint that never saw it. The JSONL will later show that event
        # before the checkpoint, and the online decision and the offline audit
        # then disagree about what the engine had reported.
        self._enqueued = 0
        self._applied = 0
        #: True once the handle is closed or being closed while the writer
        #: thread may still be running.
        self._abandoned = False
        #: Set by `close()` so the writer stops even when the bounded queue had
        #: no room for the sentinel.
        self._stopping = threading.Event()
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
        observed_ms: float = 0.0,
    ) -> None:
        """Open a search on this stream.

        `observed_ms` is this stage's run-relative dispatch time. Defaulting it
        to zero made every `search.started` claim the search began at run start,
        even for an extension dispatched seconds later, while its candidate and
        completion events carried the real controller clock -- an internally
        inconsistent timeline that no stage-duration audit could trust.
        """
        self._enqueue(("begin", search_id, position, request, controller, observed_ms))

    def submit(self, line: str, observed_ms: float) -> None:
        self._enqueue(("line", line, observed_ms))

    def _enqueue(self, item: Any) -> None:
        with self._lock:
            if self._closed:
                return
            # Count the item BEFORE publishing it. `put_nowait` makes the item
            # visible to the writer thread immediately, so incrementing
            # afterwards leaves a window in which the item is queued (or even
            # already applied) while `pending_events()` computes zero -- and a
            # routing checkpoint in that window would read "drained" with a
            # line still in flight. Counting first can only over-report, which
            # costs a conservative denial rather than an unsound stop.
            self._enqueued += 1
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._enqueued -= 1
                self._dropped += 1
            return
        size = self._queue.qsize()
        if size > self._queued_peak:
            self._queued_peak = size

    # -- consumer side (dedicated writer thread) ----------------------------

    def _drain(self) -> None:
        while True:
            if self._stopping.is_set() and self._queue.empty():
                # The sentinel could not be enqueued because the queue was
                # full. Shutdown may not depend on room in a bounded queue, so
                # the flag ends the loop once the backlog is gone.
                return
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                self._queue.task_done()
                return
            try:
                try:
                    self._apply(item)
                finally:
                    with self._lock:
                        self._applied += 1
            except Exception as exc:  # pragma: no cover - writer isolation
                with self._lock:
                    if len(self._errors) < 32:
                        self._errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()

    def _apply(self, item: Any) -> None:
        kind = item[0]
        if kind == "begin":
            _, search_id, position, request, controller, observed_ms = item
            adapter = self._adapter_factory(search_id)
            event = adapter.start(
                position=position,
                request=request,
                controller=controller,
                observed_ms=observed_ms,
            )
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
        with self._lock:
            if self._abandoned:
                # The handle is closed or about to be. Anything still in flight
                # is already counted as dropped evidence; writing it now would
                # raise into the writer thread and could corrupt a stream the
                # manifest has already hashed.
                return
        self._handle.write(json.dumps(event, sort_keys=True) + "\n")
        self._event_count += 1
        if self._track_events:
            with self._lock:
                if len(self._events) < _TRACKED_EVENT_LIMIT:
                    self._events.append(event)
                else:
                    # The live view stops growing here. Say so, so a consumer
                    # cannot mistake a frozen prefix for the current state of
                    # the search and decide from stale evidence.
                    self._events_truncated = True

    def tracked_events(self) -> list[dict[str, Any]]:
        """Copy of the events written so far; empty unless tracking is enabled."""
        with self._lock:
            return list(self._events)

    @property
    def tracked_events_truncated(self) -> bool:
        """True once the live event view has stopped tracking the stream."""
        with self._lock:
            return self._events_truncated

    def pending_events(self) -> int:
        """Lines received from the engine but not yet translated to disk."""
        with self._lock:
            return max(0, self._enqueued - self._applied)

    @property
    def evidence_lossy(self) -> bool:
        """True once this stream has lost evidence it can never recover.

        A dropped queue entry or a failed adapter translation removes an
        observation permanently. Once the queue has drained, `pending_events()`
        is zero again and the backlog gate passes, so without this a lost leader
        flip is indistinguishable from a stable search -- the one direction that
        authorizes suppression. The finalized manifest records the same thing as
        `contract_validatable: false`, but only after the run is over, which is
        far too late for the decision that used it.
        """
        with self._lock:
            return self._dropped > 0 or bool(self._errors)

    def live_evidence_faults(self) -> dict[str, Any]:
        """What this stream has lost, for the decision certificate."""
        with self._lock:
            return {
                "dropped_events": self._dropped,
                "adapter_errors": len(self._errors),
                "view_truncated": self._events_truncated,
            }

    def drain_barrier(self, timeout: float = 0.025) -> bool:
        """Best-effort wait for the writer thread to catch up.

        Returns True when nothing is in flight. The common case costs nothing:
        the writer keeps up, `pending_events()` is already zero, and this
        returns on the first check without sleeping. It is deliberately short --
        blocking the routing checkpoint on the writer thread trades a live
        decision deadline for bookkeeping, which is the worse failure. When the
        barrier expires with work still in flight the caller is expected to fail
        closed rather than decide from the prefix it can see.
        """
        if self.pending_events() == 0:
            return True
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self.pending_events() == 0:
                return True
            time.sleep(0.002)
        return self.pending_events() == 0

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
        # NOT a blocking `put`. On a full queue with a writer stalled in adapter
        # or filesystem work this waited without bound, before either timed
        # `join` below was reached, so the advertised close timeout bounded
        # nothing at all and a replay run could stay active forever.
        self._stopping.set()
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:  # pragma: no cover - the flag ends the loop instead
            pass
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            # The writer is still draining. Closing the handle now would make
            # every remaining event fail against a closed file, and
            # `snapshot()` would hash and describe a partial stream while
            # calling it complete. Give it one more full timeout, then record
            # what is still unwritten as lost evidence rather than pretend the
            # stream is whole.
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                with self._lock:
                    stranded = max(0, self._enqueued - self._applied)
                    if stranded:
                        self._dropped += stranded
                        self._applied += stranded
                    if len(self._errors) < 32:
                        self._errors.append(
                            f"telemetry writer did not drain within {2 * timeout}s; "
                            f"{stranded} event(s) were not written"
                        )
                    # Marking the queue dropped does not stop the consumer. If
                    # it resumes after this method closes the handle it writes
                    # into a closed file, and the snapshot taken immediately
                    # afterwards may hash a partial one. The writer checks this
                    # before every write.
                    self._abandoned = True
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except (OSError, ValueError):  # pragma: no cover - best-effort durability
            pass
        self._handle.close()

    def note_loss(self, reason: str) -> None:
        with self._lock:
            self._dropped += 1
            if len(self._errors) < 32:
                self._errors.append(reason)

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
            "live_view_truncated": self._events_truncated,
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
    #: Roots actually partitioned, which is smaller than the oracle count when
    #: the external request carried a `searchmoves` restriction.
    dispatch_root_count: int | None = None
    external_root_restriction: list[str] | None = None
    oracle_instance: str | None = None
    terminal_universe: bool = False
    finalized: bool = False
    _finalizing: bool = False
    _final_manifest: dict[str, Any] | None = field(default=None, repr=False)
    #: Controller work performed synchronously before the anchor was dispatched.
    prepare_ms: float = 0.0
    #: Controller work performed between run start and the first shadow dispatch.
    qualification_ms: float | None = None
    time_plan: dict[str, Any] | None = None
    clock_outcome: dict[str, Any] | None = None
    outward_decision: dict[str, Any] | None = None

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
                if self._final_manifest is None:
                    raise ReplayError("replay marked finalized without an in-memory manifest")
                return json.loads(json.dumps(self._final_manifest))
            if self._finalizing:
                raise ReplayError("replay finalization is already in progress")
            self._finalizing = True
            self.run_disposition = disposition
            self.run_stop_reason = stop_reason
            streams = list(self._streams.values())
            stages = list(self._stages)

        try:
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
                    "overhead": {
                        "prepare_ms": round(self.prepare_ms, 3),
                        "qualification_ms": (
                            None if self.qualification_ms is None else round(self.qualification_ms, 3)
                        ),
                    },
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
                    "dispatch_root_count": self.dispatch_root_count,
                    "external_root_restriction": self.external_root_restriction,
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

            if self.time_plan is not None:
                manifest["time_plan"] = self.time_plan
                manifest["clock_outcome"] = self.clock_outcome
            if self.outward_decision is not None:
                manifest["outward_decision"] = self.outward_decision
            payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

            atomic_write_text(self.run_dir / "manifest.json", payload)
        except Exception:
            with self._lock:
                self._finalizing = False
            raise

        with self._lock:
            self._final_manifest = manifest
            self.finalized = True
            self._finalizing = False
        return json.loads(json.dumps(manifest))


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


@dataclass(frozen=True)
class ReplaySkip:
    """One directory ignored by replay discovery, with an auditable reason."""

    path: Path
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path.name, "reason": self.reason}


@dataclass(frozen=True)
class ReplayDiscovery:
    """Manifest-qualified replay bundles plus every directory that was skipped."""

    bundles: tuple[Path, ...]
    skipped: tuple[ReplaySkip, ...]


def discover_replay_bundles(replay_root: Path | str) -> ReplayDiscovery:
    """Discover finalized replay bundles without treating every directory as evidence.

    A controller crash, SIGKILL, full disk, or interrupted setup can leave a
    directory behind without a manifest. Those directories are not replay
    bundles and must not poison an otherwise valid corpus. They are reported
    explicitly instead of being silently ignored.

    Discovery validates only the replay manifest shape/version. Stream hashes
    remain the responsibility of :func:`verify_bundle_integrity` and derived
    extraction, so discovery never upgrades an unverified bundle into trusted
    evidence.
    """

    root = Path(replay_root)
    if not root.exists():
        return ReplayDiscovery(bundles=(), skipped=())
    if not root.is_dir():
        raise ReplayError(f"replay root is not a directory: {root}")

    bundles: list[Path] = []
    skipped: list[ReplaySkip] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            skipped.append(ReplaySkip(path=path, reason="missing manifest.json"))
            continue
        try:
            load_manifest(path)
        except ReplayError as exc:
            skipped.append(ReplaySkip(path=path, reason=f"invalid manifest: {exc}"))
            continue
        bundles.append(path)

    return ReplayDiscovery(bundles=tuple(bundles), skipped=tuple(skipped))


def verify_bundle_integrity(run_dir: Path) -> list[str]:
    """Return hash/size/semantic integrity problems for a finalized replay."""
    run_dir = Path(run_dir)
    manifest = load_manifest(run_dir)
    from controller.online_time import verify_time_manifest
    problems: list[str] = verify_time_manifest(manifest)

    stages = manifest.get("stages", [])
    stage_by_search: dict[str, dict[str, Any]] = {}
    for stage in stages:
        if not isinstance(stage, dict):
            problems.append("manifest stage entry is not an object")
            continue
        search_id = stage.get("search_id")
        if not isinstance(search_id, str) or not search_id:
            problems.append("manifest stage is missing a valid search_id")
            continue
        if search_id in stage_by_search:
            problems.append(f"duplicate manifest stage search_id: {search_id}")
        stage_by_search[search_id] = stage

    declared_stream_searches: set[str] = set()
    observed_searches: set[str] = set()

    for record in manifest.get("streams", []):
        if not isinstance(record, dict):
            problems.append("manifest stream entry is not an object")
            continue
        rel = record.get("path")
        if not isinstance(rel, str) or not rel or Path(rel).name != rel:
            problems.append(f"invalid stream path in manifest: {rel!r}")
            continue
        path = run_dir / rel
        if not path.is_file():
            problems.append(f"missing stream file: {rel}")
            continue
        actual = sha256_file(path)
        if actual != record.get("sha256"):
            problems.append(
                f"stream hash mismatch for {rel}: "
                f"manifest={record.get('sha256')}, actual={actual}"
            )
        if path.stat().st_size != record.get("bytes"):
            problems.append(f"stream size mismatch for {rel}")

        declared_ids = record.get("search_ids") or []
        if not isinstance(declared_ids, list) or not all(
            isinstance(item, str) and item for item in declared_ids
        ):
            problems.append(f"stream {rel} has malformed search_ids")
            declared_ids = []
        for search_id in declared_ids:
            if search_id in declared_stream_searches:
                problems.append(f"search_id declared by multiple streams: {search_id}")
            declared_stream_searches.add(search_id)

        started: dict[str, dict[str, Any]] = {}
        completed: dict[str, dict[str, Any]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            problems.append(f"cannot read stream {rel}: {exc}")
            continue

        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{rel}:{line_no}: malformed JSON: {exc}")
                continue
            if not isinstance(event, dict):
                problems.append(f"{rel}:{line_no}: event is not an object")
                continue
            search_id = event.get("search_id")
            if not isinstance(search_id, str) or not search_id:
                problems.append(f"{rel}:{line_no}: event missing search_id")
                continue
            observed_searches.add(search_id)
            if event.get("engine_instance") != record.get("instance"):
                problems.append(
                    f"{rel}:{line_no}: engine_instance does not match stream manifest"
                )
            if event.get("engine") != record.get("engine"):
                problems.append(f"{rel}:{line_no}: engine does not match stream manifest")
            if event.get("position_id") != manifest.get("position", {}).get("position_id"):
                problems.append(f"{rel}:{line_no}: position_id does not match replay manifest")
            event_type = event.get("event_type")
            if event_type == "search.started":
                if search_id in started:
                    problems.append(f"{rel}: duplicate search.started for {search_id}")
                started[search_id] = event
            elif event_type == "search.complete":
                if search_id in completed:
                    problems.append(f"{rel}: duplicate search.complete for {search_id}")
                completed[search_id] = event

        if set(started) != set(declared_ids):
            problems.append(
                f"stream {rel} search.started ids {sorted(started)} do not match "
                f"manifest search_ids {sorted(declared_ids)}"
            )

        for search_id, event in started.items():
            stage = stage_by_search.get(search_id)
            if stage is None:
                problems.append(f"stream {rel} contains undeclared search_id {search_id}")
                continue
            if stage.get("instance") != record.get("instance"):
                problems.append(f"{search_id}: stage instance does not match stream")
            if stage.get("engine") != record.get("engine"):
                problems.append(f"{search_id}: stage engine does not match stream")
            controller = event.get("controller") or {}
            owner = controller.get("owner")
            if stage.get("owner") != owner:
                problems.append(f"{search_id}: stage owner does not match search.started")
            request = event.get("request") or {}
            if manifest.get("time_plan") is not None and stage.get("role") == "anchor":
                if request != parse_go_request(stage.get("command", "")):
                    problems.append(f"{search_id}: clock anchor telemetry request mismatch")
            event_roots = list(request.get("root_moves") or [])
            expected_roots = list(stage.get("dispatched_roots") or [])
            if stage.get("role") == "shadow" and event_roots != expected_roots:
                problems.append(
                    f"{search_id}: dispatched roots do not match search.started request"
                )

        for search_id, event in completed.items():
            stage = stage_by_search.get(search_id)
            if stage is None:
                continue
            if _semantic_bestmove(stage.get("bestmove")) != _semantic_bestmove(
                event.get("bestmove")
            ):
                problems.append(f"{search_id}: stage bestmove does not match search.complete")

    stage_ids = set(stage_by_search)
    # A dispatched stage is allowed to have no surviving stream record. Replay
    # schema v1 treats that as explicit missing evidence: load_bundle() reports
    # the instance in missing_streams and downstream extraction must not impute
    # observations. What is *not* allowed is the inverse -- a stream claiming a
    # search the orchestration manifest never declared.
    extra = sorted(declared_stream_searches - stage_ids)
    if extra:
        problems.append(f"stream search_ids missing from stages: {extra}")
    if observed_searches - stage_ids:
        problems.append(
            f"telemetry contains undeclared search_ids: {sorted(observed_searches - stage_ids)}"
        )
    return problems
