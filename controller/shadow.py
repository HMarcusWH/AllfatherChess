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

    def authorize_initial(self, context: "RunContext", owner: str) -> bool:
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
    stream: TelemetryStreamWriter | None = None
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
        if state is None or state.stream is None:
            return None
        return state.stream.path

    def owner_active(self, owner: str) -> bool:
        state = self._coordinator._owner_state(self.generation, owner)
        return state is not None and state.dispatched and not state.done.is_set()

    def owner_stages(self, owner: str) -> int:
        state = self._coordinator._owner_state(self.generation, owner)
        return 0 if state is None else state.stages_dispatched

    def active_owners(self) -> tuple[str, ...]:
        return tuple(owner for owner in self.owners if self.owner_active(owner))

    def dispatchable_owners(self) -> tuple[str, ...]:
        """Owners that actually hold a dispatch state for this run.

        An owner excluded for an unhealthy process, or assigned an empty root
        region, never gets an entry. It would otherwise look merely idle to the
        router, which could reserve an extension the coordinator silently drops.
        """
        state = self._coordinator._run_snapshot(self.generation)
        if state is None:
            return ()
        return tuple(owner for owner in self.owners if owner in state)

    def owner_instance(self, owner: str) -> str:
        state = self._coordinator._owner_state(self.generation, owner)
        if state is not None:
            return state.instance
        return self._coordinator.settings.instance_by_owner.get(owner, owner)

    def owner_family(self, owner: str) -> str:
        state = self._coordinator._owner_state(self.generation, owner)
        return owner if state is None else state.family

    def owner_last_stage_ms(self, owner: str) -> float | None:
        """Measured duration of this worker's most recent finished stage."""
        state = self._coordinator._owner_state(self.generation, owner)
        if state is None or state.stage is None:
            return None
        stage = state.stage
        if stage.completed_ms is None:
            return None
        return max(0.0, stage.completed_ms - stage.dispatched_ms)

    def owner_events(self, owner: str) -> list[dict[str, Any]]:
        """Telemetry events written so far for this worker.

        The router reconstructs live features from exactly the events that have
        landed on disk. That is not the same as everything the engine has
        reported: a line already received and timestamped on the reader thread
        may still be queued for translation, and it will appear in the JSONL
        with a timestamp earlier than a checkpoint that never saw it. Callers
        must read `owner_events_pending` alongside this and refuse to suppress
        while the two disagree.
        """
        state = self._coordinator._owner_state(self.generation, owner)
        if state is None or state.stream is None:
            return []
        return state.stream.tracked_events()

    def owner_events_truncated(self, owner: str) -> bool:
        """True when this worker's live event view has stopped tracking."""
        state = self._coordinator._owner_state(self.generation, owner)
        if state is None or state.stream is None:
            return False
        return state.stream.tracked_events_truncated

    def anchor_threads(self) -> int:
        """Declared `Threads` option for the outward anchor, defaulting to 1."""
        try:
            spec = self._coordinator.runtime.spec(self._coordinator.runtime.anchor_name)
        except Exception:  # pragma: no cover - defensive
            return 1
        try:
            return max(1, int((spec.options or {}).get("Threads", 1)))
        except (TypeError, ValueError):
            return 1

    def owner_threads(self, owner: str) -> int:
        """Declared `Threads` option for this worker's engine, defaulting to 1."""
        instance = self.owner_instance(owner)
        try:
            spec = self._coordinator.runtime.spec(instance)
        except Exception:  # pragma: no cover - defensive
            return 1
        value = (spec.options or {}).get("Threads", 1)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    def owner_evidence_lossy(self, owner: str) -> bool:
        """True when this worker's stream has already lost evidence."""
        state = self._coordinator._owner_state(self.generation, owner)
        if state is None or state.stream is None:
            return False
        return state.stream.evidence_lossy

    def owner_events_pending(self, owner: str, *, barrier_s: float = 0.025) -> int:
        """Lines received from this worker but not yet translated to disk.

        Applies a short drain barrier first, so the ordinary case -- where the
        writer thread is keeping up -- reports zero without the caller having to
        distinguish a real backlog from the microsecond between `put` and
        `get`.
        """
        state = self._coordinator._owner_state(self.generation, owner)
        if state is None or state.stream is None:
            return 0
        state.stream.drain_barrier(barrier_s)
        return state.stream.pending_events()

    def owner_elapsed_stage_ms(self, owner: str) -> float | None:
        """Time since the current stage was dispatched, for an unfinished stage."""
        state = self._coordinator._owner_state(self.generation, owner)
        if state is None or state.stage is None:
            return None
        stage = state.stage
        if stage.completed_ms is not None:
            return max(0.0, stage.completed_ms - stage.dispatched_ms)
        elapsed = (time.monotonic() - self.started_monotonic) * 1000.0
        return max(0.0, elapsed - stage.dispatched_ms)


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
    #: True while the legal-root oracle request is outstanding. Owner states do
    #: not exist yet at that point, so without this the quiesce barrier sees no
    #: worker to fail and lets the caller synchronize state into a process still
    #: answering the previous generation's `go perft 1`.
    qualifying: bool = False
    #: Router lifecycle guards. `_execute` can return before either call, and a
    #: run that dispatched no shadow work still spent the anchor's compute.
    router_started: bool = False
    router_finished: bool = False


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
        # Identities are taken from the runtime, which captured them when the
        # configuration was loaded -- before any engine process was started, so
        # they describe the bytes the running processes actually came from.
        # They are also hashed exactly once: hashing per run would add hundreds
        # of milliseconds to `prepare_run`, which runs synchronously before the
        # outward anchor search starts.
        self._config_sha = getattr(runtime, "config_sha256", "") or self._hash_config(
            runtime.config.path
        )
        self._engine_identity = getattr(runtime, "engine_identity", None) or (
            self._build_engine_identities()
        )
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

    def _build_engine_identities(self) -> dict[str, Any]:
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

    def _make_run_dir_within_budget(self, run_dir: Path) -> bool:
        """Create the run directory without letting the filesystem block a search.

        A blocked or very slow `replay_root` would otherwise hold the calling
        thread -- and therefore the outward anchor -- for as long as the kernel
        takes. The work happens on a short-lived thread and is abandoned at the
        declared budget. An abandoned thread may still create the directory
        afterwards; nothing references it, so it is inert leftover rather than
        state this run relies on.
        """
        budget = max(0.001, float(self.settings.prepare_budget_s))
        outcome: dict[str, Any] = {}

        def _make() -> None:
            try:
                run_dir.mkdir(parents=True, exist_ok=False)
                outcome["ok"] = True
            except OSError as exc:
                outcome["error"] = str(exc)

        worker = threading.Thread(
            target=_make, name=f"allfather-prepare-{run_dir.name}", daemon=True
        )
        worker.start()
        worker.join(timeout=budget)
        if worker.is_alive():
            self._diagnostic(
                f"replay setup exceeded its {budget}s pre-anchor budget; this search "
                "runs without a shadow bundle rather than delaying the outward decision"
            )
            return False
        if "error" in outcome:
            self._diagnostic(f"shadow run directory unavailable: {outcome['error']}")
            return False
        return bool(outcome.get("ok"))

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

        This performs no engine IO, but it does perform **filesystem** IO -- a
        `mkdir`, a file open and a writer-thread start -- and the frontend calls
        it before `start_anchor_search`. On a slow or blocked `replay_root` that
        is observational infrastructure delaying decision authority, which the
        authority firewall does not permit. The filesystem portion therefore
        runs off the calling thread under `shadow.prepare_budget_s`: past that
        bound the search proceeds with no bundle rather than waiting.
        """
        with self._lock:
            if self._closed:
                return False
            previous = self._run
            superseded = previous is not None and not previous.finished.is_set()

        if superseded:
            # A prior generation may still be draining: the anchor can return
            # while its shadows run on, and the next `go` needs no intervening
            # `position`. Replacing `_run` without waiting would orphan the old
            # worker, whose drain deadline could then call stop_instance on a
            # process the new generation is already using. Joining must happen
            # outside self._lock, because the worker needs that same lock to
            # finish.
            if not self.quiesce(reason="superseded"):
                self._diagnostic(
                    "previous shadow generation did not drain; skipping shadow "
                    "observation for this search"
                )
                return False

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

        prepare_started = time.monotonic()
        now = _dt.datetime.now(_dt.timezone.utc)
        run_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-g{generation:06d}-{uuid.uuid4().hex[:8]}"
        run_dir = self.settings.replay_root / run_id
        if not self._make_run_dir_within_budget(run_dir):
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
            engine_identities=self._engine_identity,
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
            observed_ms=(time.monotonic() - started) * 1000.0,
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
        # Controller overhead is recorded, never hidden. This is the only work
        # that happens between the external `go` and the anchor dispatch.
        run.prepare_ms = (time.monotonic() - prepare_started) * 1000.0
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

    def _run_snapshot(self, generation: int) -> dict[str, _OwnerState] | None:
        with self._lock:
            active = self._run
            if active is None or active.generation != generation:
                return None
            return dict(active.owners)

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

    def quiesce(self, *, timeout: float | None = None, reason: str = "quiesce") -> bool:
        """Guarantee no prior shadow generation can observe the next state.

        Callers must invoke this before any position/game synchronization.

        If a worker ignores `stop` and misses the drain deadline, returning a
        bare False would leave the caller free to synchronize state into a
        process still executing the previous generation. Instead the offending
        instances are recorded as shadow failures, which removes them from
        synchronization and dispatch through the existing observational-health
        path: a stuck worker degrades to evidence, exactly like a crashed one.
        """
        if timeout is None:
            timeout = self.settings.drain_timeout_s
        with self._lock:
            active = self._run
            if active is None:
                return True
            self._cancel_locked(active, reason=reason)
            worker = active.worker
            finished = active.finished
        if worker is not None:
            worker.join(timeout=timeout)
        drained = finished.wait(timeout=max(0.0, timeout))
        if not drained:
            self._diagnostic("shadow generation did not drain within the configured timeout")
            stuck = [
                state
                for state in list(active.owners.values())
                if state.dispatched and not state.done.is_set()
            ]
            for state in stuck:
                message = (
                    f"shadow instance {state.instance} did not drain within "
                    f"{timeout}s and is excluded from further synchronization"
                )
                self.runtime.record_shadow_failure(
                    state.instance, message, generation=active.generation
                )
                active.run.note(message)
                if state.stage is not None:
                    active.run.record_completion(
                        state.stage,
                        completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                        disposition="failed",
                        failure=message,
                    )
                # A worker recorded as failed is no longer awaited. Without
                # this the run's worker thread would stay blocked until its own
                # drain deadline, so the bundle -- including the evidence of the
                # failure itself -- would not be written for many seconds.
                state.done.set()
            if active.qualifying:
                # No owner state exists yet while the oracle is answering, so
                # the loop above found nothing to fail. The oracle is still
                # running the previous generation's request, and the caller is
                # about to synchronize the next position into it.
                oracle = self.settings.oracle
                message = (
                    f"legal-root oracle {oracle} did not return within {timeout}s and is "
                    "excluded from further synchronization"
                )
                self.runtime.record_shadow_failure(
                    oracle, message, generation=active.generation
                )
                active.run.note(message)
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
                # own completion, so finalization waits for it.
                #
                # The shadow drain timeout is NOT an authority deadline. Shadow
                # stages are node-limited and finish in well under a second; a
                # `go movetime 60000` anchor does not. Bounding this wait by
                # `drain_timeout_s` closed the anchor stream and cleared the run
                # while the outward search was still going, so the anchor's own
                # `bestmove` had nowhere to land and a normally completed search
                # was recorded with an unresolved authority stage.
                #
                # Wait for as long as the anchor can still answer: it is alive
                # and this coordinator is open. Both are polled rather than
                # assumed, so a dead anchor or a closing controller releases the
                # worker within one interval instead of hanging it.
                while not active.anchor_done.is_set():
                    if active.anchor_done.wait(timeout=0.25):
                        break
                    if self._closed:
                        active.run.note(
                            "controller closed before the anchor reported completion; "
                            "the authority stream is incomplete"
                        )
                        break
                    if not self.runtime.healthy:
                        active.run.note(
                            "authority health failed before the anchor reported completion; "
                            "the authority stream is incomplete"
                        )
                        break
                # Now that the anchor has answered (or is never going to), the
                # router's run can be closed against the whole elapsed time.
                # Every qualification failure -- a terminal position, a dead
                # oracle, an external `searchmoves` leaving no shadow root --
                # returns from `_execute` before the run was ever opened, and
                # the anchor still spent its compute, so a run that observed
                # nothing still owes an audit certificate.
                if self.router is not None and not active.router_finished:
                    try:
                        self._router_start(active)
                        self._router_end(active)
                    except Exception as exc:  # pragma: no cover - router isolation
                        active.run.note(
                            f"router finalization error: {type(exc).__name__}: {exc}"
                        )
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

    def _router_start(self, active: _ActiveRun) -> None:
        """Open the router's run, at most once per run."""
        if self.router is None or active.router_started:
            return
        active.router_started = True
        self.router.on_run_start(active.context)

    def _router_end(self, active: _ActiveRun) -> None:
        """Close the router's run, at most once per run."""
        if self.router is None or active.router_finished:
            return
        active.router_finished = True
        self.router.on_run_end(active.context)

    def _execute(self, active: _ActiveRun) -> tuple[str, str | None]:
        run = active.run
        settings = self.settings

        if active.cancelled:
            return "cancelled", active.cancel_reason

        # 1. Legal-root qualification on the dedicated shadow oracle.
        with self._lock:
            active.qualifying = True
        try:
            roots = self.runtime.legal_root_moves()
        except ControllerRuntimeError as exc:
            run.note(f"legal-root oracle unavailable: {exc}")
            return "oracle_failed", str(exc)
        finally:
            with self._lock:
                active.qualifying = False
        run.oracle_root_count = len(roots)
        if not roots:
            run.terminal_universe = True
            run.note("terminal position: empty legal-root universe, no shadow dispatch")
            return "terminal_no_dispatch", None

        # The anchor searches only what the caller asked for. If the external
        # request carries `searchmoves`, shadow evidence must describe the same
        # request; partitioning roots the caller excluded would make the streams
        # and the manifest describe two different searches.
        requested = parse_go_request(active.context.external_go_command).get("root_moves")
        if requested:
            allowed = set(requested)
            restricted = tuple(move for move in roots if move in allowed)
            unknown = sorted(allowed - set(roots))
            if unknown:
                run.note(f"external searchmoves named roots outside the legal universe: {unknown}")
            run.external_root_restriction = sorted(allowed)
            run.note(
                f"external searchmoves restricted the shadow universe from "
                f"{len(roots)} to {len(restricted)} roots"
            )
            roots = restricted
            if not roots:
                run.note("external searchmoves left no legal root to observe")
                return "external_restriction_empty", None
        run.dispatch_root_count = len(roots)

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

        run.qualification_ms = (time.monotonic() - active.started_monotonic) * 1000.0

        # 4. Concurrent restricted dispatch.
        for owner in dispatchable:
            instance = settings.instance(owner)
            spec = self.runtime.spec(instance)
            active.owners[owner] = _OwnerState(
                owner=owner,
                instance=instance,
                family=spec.family,
                roots=tuple(ledger.active_roots(owner)),
            )

        self._router_start(active)

        for owner in dispatchable:
            state = active.owners[owner]
            if self.router is not None:
                try:
                    authorized = self.router.authorize_initial(active.context, owner)
                except Exception as exc:  # pragma: no cover - router isolation
                    run.note(f"router refused to rule on {owner}: {type(exc).__name__}: {exc}")
                    authorized = False
                if not authorized:
                    run.note(f"owner {owner} not dispatched: routing policy withheld authorization")
                    state.done.set()
                    continue
            if not self._dispatch_stage(active, state, limit=dict(settings.dispatch_limit)):
                # `authorize_initial` already reserved this stage's compute. If
                # the dispatch did not happen -- the anchor completed while the
                # stage was being prepared, the backend refused -- that
                # reservation covers a stage that will never exist.
                self._release_undispatched(active, owner)

        # 5. Wait for completion, running router checkpoints in active mode.
        self._await_completion(active)

        # 6. Seal owners whose region completed normally.
        for owner, state in active.owners.items():
            if state.dispatched and not state.failed:
                try:
                    ledger.seal_owner(owner)
                except ShardLedgerError as exc:  # pragma: no cover - defensive
                    run.note(f"owner {owner} could not be sealed: {exc}")

        # The router's run is deliberately NOT closed here. `on_run_end` writes
        # the envelope claim, which now includes elapsed wall time, and the
        # anchor may still be searching: closing it at this point measured only
        # the part of the run the shadows took and could write `claimed: true`
        # for a run that then outlasted `wall_ms` waiting for the anchor. The
        # worker closes it after `anchor_done`.

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
        if active.anchor_completed.is_set():
            # Stages exist to inform a decision. Once the outward decision has
            # been emitted there is nothing left for any stage to inform, and in
            # active mode it would spend envelope budget on an observation that
            # cannot matter. This applies to the first stage too: a short
            # fixed-node anchor routinely finishes before root qualification
            # does, and `drain` means "let work already in flight finish", not
            # "start new work afterwards".
            kind = "extension" if state.stage_index > 0 else "initial dispatch"
            active.run.note(
                f"owner {state.owner} {kind} suppressed: outward decision already emitted"
            )
            return False
        run = active.run
        search_id = f"{run.run_id}:{state.instance}:{state.stage_index}"
        if state.stream is None:
            # Create the stream only when a stage is actually dispatched, so a
            # cancelled run never leaves an empty telemetry artifact behind.
            spec = self.runtime.spec(state.instance)
            state.stream = TelemetryStreamWriter(
                instance=state.instance,
                family=spec.family,
                role=spec.role,
                path=run.run_dir / f"{state.instance}.jsonl",
                adapter_factory=self._adapter_factory(
                    family=spec.family,
                    instance=state.instance,
                    position_id=active.context.position.position_id,
                    variant=active.context.position.variant,
                ),
                track_events=self.router is not None,
            )
            run.register_stream(state.stream)
        try:
            command = build_go_command(limit=limit, searchmoves=state.roots)
        except SearchRequestError as exc:  # pragma: no cover - configuration is validated
            run.note(f"owner {state.owner} dispatch rejected: {exc}")
            state.done.set()
            return False

        generation = active.generation

        def on_info(token: int, line: str) -> None:
            return None

        def on_complete(token: int, line: str) -> None:
            self._on_shadow_complete(generation, state.owner, token, line)

        # Commit under the coordinator lock, which `note_anchor_complete` also
        # takes. Checking the flag only at the top of this method leaves a real
        # window: stream creation is not free, and the anchor can return inside
        # it, so a stage could still be launched against a decision that had
        # already been emitted.
        with self._lock:
            if active.cancelled or active.anchor_completed.is_set():
                run.note(
                    f"owner {state.owner} dispatch abandoned: outward decision "
                    "was emitted while the stage was being prepared"
                )
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
                observed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
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
        # A backend that ignores or mishandles `searchmoves` can answer with a
        # legal move outside the region it was assigned. The stream stays
        # telemetry-contract-valid, so nothing downstream would notice -- and an
        # unauthorized final leader would reach residual labels and calibration.
        # Ownership is the whole basis of the partition, so an escape is a
        # failure of the stage, not a completion.
        if bestmove is not None and state.roots and bestmove not in state.roots:
            escape = (
                f"shadow instance {state.instance} answered {bestmove} which is outside its "
                f"assigned root region {list(state.roots)}; the stage is recorded as failed"
            )
            active.run.note(escape)
            self.runtime.record_shadow_failure(
                state.instance, escape, generation=active.generation
            )
            state.failed = True
            disposition = "failed"
            stop_reason = "root_region_escape"
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
        # A normally progressing node-limited stage is not draining. Bounding it
        # by `drain_timeout_s * 4` turned a stop-wait bound into an undocumented
        # runtime cap that cancelled valid stages. The safety net stays -- a
        # genuinely stuck worker still has to be cut loose -- but it is its own
        # declared setting, and it is measured PER STAGE: a single deadline for
        # the whole wait handed an extension only whatever milliseconds the
        # initial stage had left, cancelling it before it had run at all.
        budget = max(1.0, self.settings.stage_timeout_s)
        # One deadline PER OWNER, measured from that owner's own current stage.
        # A single shared deadline meant any owner dispatching an extension
        # handed every other pending worker another full budget -- so a hung
        # stage was reprieved whenever a healthy one advanced -- while the
        # eventual expiry cut loose stages that had not used their own
        # allowance at all.
        seen_stages: dict[str, int] = {}
        deadlines: dict[str, float] = {}

        def refresh(now: float) -> None:
            for owner, state in active.owners.items():
                if not state.dispatched:
                    continue
                stage = state.stages_dispatched
                if seen_stages.get(owner) != stage:
                    seen_stages[owner] = stage
                    deadlines[owner] = now + budget

        refresh(time.monotonic())
        while True:
            pending = [state for state in active.owners.values() if state.dispatched and not state.done.is_set()]
            if not pending:
                if self.router is None or active.cancelled:
                    return
                if not self._router_checkpoint(active):
                    return
                refresh(time.monotonic())
                continue
            now = time.monotonic()
            refresh(now)
            overrun = [state for state in pending if now > deadlines.get(state.owner, now + budget)]
            if overrun:
                self._cut_loose_overrunning(active, overrun)
                return
            for state in pending:
                state.done.wait(timeout=interval)
            if (
                self.router is not None
                and not active.cancelled
                and not active.anchor_completed.is_set()
            ):
                # Under `on_anchor_complete: drain` the outward decision is
                # already final and an in-flight stage is supposed to finish.
                # Continuing to route let a later checkpoint authorize a
                # `stop_worker` that killed that stage anyway, contradicting the
                # declared policy and truncating evidence for an action that
                # could no longer inform anything.
                self._router_checkpoint(active)

    def _cut_loose_overrunning(self, active: _ActiveRun, pending: list) -> None:
        """Stop stages past the per-stage budget, quarantining any that ignore it.

        A worker that overruns and then ignores `stop` used to be waited on for
        one drain timeout and then simply left: nothing recorded it as failed,
        so the run finalized and cleared `_run` while `shadow_available()` still
        called the process healthy, and the next `position` went to an engine
        still executing the previous generation. This is the same handling
        `quiesce()` applies to a worker that misses its drain deadline.
        """
        for state in pending:
            active.run.note(
                f"owner {state.owner} exceeded the declared stage budget "
                f"({self.settings.stage_timeout_s}s)"
            )
        self._cancel_locked(active, reason="stage_deadline")
        for state in pending:
            if state.done.wait(timeout=self.settings.drain_timeout_s):
                continue
            message = (
                f"shadow instance {state.instance} ignored `stop` after exceeding the "
                f"stage budget and is excluded from further synchronization"
            )
            self.runtime.record_shadow_failure(
                state.instance, message, generation=active.generation
            )
            active.run.note(message)
            state.failed = True
            if state.stage is not None:
                active.run.record_completion(
                    state.stage,
                    completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                    disposition="failed",
                    failure=message,
                )
            # Stop awaiting a worker that is never going to answer, so the
            # bundle -- including this evidence -- is actually written.
            state.done.set()

    def _release_undispatched(self, active: _ActiveRun, owner: str) -> None:
        """Return the reservation for an extension that was never dispatched."""
        if self.router is None:
            return
        release = getattr(self.router, "release_undispatched", None)
        if release is None:  # pragma: no cover - defensive against older routers
            return
        try:
            release(owner)
        except Exception as exc:  # pragma: no cover - router isolation
            active.run.note(
                f"router could not release the undispatched extension for {owner}: "
                f"{type(exc).__name__}: {exc}"
            )

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
                dispatched = False
                if state.done.is_set() and not state.failed and not state.stopped_by_policy:
                    limit = command.extend_limit or dict(self.settings.dispatch_limit)
                    dispatched = self._dispatch_stage(active, state, limit=limit)
                if dispatched:
                    dispatched_any = True
                else:
                    # The router already reserved this extension's compute. If
                    # the coordinator cannot run it -- the anchor completed in
                    # the intervening race, the worker is no longer eligible,
                    # the backend refused -- that reservation is for a stage
                    # that will never exist. Leaving it open denies capacity to
                    # real work and finalization later settles it as phantom
                    # spend in route.json.
                    self._release_undispatched(active, command.owner)
        return dispatched_any
