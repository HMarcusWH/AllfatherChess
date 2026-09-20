"""Shadow execution: concurrent restricted evidence collection under one anchor.

Authority firewall
------------------
`stockfish-anchor` is the only outward decision authority. Nothing in this
module may change, delay, replace, vote on, or constrain the anchor's move.
Shadow workers exist to produce replayable evidence.

Ownership firewall
------------------
The `RootShardLedger` governs the three shadow exploration owners only. The
unrestricted anchor holds no shard, so its independent traversal of a root also
owned by a shadow worker is not an ownership violation.

Observation firewall
--------------------
In `shadow` mode the root partition is deterministic instrumentation:
`root_index % owner_count`. No score, prior, residual, or historical result may
influence it. Only `active` mode may attach a routing policy, and even there the
policy governs shadow compute allocation, never the outward move.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from adapters.telemetry import (
    Lc0TelemetryAdapter,
    RecklessTelemetryAdapter,
    StockfishTelemetryAdapter,
)
from common.search_request import (
    PositionRequest,
    SearchRequestError,
    build_go_command,
    parse_go_request,
    parse_position_command,
)
from controller.replay import ReplayRun, StageRecord, TelemetryStreamWriter
from controller.runtime import BackendManager, RuntimeError as ControllerRuntimeError
from controller.shards import RootShardLedger, ShardLedgerError


_ADAPTERS = {
    "stockfish": StockfishTelemetryAdapter,
    "reckless": RecklessTelemetryAdapter,
    "lc0": Lc0TelemetryAdapter,
}


class ShadowRouter(Protocol):
    """Optional active-mode policy hook.

    The coordinator supplies observations and executes authorized actions. It
    never asks the router about the outward move.
    """

    def on_run_start(self, context: "RunContext") -> None:
        ...

    def on_checkpoint(self, context: "RunContext") -> "list[RouterCommand]":
        ...

    def on_run_end(self, context: "RunContext") -> None:
        ...

    @property
    def checkpoint_interval_s(self) -> float:
        ...


@dataclass(frozen=True)
class RouterCommand:
    """One authorized action against one shadow worker."""

    action: str  # "stop_worker" | "extend" | "hold"
    owner: str
    extend_limit: dict[str, Any] | None = None
    reason: str = ""


def partition_roots(roots: tuple[str, ...], owners: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Deterministic, policy-free observation partition.

    `root_index % owner_count` is instrumentation, not routing. It intentionally
    ignores scores, priors, history, and residuals so the collected evidence is
    not contaminated by the hypothesis it is meant to test.
    """
    if not owners:
        raise ShardLedgerError("observation partition requires at least one owner")
    buckets: dict[str, list[str]] = {owner: [] for owner in owners}
    for index, move in enumerate(roots):
        buckets[owners[index % len(owners)]].append(move)
    return {owner: tuple(buckets[owner]) for owner in owners}


@dataclass
class _OwnerState:
    owner: str
    instance: str
    family: str
    roots: tuple[str, ...]
    stream: TelemetryStreamWriter
    stage: StageRecord | None = None
    stage_index: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    dispatched: bool = False
    failed: bool = False
    stopped_by_policy: bool = False
    stages_dispatched: int = 0
    last_bestmove: str | None = None


@dataclass
class RunContext:
    """Read-only-ish view handed to an active-mode router."""

    run_id: str
    generation: int
    run_dir: Path
    mode: str
    position: PositionRequest
    external_go_command: str
    owners: tuple[str, ...]
    owner_roots: dict[str, tuple[str, ...]]
    started_monotonic: float
    _coordinator: "ShadowRunCoordinator"

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_monotonic) * 1000.0

    def owner_stream_path(self, owner: str) -> Path | None:
        state = self._coordinator._owner_state(self.generation, owner)
        return None if state is None else state.stream.path

    def owner_active(self, owner: str) -> bool:
        state = self._coordinator._owner_state(self.generation, owner)
        return state is not None and state.dispatched and not state.done.is_set()

    def owner_stages(self, owner: str) -> int:
        state = self._coordinator._owner_state(self.generation, owner)
        return 0 if state is None else state.stages_dispatched

    def active_owners(self) -> tuple[str, ...]:
        return tuple(owner for owner in self.owners if self.owner_active(owner))


@dataclass
class _ActiveRun:
    generation: int
    run: ReplayRun
    context: RunContext
    owners: dict[str, _OwnerState] = field(default_factory=dict)
    anchor_stage: StageRecord | None = None
    anchor_stream: TelemetryStreamWriter | None = None
    ledger: RootShardLedger | None = None
    cancelled: bool = False
    cancel_reason: str | None = None
    worker: threading.Thread | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    anchor_done: threading.Event = field(default_factory=threading.Event)
    anchor_completed: threading.Event = field(default_factory=threading.Event)
    started_monotonic: float = 0.0


class ShadowRunCoordinator:
    """Own one shadow run per external search generation."""

    def __init__(
        self,
        runtime: BackendManager,
        *,
        router: ShadowRouter | None = None,
        diagnostic: Callable[[str], None] | None = None,
    ) -> None:
        if runtime.config.shadow is None:
            raise ControllerRuntimeError("shadow coordination requires shadow/active mode configuration")
        self.runtime = runtime
        self.settings = runtime.config.shadow
        self.router = router
        self._diagnostic = diagnostic or (lambda message: None)
        self._lock = threading.RLock()
        self._run: _ActiveRun | None = None
        self._closed = False
        self._config_sha = self._hash_config(runtime.config.path)
        self._history: list[Path] = []
        runtime.set_instance_observer(self._observe_line)
        runtime.set_shadow_exit_handler(self._on_shadow_exit)

    # ------------------------------------------------------------------
    # identity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_config(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:  # pragma: no cover - config was already parsed once
            return ""

    def _engine_identities(self) -> dict[str, Any]:
        identities: dict[str, Any] = {}
        for name in sorted(self.runtime.config.backends):
            spec = self.runtime.config.backends[name]
            identities[name] = {
                "engine": spec.family,
                "role": spec.role,
                "binary": str(spec.binary),
                "binary_sha256": self._hash_binary(spec.binary),
                "args": list(spec.args),
                "options": dict(spec.options),
            }
        return identities

    @staticmethod
    def _hash_binary(path: Path) -> str | None:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:  # pragma: no cover - identity is best effort
            return None

    def _variant(self) -> str:
        return "chess960" if self.runtime.chess960 else "standard"

    def _adapter_factory(self, *, family: str, instance: str, position_id: str, variant: str):
        adapter_cls = _ADAPTERS[family]
        score_type = self.settings.lc0_score_type

        def factory(search_id: str):
            kwargs: dict[str, Any] = {
                "search_id": search_id,
                "engine_instance": instance,
                "position_id": position_id,
                "variant": variant,
            }
            if family == "lc0":
                kwargs["score_type"] = score_type
            return adapter_cls(**kwargs)

        return factory

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------

    @property
    def last_run_dir(self) -> Path | None:
        with self._lock:
            return self._history[-1] if self._history else None

    def prepare_run(self, *, generation: int, go_command: str) -> bool:
        """Create the run bundle and anchor stream *before* the anchor starts.

        This does directory and file work only. It performs no engine IO, so it
        cannot delay the outward search.
        """
        with self._lock:
            if self._closed:
                return False
            if self._run is not None and not self._run.finished.is_set():
                # Defensive: the frontend rejects overlapping searches.
                self._cancel_locked(self._run, reason="superseded")
        position_command = self.runtime.position_command
        variant = self._variant()
        try:
            position = parse_position_command(
                position_command if position_command is not None else "position startpos",
                variant=variant,
            )
            parse_go_request(go_command)
        except SearchRequestError as exc:
            self._diagnostic(f"shadow run not started: {exc}")
            return False

        now = _dt.datetime.now(_dt.timezone.utc)
        run_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-g{generation:06d}-{uuid.uuid4().hex[:8]}"
        run_dir = self.settings.replay_root / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            self._diagnostic(f"shadow run directory unavailable: {exc}")
            return False

        started = time.monotonic()
        run = ReplayRun(
            run_id=run_id,
            generation=generation,
            run_dir=run_dir,
            mode=self.runtime.config.mode,
            telemetry_execution_mode=self.runtime.config.telemetry_execution_mode,
            position=position,
            external_go_command=go_command,
            config_path=str(self.runtime.config.path),
            config_sha256=self._config_sha,
            engine_identities=self._engine_identities(),
            ledger_owners=self.settings.owners,
            partition_method=self.settings.partition,
            created_utc=now.isoformat().replace("+00:00", "Z"),
        )
        run.oracle_instance = self.settings.oracle

        anchor_name = self.runtime.anchor_name
        anchor_spec = self.runtime.spec(anchor_name)
        anchor_stream = TelemetryStreamWriter(
            instance=anchor_name,
            family=anchor_spec.family,
            role=anchor_spec.role,
            path=run_dir / f"{anchor_name}.jsonl",
            adapter_factory=self._adapter_factory(
                family=anchor_spec.family,
                instance=anchor_name,
                position_id=position.position_id,
                variant=variant,
            ),
        )
        run.register_stream(anchor_stream)
        anchor_search_id = f"{run_id}:{anchor_name}:0"
        anchor_stream.begin_stage(
            search_id=anchor_search_id,
            position=position.telemetry_position(),
            request=parse_go_request(go_command),
            controller={
                # The anchor path is the unmodified baseline search. The run's
                # controller mode is recorded in the replay manifest instead.
                "execution_mode": "baseline",
                "instance_role": "anchor",
                "decision_authority": True,
            },
        )
        anchor_stage = run.record_dispatch(
            instance=anchor_name,
            family=anchor_spec.family,
            role=anchor_spec.role,
            owner=None,
            search_id=anchor_search_id,
            command=go_command,
            dispatched_roots=(),
            dispatched_ms=0.0,
            stage_index=0,
        )

        context = RunContext(
            run_id=run_id,
            generation=generation,
            run_dir=run_dir,
            mode=self.runtime.config.mode,
            position=position,
            external_go_command=go_command,
            owners=self.settings.owners,
            owner_roots={},
            started_monotonic=started,
            _coordinator=self,
        )
        active = _ActiveRun(
            generation=generation,
            run=run,
            context=context,
            anchor_stage=anchor_stage,
            anchor_stream=anchor_stream,
            started_monotonic=started,
        )
        with self._lock:
            self._run = active
            self._history.append(run_dir)
            if len(self._history) > 64:
                del self._history[:-64]
        return True

    def start_shadow_work(self, generation: int) -> None:
        """Start shadow qualification/dispatch after the anchor is already searching."""
        with self._lock:
            active = self._run
            if active is None or active.generation != generation or active.worker is not None:
                return
            worker = threading.Thread(
                target=self._run_worker,
                args=(active,),
                name=f"allfather-shadow-g{generation:06d}",
                daemon=True,
            )
            active.worker = worker
        worker.start()

    def abort_run(self, generation: int, *, reason: str) -> None:
        """Abandon a prepared run whose anchor dispatch failed."""
        with self._lock:
            active = self._run
            if active is None or active.generation != generation:
                return
            if active.worker is not None:
                self._cancel_locked(active, reason=reason)
                return
            self._run = None
        active.run.finalize(disposition="aborted", stop_reason=reason)
        active.finished.set()

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def _observe_line(self, instance: str, token: int, line: str, observed_monotonic: float) -> None:
        """Capture one dequeued engine line. Runs on that engine's reader thread."""
        with self._lock:
            active = self._run
            if active is None or active.generation != token:
                return
            stream = active.run.stream(instance)
            t0 = active.started_monotonic
        if stream is None:
            return
        stream.submit(line, max(0.0, (observed_monotonic - t0) * 1000.0))

    def note_anchor_complete(self, generation: int, line: str) -> None:
        """Record the anchor's outward completion and release the shadows."""
        with self._lock:
            active = self._run
            if active is None or active.generation != generation:
                return
            stage = active.anchor_stage
            elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
        if stage is not None:
            bestmove = line.split()[1] if line.startswith("bestmove ") and len(line.split()) > 1 else None
            active.run.record_completion(
                stage,
                completed_ms=elapsed,
                disposition="completed",
                bestmove=bestmove,
            )
        active.anchor_completed.set()
        active.anchor_done.set()
        # The outward answer is already emitted. No *new* observational stage
        # may be opened against a decision that has already been made. Whether
        # an in-flight node-limited stage is drained or killed is a declared
        # configuration choice, never an implicit one.
        if self.settings.on_anchor_complete == "cancel":
            self.cancel(generation, reason="anchor_complete")

    def _on_shadow_exit(self, instance: str, rc: int | None, token: int | None) -> None:
        with self._lock:
            active = self._run
            if active is None:
                return
            elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
            state = next((s for s in active.owners.values() if s.instance == instance), None)
        if state is None:
            return
        state.failed = True
        if state.stage is not None:
            active.run.record_completion(
                state.stage,
                completed_ms=elapsed,
                disposition="failed",
                failure=f"{instance} exited unexpectedly; rc={rc}",
            )
        active.run.note(f"shadow instance {instance} exited unexpectedly (rc={rc}); evidence only")
        state.done.set()

    def _owner_state(self, generation: int, owner: str) -> _OwnerState | None:
        with self._lock:
            active = self._run
            if active is None or active.generation != generation:
                return None
            return active.owners.get(owner)

    # ------------------------------------------------------------------
    # cancellation and draining
    # ------------------------------------------------------------------

    def cancel(self, generation: int | None = None, *, reason: str) -> None:
        with self._lock:
            active = self._run
            if active is None:
                return
            if generation is not None and active.generation != generation:
                return
            self._cancel_locked(active, reason=reason)

    def _cancel_locked(self, active: _ActiveRun, *, reason: str) -> None:
        if not active.cancelled:
            active.cancelled = True
            active.cancel_reason = reason
        states = list(active.owners.values())
        for state in states:
            if state.dispatched and not state.done.is_set():
                try:
                    self.runtime.stop_instance(state.instance)
                except ControllerRuntimeError:  # pragma: no cover - shadow stop is non-authoritative
                    pass

    def quiesce(self, *, timeout: float | None = None) -> bool:
        """Guarantee no prior shadow generation can observe the next state.

        Callers must invoke this before any position/game synchronization.
        """
        if timeout is None:
            timeout = self.settings.drain_timeout_s
        with self._lock:
            active = self._run
            if active is None:
                return True
            self._cancel_locked(active, reason="quiesce")
            worker = active.worker
            finished = active.finished
        if worker is not None:
            worker.join(timeout=timeout)
        drained = finished.wait(timeout=max(0.0, timeout))
        if not drained:
            self._diagnostic("shadow generation did not drain within the configured timeout")
        return drained

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.quiesce()
        self.runtime.set_instance_observer(None)
        self.runtime.set_shadow_exit_handler(None)

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------

    def _run_worker(self, active: _ActiveRun) -> None:
        disposition = "completed"
        stop_reason: str | None = active.cancel_reason
        try:
            disposition, stop_reason = self._execute(active)
        except Exception as exc:  # pragma: no cover - worker isolation
            active.run.note(f"shadow worker error: {type(exc).__name__}: {exc}")
            disposition, stop_reason = "error", f"{type(exc).__name__}: {exc}"
        finally:
            try:
                # Replay evidence is incomplete without the authority stream's
                # own completion, so finalization waits for it under a bound.
                if not active.anchor_done.is_set():
                    active.anchor_done.wait(timeout=self.settings.drain_timeout_s)
                if active.ledger is not None:
                    active.run.post_ledger_snapshot = active.ledger.snapshot()
                active.run.shadow_health = {
                    name: health.snapshot() for name, health in self.runtime.shadow_health().items()
                }
                if active.cancelled and stop_reason is None:
                    stop_reason = active.cancel_reason
                active.run.finalize(disposition=disposition, stop_reason=stop_reason)
            except Exception as exc:  # pragma: no cover - finalization isolation
                self._diagnostic(f"replay finalization failed: {type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    if self._run is active:
                        self._run = None
                active.finished.set()

    def _execute(self, active: _ActiveRun) -> tuple[str, str | None]:
        run = active.run
        settings = self.settings

        if active.cancelled:
            return "cancelled", active.cancel_reason

        # 1. Legal-root qualification on the dedicated shadow oracle.
        try:
            roots = self.runtime.legal_root_moves()
        except ControllerRuntimeError as exc:
            run.note(f"legal-root oracle unavailable: {exc}")
            return "oracle_failed", str(exc)
        run.oracle_root_count = len(roots)
        if not roots:
            run.terminal_universe = True
            run.note("terminal position: empty legal-root universe, no shadow dispatch")
            return "terminal_no_dispatch", None
        if active.cancelled:
            return "cancelled", active.cancel_reason

        # 2. Only healthy shadow instances may own exploration shards.
        owners = tuple(
            owner
            for owner in settings.owners
            if self.runtime.shadow_available(settings.instance(owner))
        )
        excluded = [owner for owner in settings.owners if owner not in owners]
        for owner in excluded:
            run.note(f"owner {owner} excluded: shadow instance unavailable before dispatch")
        if not owners:
            return "no_shadow_owner", "every shadow instance was unavailable"

        # 3. Ledger ownership: exact coverage, pairwise disjoint, one-shot.
        ledger = RootShardLedger(roots, owners=owners, generation=active.generation)
        partition = partition_roots(roots, owners)
        try:
            ledger.assign_partition(partition)
        except ShardLedgerError as exc:
            run.note(f"partition rejected: {exc}")
            return "partition_failed", str(exc)
        active.ledger = ledger
        run.ledger_owners = owners

        dispatchable: list[str] = []
        for owner in owners:
            if partition[owner]:
                ledger.activate_owner(owner)
                dispatchable.append(owner)
            else:
                run.note(f"owner {owner} holds an empty region and is not dispatched")
        run.pre_ledger_snapshot = ledger.snapshot()
        run.owner_roots = {owner: list(ledger.active_roots(owner)) for owner in dispatchable}
        active.context.owner_roots = {
            owner: tuple(ledger.active_roots(owner)) for owner in dispatchable
        }

        if active.cancelled:
            return "cancelled", active.cancel_reason

        # 4. Concurrent restricted dispatch.
        variant = active.context.position.variant
        position_id = active.context.position.position_id
        for owner in dispatchable:
            instance = settings.instance(owner)
            spec = self.runtime.spec(instance)
            stream = TelemetryStreamWriter(
                instance=instance,
                family=spec.family,
                role=spec.role,
                path=active.run.run_dir / f"{instance}.jsonl",
                adapter_factory=self._adapter_factory(
                    family=spec.family,
                    instance=instance,
                    position_id=position_id,
                    variant=variant,
                ),
            )
            run.register_stream(stream)
            active.owners[owner] = _OwnerState(
                owner=owner,
                instance=instance,
                family=spec.family,
                roots=tuple(ledger.active_roots(owner)),
                stream=stream,
            )

        if self.router is not None:
            self.router.on_run_start(active.context)

        for owner in dispatchable:
            self._dispatch_stage(active, active.owners[owner], limit=dict(settings.dispatch_limit))

        # 5. Wait for completion, running router checkpoints in active mode.
        self._await_completion(active)

        # 6. Seal owners whose region completed normally.
        for owner, state in active.owners.items():
            if state.dispatched and not state.failed:
                try:
                    ledger.seal_owner(owner)
                except ShardLedgerError as exc:  # pragma: no cover - defensive
                    run.note(f"owner {owner} could not be sealed: {exc}")

        if self.router is not None:
            self.router.on_run_end(active.context)

        if active.anchor_completed.is_set():
            run.note(
                "outward decision was emitted before shadow observation finished; "
                f"in-flight stages were handled with on_anchor_complete={settings.on_anchor_complete}"
            )
        if active.cancelled:
            return "cancelled", active.cancel_reason
        return "completed", None

    def _dispatch_stage(self, active: _ActiveRun, state: _OwnerState, *, limit: dict[str, Any]) -> bool:
        if active.cancelled or self._closed:
            return False
        if state.stage_index > 0 and active.anchor_completed.is_set():
            # Extension stages exist to inform a decision. Once the outward
            # decision is emitted there is nothing left for them to inform.
            active.run.note(
                f"owner {state.owner} extension suppressed: outward decision already emitted"
            )
            return False
        run = active.run
        search_id = f"{run.run_id}:{state.instance}:{state.stage_index}"
        try:
            command = build_go_command(limit=limit, searchmoves=state.roots)
        except SearchRequestError as exc:  # pragma: no cover - configuration is validated
            run.note(f"owner {state.owner} dispatch rejected: {exc}")
            state.done.set()
            return False

        state.stream.begin_stage(
            search_id=search_id,
            position=active.context.position.telemetry_position(),
            request=parse_go_request(command),
            controller={
                "execution_mode": self.runtime.config.telemetry_execution_mode,
                "phase": "EXPLORE",
                "instance_role": "shadow",
                "owner": state.owner,
                "decision_authority": False,
            },
        )
        elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
        stage = run.record_dispatch(
            instance=state.instance,
            family=state.family,
            role="shadow",
            owner=state.owner,
            search_id=search_id,
            command=command,
            dispatched_roots=state.roots,
            dispatched_ms=elapsed,
            stage_index=state.stage_index,
        )
        state.stage = stage
        state.done.clear()

        generation = active.generation

        def on_info(token: int, line: str) -> None:
            return None

        def on_complete(token: int, line: str) -> None:
            self._on_shadow_complete(generation, state.owner, token, line)

        dispatched = self.runtime.start_shadow_search(
            state.instance,
            command,
            token=generation,
            on_info=on_info,
            on_complete=on_complete,
        )
        if not dispatched:
            state.failed = True
            run.record_completion(
                stage,
                completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                disposition="failed",
                failure="shadow dispatch rejected; instance unavailable",
            )
            state.done.set()
            return False

        state.dispatched = True
        state.stages_dispatched += 1
        state.stage_index += 1
        return True

    def _on_shadow_complete(self, generation: int, owner: str, token: int, line: str) -> None:
        with self._lock:
            active = self._run
            if active is None or active.generation != generation or token != generation:
                return
            state = active.owners.get(owner)
            elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
        if state is None or state.stage is None:
            return
        tokens = line.split()
        bestmove = tokens[1] if line.startswith("bestmove ") and len(tokens) > 1 else None
        state.last_bestmove = bestmove
        disposition = "stopped" if (active.cancelled or state.stopped_by_policy) else "completed"
        stop_reason = None
        if state.stopped_by_policy:
            stop_reason = "route_stop_worker"
        elif active.cancelled:
            stop_reason = active.cancel_reason
        active.run.record_completion(
            state.stage,
            completed_ms=elapsed,
            disposition=disposition,
            bestmove=bestmove,
            stop_reason=stop_reason,
        )
        state.done.set()

    def _await_completion(self, active: _ActiveRun) -> None:
        interval = 0.02
        if self.router is not None:
            interval = max(0.005, float(self.router.checkpoint_interval_s))
        deadline = time.monotonic() + max(1.0, self.settings.drain_timeout_s * 4)
        while True:
            pending = [state for state in active.owners.values() if state.dispatched and not state.done.is_set()]
            if not pending:
                if self.router is None or active.cancelled:
                    return
                if not self._router_checkpoint(active):
                    return
                continue
            if time.monotonic() > deadline:
                for state in pending:
                    active.run.note(f"owner {state.owner} did not drain before the shadow deadline")
                self._cancel_locked(active, reason="drain_deadline")
                for state in pending:
                    state.done.wait(timeout=self.settings.drain_timeout_s)
                return
            for state in pending:
                state.done.wait(timeout=interval)
            if self.router is not None and not active.cancelled:
                self._router_checkpoint(active)

    def _router_checkpoint(self, active: _ActiveRun) -> bool:
        """Apply authorized router commands. Returns True if new work was dispatched."""
        if self.router is None or active.cancelled:
            return False
        try:
            commands = self.router.on_checkpoint(active.context)
        except Exception as exc:  # pragma: no cover - router isolation
            active.run.note(f"router checkpoint error: {type(exc).__name__}: {exc}")
            return False
        dispatched_any = False
        for command in commands:
            state = active.owners.get(command.owner)
            if state is None:
                continue
            if command.action == "stop_worker":
                if state.dispatched and not state.done.is_set():
                    state.stopped_by_policy = True
                    try:
                        self.runtime.stop_instance(state.instance)
                    except ControllerRuntimeError:  # pragma: no cover
                        pass
            elif command.action == "extend":
                if state.done.is_set() and not state.failed and not state.stopped_by_policy:
                    limit = command.extend_limit or dict(self.settings.dispatch_limit)
                    if self._dispatch_stage(active, state, limit=limit):
                        dispatched_any = True
        return dispatched_any
