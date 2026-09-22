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
from common.prefix_dispatch import compile_descendant_region
from common.search_request import (
    PositionRequest,
    SearchRequestError,
    build_go_command,
    parse_go_request,
    parse_position_command,
)
from controller.replay import ReplayRun, StageRecord, TelemetryStreamWriter, sha256_file
from controller.prefix_shards import PrefixShardLedger, PrefixShardLedgerError
from controller.refinement import (
    RefinementError,
    RefinementRun,
    build_refinement_plan,
    partition_children,
)
from controller.runtime import BackendManager, RuntimeError as ControllerRuntimeError
from controller.verification import (
    VerificationError,
    VerificationRun,
    VerificationStage,
    build_verification_plan,
)
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

    def authorize_specialist(
        self,
        context: "RunContext",
        *,
        phase: str,
        owner: str | None = None,
        target_id: str | None = None,
    ) -> str | None:
        ...

    def settle_specialist(
        self,
        token: str,
        *,
        actual_wall_ms: float | None = None,
        threads: int = 1,
    ) -> None:
        ...

    def release_specialist(self, token: str, *, reason: str) -> None:
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
    verification: VerificationRun | None = None
    refinement: RefinementRun | None = None
    refinement_positioned: set[str] = field(default_factory=set)
    refinement_oracle_active: bool = False
    specialist_tokens: dict[str, str] = field(default_factory=dict)
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
        #: Detached `stop` writers started by authority-path cancellations.
        #: `quiesce()` joins them so none can reach a later generation.
        self._stop_threads: list[threading.Thread] = []
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

    @staticmethod
    def _discard_run_dir(run_dir: Path) -> None:
        """Take back a bundle directory no run will ever use.

        `rmdir` removes only an EMPTY directory -- it raises rather than
        deleting anything else -- so this can never destroy evidence. It is
        best effort because an abandoned setup thread may still be creating its
        stream file in there; that thread releases the directory itself once
        its writer is closed, so a failure here is covered rather than final.
        """
        try:
            run_dir.rmdir()
        except OSError:  # pragma: no cover - covered by the late release path
            pass

    def _release_late_stream(self, run_dir: Path | None, stream: Any) -> None:
        """Release a stream setup whose run already gave up waiting for it.

        A constructed writer owns a running thread and an open file handle. Its
        queue is empty -- nothing was ever submitted to a stream no run holds --
        so the writer takes the sentinel and exits at once, and the short
        timeout only bounds a pathological case. A constructor that raised
        after opening its file leaves the same directory behind with no writer,
        which is why `stream` may be `None` here.
        """
        if stream is not None:
            stream.close(timeout=1.0)
            try:
                stream.path.unlink()
            except OSError:  # pragma: no cover - best effort
                pass
        # `run_dir` is None when the run that gave up on this stream is still
        # live and using that directory for its other streams; only a directory
        # no run will ever finalize into is taken back.
        if run_dir is not None:
            self._discard_run_dir(run_dir)

    def _within_prepare_budget(
        self,
        label: str,
        work: Callable[[], Any],
        *,
        discard: Callable[[Any], None] | None = None,
        deadline: float | None = None,
    ) -> tuple[bool, Any]:
        """Run pre-anchor filesystem work without letting it block a search.

        A blocked or very slow `replay_root` would otherwise hold the calling
        thread -- and therefore the outward anchor -- for as long as the kernel
        takes. EVERY piece of filesystem work on this path has to be inside the
        bound, not just the first: round seven bounded the `mkdir` and left the
        stream file's own `mkdir` and `open` outside it, which is the same
        unbounded delay one call later.

        The work happens on a short-lived thread and is abandoned at the
        declared budget. An abandoned thread may still finish afterwards, and
        what it finishes is not always inert: a constructed
        `TelemetryStreamWriter` owns a running writer thread blocked on its
        queue and an open file handle, so a run that went on without it leaks
        both -- once per timeout, until a repeatedly slow `replay_root`
        exhausts the controller's descriptors. `discard` releases such a late
        result, on the abandoned thread that produced it. Work whose result is
        genuinely inert (a `mkdir` returning `None`) passes none.
        """
        # `prepare_budget_s` is documented as the cap for ALL pre-anchor replay
        # setup, so every step shares one deadline. Giving each step its own
        # full window let the directory creation and the stream open each
        # finish just inside their own bound and delay the outward anchor by
        # nearly twice the declared hard cap.
        if deadline is None:
            budget = max(0.001, float(self.settings.prepare_budget_s))
        else:
            budget = max(0.001, deadline - time.monotonic())
        outcome: dict[str, Any] = {}
        guard = threading.Lock()
        abandoned = False

        def _run() -> None:
            value: Any = None
            failure: str | None = None
            try:
                value = work()
            except Exception as exc:  # noqa: BLE001 - reported, never raised here
                failure = f"{type(exc).__name__}: {exc}"
            with guard:
                late = abandoned
                if not late:
                    if failure is None:
                        outcome["value"] = value
                        outcome["ok"] = True
                    else:
                        outcome["error"] = failure
            if not late or discard is None:
                return
            # A late failure still needs releasing: a constructor that opened a
            # file and then raised leaves the same partial state behind as one
            # that succeeded, so `discard` runs either way and takes `None`.
            try:
                discard(value)
            except Exception as exc:  # noqa: BLE001 - no run is left to fail
                self._diagnostic(
                    f"replay setup ({label}) could not release its late result: "
                    f"{type(exc).__name__}: {exc}"
                )

        worker = threading.Thread(
            target=_run, name=f"allfather-prepare-{label}", daemon=True
        )
        worker.start()
        worker.join(timeout=budget)
        with guard:
            # Decide on the published outcome, not on `is_alive()`: the flag the
            # worker reads and the result this returns have to be the same
            # decision, taken once, or a result could be both used here and
            # discarded there.
            settled = "ok" in outcome or "error" in outcome
            if not settled:
                abandoned = True
        if not settled:
            self._diagnostic(
                f"replay setup ({label}) exceeded its {budget}s pre-anchor budget; this "
                "search runs without a shadow bundle rather than delaying the outward "
                "decision"
            )
            return False, None
        if "error" in outcome:
            self._diagnostic(f"replay setup ({label}) failed: {outcome['error']}")
            return False, None
        return bool(outcome.get("ok")), outcome.get("value")

    def _make_run_dir_within_budget(self, run_dir: Path, deadline: float) -> bool:
        """Create the bundle directory, or give up on it inside the budget.

        A directory the abandoned thread creates afterwards is not inert
        leftover: offline derivation walks every directory under `replay_root`
        and one with no manifest is not a bundle, so a single timeout during a
        game would break the whole derivation pass. It is taken back here.
        """
        ok, _ = self._within_prepare_budget(
            run_dir.name,
            lambda: run_dir.mkdir(parents=True, exist_ok=False),
            discard=lambda _value: self._discard_run_dir(run_dir),
            deadline=deadline,
        )
        return ok

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

        # The run clock starts HERE, before any preparation. Capturing it after
        # `_make_run_dir_within_budget` returned put every millisecond of
        # pre-anchor filesystem work outside the wall envelope: a 200 ms `mkdir`
        # ahead of a 900 ms anchor search still reported `wall_within_envelope`
        # against a 1000 ms envelope, and the same milliseconds went uncharged
        # as controller overhead. Preparation is bounded, not free.
        started = time.monotonic()
        # One deadline for every pre-anchor filesystem step, taken from the
        # same origin as the run clock.
        prepare_deadline = started + max(0.001, float(self.settings.prepare_budget_s))
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
        if not self._make_run_dir_within_budget(run_dir, prepare_deadline):
            return False

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
        # `TelemetryStreamWriter` does its own `mkdir` and `open`, which is more
        # pre-anchor filesystem work and belongs inside the same bound.
        opened, anchor_stream = self._within_prepare_budget(
            f"{run_id}-anchor-stream",
            discard=lambda stream: self._release_late_stream(run_dir, stream),
            deadline=prepare_deadline,
            work=lambda: TelemetryStreamWriter(
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
            ),
        )
        if not opened or anchor_stream is None:
            # The directory exists and no run will ever finalize into it, so no
            # manifest will ever be written there. Take it back here; on the
            # timeout path an abandoned thread may still be opening its stream
            # file, and that thread releases the directory itself afterwards.
            self._discard_run_dir(run_dir)
            return False
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
        run.prepare_ms = (time.monotonic() - started) * 1000.0
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
                instances = self._cancel_locked(active, reason=reason)
            else:
                instances = None
                self._run = None
        if instances is not None:
            self._stop_instances(instances)
            return
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
            stream = None
            if active.refinement is not None:
                stream = active.refinement.observation_stream(instance)
            if stream is None and active.verification is not None:
                verification_stage = active.verification.stage_for_instance(instance)
                if verification_stage is not None and not verification_stage.done.is_set():
                    stream = active.verification.observation_stream(instance)
            if stream is None:
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
            # Publish the decision boundary while holding the exact lock used
            # by EXPLORE/VERIFY dispatch commits. The old placement set this
            # flag after releasing the lock, leaving a window in which the
            # anchor callback had already begun but a new observational stage
            # could still acquire the lock and launch.
            active.anchor_completed.set()
        if stage is not None:
            bestmove = line.split()[1] if line.startswith("bestmove ") and len(line.split()) > 1 else None
            active.run.record_completion(
                stage,
                completed_ms=elapsed,
                disposition="completed",
                bestmove=bestmove,
            )
        # Finalization waits on anchor_done, not merely anchor_completed. Keep
        # this second event after the authority StageRecord is complete.
        active.anchor_done.set()
        # The outward answer is already emitted. No *new* observational stage
        # may be opened against a decision that has already been made. Whether
        # an in-flight node-limited stage is drained or killed is a declared
        # configuration choice, never an implicit one.
        if self.settings.on_anchor_complete == "cancel":
            # This runs on the ANCHOR's stdout reader thread, which is the
            # thread that carries the outward `bestmove`. A shadow whose stdin
            # blocks must not be able to stall it, so the `stop` writes are
            # detached; `quiesce()` joins them before any state change.
            self.cancel(generation, reason="anchor_complete", detach=True)

    def _on_shadow_exit(self, instance: str, rc: int | None, token: int | None) -> None:
        with self._lock:
            active = self._run
            if active is None:
                return
            elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
            refinement_stage = (
                None
                if active.refinement is None
                else active.refinement.stage_for_instance(instance)
            )
            verification_stage = (
                None
                if active.verification is None
                else active.verification.stage_for_instance(instance)
            )
            state = next((s for s in active.owners.values() if s.instance == instance), None)
        if refinement_stage is not None and not refinement_stage.done.is_set():
            if active.refinement is not None:
                message = f"{instance} exited unexpectedly during REFINE; rc={rc}"
                active.refinement.record_completion(
                    refinement_stage,
                    completed_ms=elapsed,
                    disposition="failed",
                    failure=message,
                )
                active.refinement.set_target_disposition(
                    refinement_stage.target_id, "incomplete", message
                )
                active.refinement.set_disposition("incomplete", message)
            return
        if verification_stage is not None and not verification_stage.done.is_set():
            if active.verification is not None:
                active.verification.record_completion(
                    verification_stage,
                    completed_ms=elapsed,
                    disposition="failed",
                    failure=f"{instance} exited unexpectedly during VERIFY; rc={rc}",
                )
                active.verification.set_disposition(
                    "incomplete", f"{instance} exited unexpectedly during VERIFY"
                )
            return
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

    def cancel(
        self, generation: int | None = None, *, reason: str, detach: bool = False
    ) -> None:
        """Cancel the active generation and stop its dispatched shadows.

        `detach=True` performs the `stop` writes on a short-lived thread. It is
        for callers on an AUTHORITY path -- the frontend's `stop` handler and
        the anchor's own stdout reader -- because writing to a shadow's stdin
        can block on a full pipe, and neither the outward `bestmove` nor the
        reader thread that carries it may ever wait on an observational
        process. A detached stop is tracked and joined by `quiesce()`, so it
        can never land on a later generation that reuses the same process.
        """
        with self._lock:
            active = self._run
            if active is None:
                return
            if generation is not None and active.generation != generation:
                return
            instances = self._cancel_locked(active, reason=reason)
        if not instances:
            return
        if not detach:
            self._stop_instances(instances)
            return
        worker = threading.Thread(
            target=self._stop_instances,
            args=(instances,),
            name=f"allfather-shadow-stop-g{active.generation:06d}",
            daemon=True,
        )
        with self._lock:
            self._stop_threads = [t for t in self._stop_threads if t.is_alive()]
            self._stop_threads.append(worker)
        worker.start()

    def _cancel_locked(self, active: _ActiveRun, *, reason: str) -> list[str]:
        """Mark the run cancelled and report which instances still need `stop`.

        The `stop` writes are deliberately NOT done here. Three of this
        method's four callers hold `self._lock`, and an engine's stdin can
        block on a full pipe: holding the coordinator lock across that write
        blocks `note_anchor_complete`, which is the path that records the
        anchor's own completion. Observation may never hold up authority.
        Callers release the lock and pass this list to `_stop_instances`.
        """
        if not active.cancelled:
            active.cancelled = True
            active.cancel_reason = reason
        instances = [
            state.instance
            for state in active.owners.values()
            if state.dispatched and not state.done.is_set()
        ]
        if active.verification is not None:
            instances.extend(stage.instance for stage in active.verification.active_stages())
        if active.refinement is not None:
            instances.extend(stage.instance for stage in active.refinement.active_stages())
        return list(dict.fromkeys(instances))

    def _stop_instances(self, instances: list[str]) -> None:
        """Send `stop` to each instance. Never called with `self._lock` held."""
        for instance in instances:
            try:
                self.runtime.stop_instance(instance)
            except ControllerRuntimeError:  # pragma: no cover - shadow stop is non-authoritative
                pass

    def _join_stop_threads(self, deadline: float) -> bool:
        """Wait for detached `stop` writers, so none outlives its generation."""
        with self._lock:
            workers = [t for t in self._stop_threads if t.is_alive()]
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        outstanding = [t for t in workers if t.is_alive()]
        with self._lock:
            self._stop_threads = [t for t in self._stop_threads if t.is_alive()]
        return not outstanding

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
        # ONE deadline for the whole barrier. Passing `timeout` to each wait in
        # turn granted the worker a fresh full window at every step, so a
        # setting documented as the hard bound for draining after `stop` could
        # hold state-changing UCI commands for a multiple of itself.
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._lock:
            active = self._run
            if active is None:
                # Even with no active run, a detached `stop` from the
                # generation just cancelled may still be in flight; it must not
                # reach the process the caller is about to synchronize.
                return self._join_stop_threads(deadline)
            instances = self._cancel_locked(active, reason=reason)
            worker = active.worker
            finished = active.finished
        self._stop_instances(instances)
        detached = self._join_stop_threads(deadline)
        if worker is not None:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        drained = finished.wait(timeout=max(0.0, deadline - time.monotonic()))
        if not detached:
            self._diagnostic(
                "a detached shadow `stop` did not complete within the configured "
                "timeout; this state change is refused rather than risking it "
                "landing on the next generation"
            )
            drained = False
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
                # Recorded as failed BEFORE it is released. `_execute` seals
                # an owner whose state is `dispatched and not failed`, so
                # releasing the waiter without this flag left the manifest
                # carrying a failed stage whose region was nevertheless sealed
                # as normally completed.
                state.failed = True
                # A worker recorded as failed is no longer awaited. Without
                # this the run's worker thread would stay blocked until its own
                # drain deadline, so the bundle -- including the evidence of the
                # failure itself -- would not be written for many seconds.
                state.done.set()
            if active.verification is not None:
                for stage in active.verification.active_stages():
                    message = (
                        f"verification instance {stage.instance} did not drain within "
                        f"{timeout}s and is excluded from further synchronization"
                    )
                    self.runtime.record_shadow_failure(
                        stage.instance, message, generation=active.generation
                    )
                    active.verification.record_completion(
                        stage,
                        completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                        disposition="failed",
                        failure=message,
                    )
                    active.verification.set_disposition("incomplete", message)
            if active.refinement is not None:
                for stage in active.refinement.active_stages():
                    message = (
                        f"refinement instance {stage.instance} did not drain within "
                        f"{timeout}s and is excluded from further synchronization"
                    )
                    self.runtime.record_shadow_failure(
                        stage.instance, message, generation=active.generation
                    )
                    active.refinement.record_completion(
                        stage,
                        completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                        disposition="failed",
                        failure=message,
                    )
                    active.refinement.set_target_disposition(
                        stage.target_id, "incomplete", message
                    )
                    active.refinement.set_disposition("incomplete", message)
            for instance in sorted(active.refinement_positioned):
                message = (
                    f"REFINE-positioned shadow {instance} did not restore within "
                    f"{timeout}s and is excluded from further synchronization"
                )
                self.runtime.record_shadow_failure(
                    instance, message, generation=active.generation
                )
                if active.refinement is not None:
                    active.refinement.note(message)
                    active.refinement.set_disposition("incomplete", message)
            active.refinement_positioned.clear()
            if active.refinement_oracle_active:
                oracle = self.settings.oracle
                message = (
                    f"REFINE child oracle {oracle} did not return within {timeout}s and is "
                    "excluded from further synchronization"
                )
                self.runtime.record_shadow_failure(
                    oracle, message, generation=active.generation
                )
                if active.refinement is not None:
                    active.refinement.note(message)
                    active.refinement.set_disposition("incomplete", message)
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
            # Orchestration can raise after some owners are already searching --
            # a later owner's telemetry file failing to open, for example.
            # Recording the error and returning left those processes running
            # while finalization cleared `_run`, so the next `position` could
            # reach a worker still on the previous generation. Stop and drain
            # them here, on the same path `quiesce()` uses.
            try:
                with self._lock:
                    instances = self._cancel_locked(active, reason="worker_error")
                if instances:
                    self._stop_instances(instances)

                deadline = time.monotonic() + self.settings.drain_timeout_s
                for state in list(active.owners.values()):
                    if not state.dispatched or state.done.is_set():
                        continue
                    state.done.wait(timeout=max(0.0, deadline - time.monotonic()))
                    if state.done.is_set():
                        continue
                    message = (
                        f"shadow instance {state.instance} did not drain after a worker "
                        "orchestration error and is excluded from further synchronization"
                    )
                    self.runtime.record_shadow_failure(
                        state.instance, message, generation=active.generation
                    )
                    state.failed = True
                    if state.stage is not None:
                        active.run.record_completion(
                            state.stage,
                            completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                            disposition="failed",
                            failure=message,
                        )
                    state.done.set()

                if active.verification is not None:
                    for stage in active.verification.active_stages():
                        stage.done.wait(timeout=max(0.0, deadline - time.monotonic()))
                        if stage.done.is_set():
                            continue
                        message = (
                            f"verification instance {stage.instance} did not drain after "
                            "a worker orchestration error and is excluded from further synchronization"
                        )
                        self.runtime.record_shadow_failure(
                            stage.instance, message, generation=active.generation
                        )
                        active.verification.record_completion(
                            stage,
                            completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                            disposition="failed",
                            failure=message,
                        )
                        active.verification.set_disposition("incomplete", message)

                if active.refinement is not None:
                    for stage in active.refinement.active_stages():
                        stage.done.wait(timeout=max(0.0, deadline - time.monotonic()))
                        if stage.done.is_set():
                            continue
                        message = (
                            f"refinement instance {stage.instance} did not drain after "
                            "a worker orchestration error and is excluded from further synchronization"
                        )
                        self.runtime.record_shadow_failure(
                            stage.instance, message, generation=active.generation
                        )
                        active.refinement.record_completion(
                            stage,
                            completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                            disposition="failed",
                            failure=message,
                        )
                        active.refinement.set_target_disposition(
                            stage.target_id, "incomplete", message
                        )
                        active.refinement.set_disposition("incomplete", message)
                if (
                    active.refinement is not None
                    and not active.refinement.active_stages()
                    and active.refinement_positioned
                ):
                    self._restore_all_refinement_positions(active)
            except Exception as cleanup_exc:  # pragma: no cover - defensive
                active.run.note(
                    f"could not drain dispatched stages after a worker error: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
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
                if (
                    active.refinement is not None
                    and not active.refinement.active_stages()
                    and active.refinement_positioned
                ):
                    self._restore_all_refinement_positions(active)
                if active.ledger is not None:
                    active.run.post_ledger_snapshot = active.ledger.snapshot()
                active.run.shadow_health = {
                    name: health.snapshot() for name, health in self.runtime.shadow_health().items()
                }
                if active.cancelled and stop_reason is None:
                    stop_reason = active.cancel_reason
                active.run.finalize(disposition=disposition, stop_reason=stop_reason)
                parent_manifest_sha = sha256_file(active.run.run_dir / "manifest.json")
                if active.verification is not None:
                    active.verification.finalize(
                        source_manifest_sha256=parent_manifest_sha
                    )
                if active.refinement is not None:
                    verification_path = active.run.run_dir / "verification" / "manifest.json"
                    if not verification_path.is_file():
                        raise ControllerRuntimeError(
                            "REFINE finalization requires a finalized VERIFY manifest"
                        )
                    active.refinement.finalize(
                        source_manifest_sha256=parent_manifest_sha,
                        verification_manifest_sha256=sha256_file(verification_path),
                    )
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

    def _authorize_specialist(
        self,
        active: _ActiveRun,
        *,
        key: str,
        phase: str,
        owner: str | None = None,
        target_id: str | None = None,
    ) -> bool:
        if self.router is None:
            return True
        authorize = getattr(self.router, "authorize_specialist", None)
        if authorize is None:
            return False
        try:
            token = authorize(
                active.context,
                phase=phase,
                owner=owner,
                target_id=target_id,
            )
        except Exception as exc:  # pragma: no cover - router isolation
            active.run.note(
                f"specialist authorization failed for {key}: "
                f"{type(exc).__name__}: {exc}"
            )
            return False
        if token is None:
            active.run.note(f"specialist authorization denied for {key}")
            return False
        active.specialist_tokens[key] = token
        return True

    def _settle_specialist(
        self,
        active: _ActiveRun,
        *,
        key: str,
        dispatched_ms: float,
        completed_ms: float,
        instance: str,
    ) -> None:
        token = active.specialist_tokens.pop(key, None)
        if token is None or self.router is None:
            return
        settle = getattr(self.router, "settle_specialist", None)
        if settle is None:
            return
        threads = 1
        try:
            threads = max(1, int(self.runtime.spec(instance).options.get("Threads", 1)))
        except (TypeError, ValueError):
            threads = 1
        try:
            settle(
                token,
                actual_wall_ms=max(0.0, completed_ms - dispatched_ms),
                threads=threads,
            )
        except Exception as exc:  # pragma: no cover - router isolation
            active.run.note(
                f"specialist settlement failed for {key}: "
                f"{type(exc).__name__}: {exc}"
            )

    def _release_specialist(
        self,
        active: _ActiveRun,
        *,
        key: str,
        reason: str,
    ) -> None:
        token = active.specialist_tokens.pop(key, None)
        if token is None or self.router is None:
            return
        release = getattr(self.router, "release_specialist", None)
        if release is None:
            return
        try:
            release(token, reason=reason)
        except Exception as exc:  # pragma: no cover - router isolation
            active.run.note(
                f"specialist release failed for {key}: "
                f"{type(exc).__name__}: {exc}"
            )

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

        # 7. Deliberate common-support overlap. RootShardLedger remains frozen:
        # VERIFY is represented by a separate artifact and never grants a second
        # EXPLORE owner to any shard.
        self._execute_verification(active)

        # 8. One-level recursive shadow REFINE. This consumes only completed
        # raw VERIFY facts and the PrefixShardLedger v2 substrate. It remains
        # research instrumentation and may not influence the outward anchor.
        self._execute_refinement(active)

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

    def _execute_verification(self, active: _ActiveRun) -> None:
        settings = self.runtime.config.verification
        if settings is None or active.cancelled or self._closed:
            return
        if active.anchor_completed.is_set():
            return

        owner_bestmoves: dict[str, str | None] = {}
        owner_roots: dict[str, tuple[str, ...]] = {}
        for owner in self.settings.owners:
            state = active.owners.get(owner)
            if (
                state is None
                or not state.dispatched
                or state.failed
                or state.stopped_by_policy
                or state.stage is None
                or state.stage.disposition != "completed"
            ):
                return
            owner_bestmoves[owner] = state.last_bestmove
            owner_roots[owner] = state.roots
            if not self.runtime.shadow_available(state.instance):
                return

        try:
            plan = build_verification_plan(
                generation=active.generation,
                source_run_id=active.run.run_id,
                position_id=active.context.position.position_id,
                settings=settings,
                owners=self.settings.owners,
                instance_by_owner=self.settings.instance_by_owner,
                owner_bestmoves=owner_bestmoves,
                owner_roots=owner_roots,
            )
        except VerificationError:
            return

        verification = VerificationRun(plan=plan, run_dir=active.run.run_dir)
        active.verification = verification

        # Prepare all three writers before dispatching any verifier. If setup
        # fails, v1 refuses a partial comparison instead of silently changing
        # the experiment from three-way to two-way.
        for owner in plan.owners:
            if active.anchor_completed.is_set() or active.cancelled:
                verification.set_disposition("skipped", "anchor completed or run cancelled")
                return
            instance = plan.participants[owner]
            spec = self.runtime.spec(instance)
            stream_path = verification.verification_dir / f"{instance}.jsonl"
            opened, stream = self._within_prepare_budget(
                f"{active.run.run_id}-verify-{instance}-stream",
                discard=lambda late: self._release_late_stream(None, late),
                work=lambda spec=spec, instance=instance, stream_path=stream_path: TelemetryStreamWriter(
                    instance=instance,
                    family=spec.family,
                    role=spec.role,
                    path=stream_path,
                    adapter_factory=self._adapter_factory(
                        family=spec.family,
                        instance=instance,
                        position_id=active.context.position.position_id,
                        variant=active.context.position.variant,
                    ),
                    track_events=True,
                ),
            )
            if not opened or stream is None:
                verification.set_disposition(
                    "skipped", f"verification stream setup failed for {instance}"
                )
                return
            verification.register_stream(stream)

        dispatched = 0
        for owner in plan.owners:
            if self._dispatch_verification_stage(active, owner):
                dispatched += 1
            else:
                break

        if dispatched != len(plan.owners):
            verification.set_disposition(
                "incomplete",
                "not all verification participants were dispatched before the decision boundary",
            )
        self._await_verification(active)

        stages = verification.stages()
        if len(stages) == len(plan.owners) and all(
            stage.disposition == "completed" for stage in stages
        ):
            verification.set_disposition("completed")
        elif verification.disposition == "running":
            verification.set_disposition("incomplete", "verification stages did not all complete")

    def _dispatch_verification_stage(self, active: _ActiveRun, owner: str) -> bool:
        verification = active.verification
        settings = self.runtime.config.verification
        if verification is None or settings is None:
            return False
        plan = verification.plan
        instance = plan.participants[owner]
        spec = self.runtime.spec(instance)
        stream = verification.stream(instance)
        if stream is None:
            return False
        try:
            command = build_go_command(
                limit=dict(settings.dispatch_limit),
                searchmoves=plan.candidate_roots,
            )
        except SearchRequestError:
            return False
        search_id = f"{active.run.run_id}:verify:{instance}:0"
        generation = active.generation

        def on_info(token: int, line: str) -> None:
            return None

        def on_complete(token: int, line: str) -> None:
            self._on_verification_complete(generation, owner, token, line)

        # The final decision-boundary check and dispatch use the same lock as
        # note_anchor_complete(). If the anchor wins the race this stage never
        # starts; if this dispatch wins, it is already in flight and the
        # declared drain/cancel policy applies.
        reservation_key = f"verify:{owner}"
        with self._lock:
            if (
                active.cancelled
                or self._closed
                or active.anchor_completed.is_set()
                or not self.runtime.shadow_available(instance)
            ):
                return False
            if not self._authorize_specialist(
                active,
                key=reservation_key,
                phase="verify",
                owner=owner,
            ):
                return False
            verification.activate_stream(instance)
            stream.begin_stage(
                search_id=search_id,
                position=active.context.position.telemetry_position(),
                request=parse_go_request(command),
                controller={
                    "execution_mode": self.runtime.config.telemetry_execution_mode,
                    "phase": "VERIFY",
                    "instance_role": "shadow",
                    "owner": owner,
                    "decision_authority": False,
                },
                observed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
            )
            stage = verification.record_dispatch(
                owner=owner,
                instance=instance,
                family=spec.family,
                search_id=search_id,
                command=command,
                dispatched_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
            )
            dispatched = self.runtime.start_shadow_search(
                instance,
                command,
                token=generation,
                on_info=on_info,
                on_complete=on_complete,
            )
        if not dispatched:
            self._release_specialist(
                active,
                key=reservation_key,
                reason="VERIFY reservation released because backend dispatch failed",
            )
            verification.record_completion(
                stage,
                completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                disposition="failed",
                failure="verification dispatch rejected; instance unavailable",
            )
            verification.set_disposition(
                "incomplete", f"verification dispatch rejected for {instance}"
            )
            return False
        return True

    def _on_verification_complete(
        self, generation: int, owner: str, token: int, line: str
    ) -> None:
        with self._lock:
            active = self._run
            if active is None or active.generation != generation or token != generation:
                return
            verification = active.verification
            stage = None if verification is None else verification.stage_for_owner(owner)
            elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
        if verification is None or stage is None:
            return

        tokens = line.split()
        bestmove = tokens[1] if line.startswith("bestmove ") and len(tokens) > 1 else None
        failure: str | None = None
        candidate_set = set(verification.plan.candidate_roots)
        if bestmove is not None and bestmove not in candidate_set:
            failure = (
                f"verification instance {stage.instance} answered {bestmove} outside "
                f"the common candidate set {list(verification.plan.candidate_roots)}"
            )

        stream = verification.stream(stage.instance)
        if failure is None and stream is not None:
            if not stream.drain_barrier(0.25):
                failure = (
                    f"verification telemetry for {stage.instance} did not drain before "
                    "the completion audit"
                )
            elif stream.evidence_lossy:
                failure = f"verification telemetry for {stage.instance} lost evidence"
            elif stream.tracked_events_truncated:
                failure = (
                    f"verification live evidence for {stage.instance} exceeded the "
                    "tracked-event limit before the completion audit"
                )
            else:
                for event in stream.tracked_events():
                    if event.get("event_type") != "candidate.update":
                        continue
                    candidate = event.get("candidate") or {}
                    move = candidate.get("move")
                    pv = candidate.get("pv") or []
                    if move not in candidate_set or (pv and pv[0] not in candidate_set):
                        failure = (
                            f"verification telemetry for {stage.instance} escaped the "
                            "declared common candidate set"
                        )
                        break

        self._settle_specialist(
            active,
            key=f"verify:{owner}",
            dispatched_ms=stage.dispatched_ms,
            completed_ms=elapsed,
            instance=stage.instance,
        )

        if failure is not None:
            self.runtime.record_shadow_failure(
                stage.instance, failure, generation=active.generation
            )
            verification.record_completion(
                stage,
                completed_ms=elapsed,
                disposition="failed",
                bestmove=bestmove,
                stop_reason="verification_root_escape_or_loss",
                failure=failure,
            )
            verification.set_disposition("incomplete", failure)
            return

        disposition = "stopped" if active.cancelled else "completed"
        verification.record_completion(
            stage,
            completed_ms=elapsed,
            disposition=disposition,
            bestmove=bestmove,
            stop_reason=active.cancel_reason if active.cancelled else None,
        )

    def _await_verification(self, active: _ActiveRun) -> None:
        verification = active.verification
        if verification is None:
            return
        interval = 0.02
        stage_budget_ms = float(self.settings.stage_timeout_s) * 1000.0

        while True:
            pending = list(verification.active_stages())
            if not pending:
                return
            elapsed_ms = (time.monotonic() - active.started_monotonic) * 1000.0
            overrun = [
                stage
                for stage in pending
                if elapsed_ms - stage.dispatched_ms > stage_budget_ms
            ]
            if overrun:
                instances = list(dict.fromkeys(stage.instance for stage in pending))
                self._stop_instances(instances)
                deadline = time.monotonic() + self.settings.drain_timeout_s
                for stage in pending:
                    stage.done.wait(timeout=max(0.0, deadline - time.monotonic()))
                for stage in pending:
                    if stage.done.is_set():
                        continue
                    message = (
                        f"verification instance {stage.instance} exceeded the stage "
                        "budget and did not drain after stop"
                    )
                    self.runtime.record_shadow_failure(
                        stage.instance, message, generation=active.generation
                    )
                    verification.record_completion(
                        stage,
                        completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                        disposition="failed",
                        failure=message,
                    )
                verification.set_disposition(
                    "incomplete", "verification stage deadline exceeded"
                )
                return
            self._wait_slice(pending, interval)

    def _execute_refinement(self, active: _ActiveRun) -> None:
        settings = self.runtime.config.refinement
        verification = active.verification
        if settings is None or verification is None or active.cancelled or self._closed:
            return
        if active.anchor_completed.is_set() or verification.disposition != "completed":
            return
        if active.ledger is None:
            active.run.note("REFINE skipped: completed root ledger is unavailable")
            return

        try:
            plan = build_refinement_plan(
                settings=settings,
                verification=verification,
            )
        except RefinementError as exc:
            active.run.note(f"REFINE plan rejected: {exc}")
            return

        source_root_snapshot = active.ledger.snapshot()
        try:
            prefix_ledger = PrefixShardLedger(
                active.ledger.candidate_roots,
                owners=plan.owners,
                generation=active.generation,
            )
            root_partition = {
                owner: tuple(active.run.owner_roots.get(owner, ()))
                for owner in plan.owners
            }
            prefix_ledger.assign_root_partition(root_partition)
            for owner in plan.owners:
                owned = [
                    item
                    for item in prefix_ledger.frontier_for_owner(owner)
                    if item["state"] == "leased"
                ]
                for item in owned:
                    shard_id = str(item["id"])
                    prefix_ledger.activate_shard(shard_id, owner=owner)
                    prefix_ledger.seal_shard(shard_id, owner=owner)
            initial_v2_snapshot = prefix_ledger.snapshot()
        except PrefixShardLedgerError as exc:
            active.run.note(f"REFINE v2 mirror rejected: {exc}")
            return

        refinement = RefinementRun(
            plan=plan,
            run_dir=active.run.run_dir,
            ledger=prefix_ledger,
            oracle_instance=self.settings.oracle,
            source_root_v1_snapshot=source_root_snapshot,
            initial_v2_snapshot=initial_v2_snapshot,
        )
        active.refinement = refinement

        if not plan.targets:
            refinement.set_disposition(
                "not_applicable",
                "completed VERIFY final leaders were unanimous",
            )
            return

        all_completed = True
        for target in plan.targets:
            if active.cancelled or self._closed or active.anchor_completed.is_set():
                all_completed = False
                refinement.set_disposition(
                    "incomplete",
                    "decision boundary or cancellation reached before all REFINE targets",
                )
                break

            root_shard = next(
                (
                    item
                    for item in prefix_ledger.frontier()
                    if item["prefix"] == [target.root_move]
                ),
                None,
            )
            if root_shard is None:
                all_completed = False
                refinement.set_disposition(
                    "incomplete",
                    f"target root {target.root_move} is not on the v2 frontier",
                )
                break
            source_shard_id = str(root_shard["id"])
            if (
                root_shard["state"] != "sealed"
                or root_shard["owner"] != target.source_owner
            ):
                all_completed = False
                refinement.set_disposition(
                    "incomplete",
                    f"target root {target.root_move} does not match completed EXPLORE ownership",
                )
                break

            descendant_position = PositionRequest(
                base_fen=active.context.position.base_fen,
                moves=tuple(active.context.position.moves) + (target.root_move,),
                variant=active.context.position.variant,
            )
            oracle_key = f"refine-oracle:{target.target_id}"
            with self._lock:
                if active.cancelled or active.anchor_completed.is_set():
                    all_completed = False
                    refinement.set_disposition(
                        "incomplete",
                        "decision boundary reached before REFINE child oracle",
                    )
                    break
                if not self._authorize_specialist(
                    active,
                    key=oracle_key,
                    phase="refine_oracle",
                    target_id=target.target_id,
                ):
                    all_completed = False
                    refinement.set_disposition(
                        "incomplete",
                        f"active budget denied child oracle for {target.root_move}",
                    )
                    break
                active.refinement_oracle_active = True
                oracle_dispatched_ms = (
                    time.monotonic() - active.started_monotonic
                ) * 1000.0
            try:
                children = self.runtime.legal_moves_at_shadow_position(
                    instance=self.settings.oracle,
                    position_command=descendant_position.command(),
                    timeout=self.settings.oracle_timeout_s,
                )
            except ControllerRuntimeError as exc:
                all_completed = False
                refinement.set_disposition(
                    "incomplete",
                    f"child oracle failed for {target.root_move}: {exc}",
                )
                break
            finally:
                oracle_completed_ms = (
                    time.monotonic() - active.started_monotonic
                ) * 1000.0
                self._settle_specialist(
                    active,
                    key=oracle_key,
                    dispatched_ms=oracle_dispatched_ms,
                    completed_ms=oracle_completed_ms,
                    instance=self.settings.oracle,
                )
                with self._lock:
                    active.refinement_oracle_active = False

            child_partition = partition_children(children, plan.owners)
            if not children:
                refinement.register_target(
                    target=target,
                    source_shard_id=source_shard_id,
                    oracle_position_command=descendant_position.command(),
                    oracle_children=children,
                    child_partition=child_partition,
                    child_shards={owner: () for owner in plan.owners},
                )
                refinement.set_target_disposition(
                    target.target_id,
                    "terminal",
                    "exact child oracle returned an empty legal continuation set",
                )
                continue

            try:
                child_ids = prefix_ledger.split_shard(
                    source_shard_id,
                    children,
                    owner=target.source_owner,
                )
                child_id_by_move = {
                    str(prefix_ledger.get(shard_id)["prefix"][-1]): shard_id
                    for shard_id in child_ids
                }
                child_shards = {
                    owner: tuple(child_id_by_move[move] for move in child_partition[owner])
                    for owner in plan.owners
                }
                for owner in plan.owners:
                    shard_ids = child_shards[owner]
                    if owner == target.source_owner or not shard_ids:
                        continue
                    prefix_ledger.transfer_shards(
                        shard_ids,
                        from_owner=target.source_owner,
                        to_owner=owner,
                    )
            except PrefixShardLedgerError as exc:
                all_completed = False
                refinement.set_disposition(
                    "incomplete",
                    f"recursive split/transfer failed for {target.root_move}: {exc}",
                )
                break

            record = refinement.register_target(
                target=target,
                source_shard_id=source_shard_id,
                oracle_position_command=descendant_position.command(),
                oracle_children=children,
                child_partition=child_partition,
                child_shards=child_shards,
            )

            prepared_instances: list[str] = []
            setup_ok = True
            for owner in plan.owners:
                moves = child_partition[owner]
                if not moves:
                    continue
                instance = plan.participants[owner]
                spec = self.runtime.spec(instance)
                stream_path = (
                    refinement.refinement_dir
                    / target.target_id
                    / f"{instance}.jsonl"
                )
                opened, stream = self._within_prepare_budget(
                    f"{active.run.run_id}-refine-{target.target_id}-{instance}-stream",
                    discard=lambda late: self._release_late_stream(None, late),
                    work=lambda spec=spec, instance=instance, stream_path=stream_path, descendant_position=descendant_position: TelemetryStreamWriter(
                        instance=instance,
                        family=spec.family,
                        role=spec.role,
                        path=stream_path,
                        adapter_factory=self._adapter_factory(
                            family=spec.family,
                            instance=instance,
                            position_id=descendant_position.position_id,
                            variant=descendant_position.variant,
                        ),
                        track_events=True,
                    ),
                )
                if not opened or stream is None:
                    setup_ok = False
                    refinement.set_target_disposition(
                        target.target_id,
                        "incomplete",
                        f"REFINE telemetry stream setup failed for {instance}",
                    )
                    refinement.set_disposition(
                        "incomplete",
                        f"REFINE telemetry stream setup failed for {instance}",
                    )
                    break
                refinement.register_stream(target.target_id, stream)

            if setup_ok:
                for owner in plan.owners:
                    if not child_partition[owner]:
                        continue
                    instance = plan.participants[owner]
                    if (
                        active.cancelled
                        or self._closed
                        or active.anchor_completed.is_set()
                        or not self.runtime.shadow_available(instance)
                    ):
                        setup_ok = False
                        refinement.set_target_disposition(
                            target.target_id,
                            "incomplete",
                            "decision boundary or shadow health changed during REFINE setup",
                        )
                        refinement.set_disposition(
                            "incomplete",
                            "REFINE setup lost its decision/health preconditions",
                        )
                        break
                    try:
                        self.runtime.set_shadow_position(
                            instance, descendant_position.command()
                        )
                        prepared_instances.append(instance)
                        with self._lock:
                            active.refinement_positioned.add(instance)
                    except ControllerRuntimeError as exc:
                        setup_ok = False
                        refinement.set_target_disposition(
                            target.target_id,
                            "incomplete",
                            f"could not position {instance} for REFINE: {exc}",
                        )
                        refinement.set_disposition(
                            "incomplete",
                            f"could not position {instance} for REFINE",
                        )
                        break

            if not setup_ok:
                self._restore_refinement_instances(
                    active, refinement, target.target_id, prepared_instances
                )
                all_completed = False
                break

            dispatched_all = True
            for owner in plan.owners:
                if not child_partition[owner]:
                    continue
                if not self._dispatch_refinement_stage(
                    active,
                    target_id=target.target_id,
                    owner=owner,
                    descendant_position=descendant_position,
                ):
                    dispatched_all = False
                    refinement.set_target_disposition(
                        target.target_id,
                        "incomplete",
                        f"REFINE dispatch failed for {owner}",
                    )
                    refinement.set_disposition(
                        "incomplete",
                        f"REFINE dispatch failed for target {target.root_move}",
                    )
                    break

            if not dispatched_all:
                refinement.request_target_abort(
                    target.target_id,
                    "partial REFINE dispatch; stopping already-dispatched stages",
                )
                pending_instances = [
                    stage.instance
                    for stage in refinement.active_stages()
                    if stage.target_id == target.target_id
                ]
                if pending_instances:
                    self._stop_instances(list(dict.fromkeys(pending_instances)))

            self._await_refinement_target(active, target.target_id)
            restored = self._restore_refinement_instances(
                active, refinement, target.target_id, prepared_instances
            )

            required_owners = {
                owner for owner in plan.owners if child_partition[owner]
            }
            completed_owners = {
                stage.owner
                for stage in record.stages.values()
                if stage.disposition == "completed"
            }
            if dispatched_all and restored and completed_owners == required_owners:
                refinement.set_target_disposition(target.target_id, "completed")
            else:
                all_completed = False
                if record.disposition == "running":
                    refinement.set_target_disposition(
                        target.target_id,
                        "incomplete",
                        "not all required REFINE stages completed and restored",
                    )
                refinement.set_disposition(
                    "incomplete",
                    "at least one REFINE target did not complete cleanly",
                )
                break

        if all_completed and refinement.disposition == "running":
            refinement.set_disposition("completed")

    def _dispatch_refinement_stage(
        self,
        active: _ActiveRun,
        *,
        target_id: str,
        owner: str,
        descendant_position: PositionRequest,
    ) -> bool:
        refinement = active.refinement
        settings = self.runtime.config.refinement
        if refinement is None or settings is None:
            return False
        record = refinement.target_record(target_id)
        if record is None:
            return False
        child_moves = record.child_partition.get(owner, ())
        shard_ids = record.child_shards.get(owner, ())
        if not child_moves or not shard_ids:
            return False
        instance = refinement.plan.participants[owner]
        spec = self.runtime.spec(instance)
        stream = refinement.stream(target_id, instance)
        if stream is None:
            return False
        try:
            dispatch = compile_descendant_region(
                active.context.position,
                parent_prefix=(record.target.root_move,),
                child_moves=child_moves,
                limit=dict(settings.dispatch_limit),
            )
        except SearchRequestError:
            return False
        if dispatch.position != descendant_position:
            return False

        generation = active.generation
        search_id = (
            f"{active.run.run_id}:refine:{target_id}:{instance}"
        )

        def on_info(token: int, line: str) -> None:
            return None

        def on_complete(token: int, line: str) -> None:
            self._on_refinement_complete(
                generation,
                target_id,
                owner,
                token,
                line,
            )

        reservation_key = f"refine:{target_id}:{owner}"
        with self._lock:
            if (
                active.cancelled
                or self._closed
                or active.anchor_completed.is_set()
                or not self.runtime.shadow_available(instance)
            ):
                return False
            if not self._authorize_specialist(
                active,
                key=reservation_key,
                phase="refine",
                owner=owner,
                target_id=target_id,
            ):
                return False
            try:
                for shard_id in shard_ids:
                    refinement.ledger.activate_shard(shard_id, owner=owner)
            except PrefixShardLedgerError as exc:
                self._release_specialist(
                    active,
                    key=reservation_key,
                    reason="REFINE reservation released because shard activation failed",
                )
                refinement.note(
                    f"could not activate REFINE shards for {owner}: {exc}"
                )
                return False

            refinement.activate_stream(target_id, instance)
            stream.begin_stage(
                search_id=search_id,
                position=descendant_position.telemetry_position(),
                request=parse_go_request(dispatch.go_command),
                controller={
                    "execution_mode": self.runtime.config.telemetry_execution_mode,
                    "phase": "REFINE",
                    "instance_role": "shadow",
                    "owner": owner,
                    "decision_authority": False,
                },
                observed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
            )
            prefixes = tuple(
                (record.target.root_move, move) for move in child_moves
            )
            stage = refinement.record_dispatch(
                target_id=target_id,
                owner=owner,
                instance=instance,
                family=spec.family,
                search_id=search_id,
                position_command=descendant_position.command(),
                command=dispatch.go_command,
                child_moves=child_moves,
                shard_ids=shard_ids,
                prefixes=prefixes,
                dispatched_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
            )
            dispatched = self.runtime.start_shadow_search(
                instance,
                dispatch.go_command,
                token=generation,
                on_info=on_info,
                on_complete=on_complete,
            )
        if not dispatched:
            self._release_specialist(
                active,
                key=reservation_key,
                reason="REFINE reservation released because backend dispatch failed",
            )
            refinement.record_completion(
                stage,
                completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                disposition="failed",
                failure="REFINE dispatch rejected; instance unavailable",
            )
            return False
        return True

    def _on_refinement_complete(
        self,
        generation: int,
        target_id: str,
        owner: str,
        token: int,
        line: str,
    ) -> None:
        with self._lock:
            active = self._run
            if active is None or active.generation != generation or token != generation:
                return
            refinement = active.refinement
            stage = (
                None
                if refinement is None
                else refinement.stage_for_target_owner(target_id, owner)
            )
            elapsed = (time.monotonic() - active.started_monotonic) * 1000.0
        if refinement is None or stage is None:
            return

        tokens = line.split()
        bestmove = (
            tokens[1]
            if line.startswith("bestmove ") and len(tokens) > 1
            else None
        )
        allowed = set(stage.child_moves)
        failure: str | None = None
        if bestmove is not None and bestmove not in allowed:
            failure = (
                f"REFINE instance {stage.instance} answered {bestmove} outside "
                f"its child region {list(stage.child_moves)}"
            )

        stream = refinement.stream(target_id, stage.instance)
        if failure is None and stream is not None:
            if not stream.drain_barrier(0.25):
                failure = (
                    f"REFINE telemetry for {stage.instance} did not drain before "
                    "the completion audit"
                )
            elif stream.evidence_lossy:
                failure = f"REFINE telemetry for {stage.instance} lost evidence"
            elif stream.tracked_events_truncated:
                failure = (
                    f"REFINE live evidence for {stage.instance} exceeded the "
                    "tracked-event limit before the completion audit"
                )
            else:
                for event in stream.tracked_events():
                    if event.get("event_type") != "candidate.update":
                        continue
                    candidate = event.get("candidate") or {}
                    move = candidate.get("move")
                    pv = candidate.get("pv") or []
                    if move not in allowed or (pv and pv[0] not in allowed):
                        failure = (
                            f"REFINE telemetry for {stage.instance} escaped "
                            "its assigned child region"
                        )
                        break

        self._settle_specialist(
            active,
            key=f"refine:{target_id}:{owner}",
            dispatched_ms=stage.dispatched_ms,
            completed_ms=elapsed,
            instance=stage.instance,
        )

        target_abort = refinement.target_abort_requested(target_id)
        if failure is None and not active.cancelled and not target_abort:
            try:
                for shard_id in stage.shard_ids:
                    refinement.ledger.seal_shard(shard_id, owner=owner)
            except PrefixShardLedgerError as exc:
                failure = f"REFINE shard sealing failed for {owner}: {exc}"

        if failure is not None:
            self.runtime.record_shadow_failure(
                stage.instance, failure, generation=active.generation
            )
            refinement.record_completion(
                stage,
                completed_ms=elapsed,
                disposition="failed",
                bestmove=bestmove,
                stop_reason="refine_region_escape_or_loss",
                failure=failure,
            )
            refinement.set_target_disposition(target_id, "incomplete", failure)
            refinement.set_disposition("incomplete", failure)
            return

        disposition = (
            "stopped" if active.cancelled or target_abort else "completed"
        )
        stop_reason = (
            active.cancel_reason
            if active.cancelled
            else "refine_target_abort"
            if target_abort
            else None
        )
        refinement.record_completion(
            stage,
            completed_ms=elapsed,
            disposition=disposition,
            bestmove=bestmove,
            stop_reason=stop_reason,
        )

    def _await_refinement_target(
        self,
        active: _ActiveRun,
        target_id: str,
    ) -> None:
        refinement = active.refinement
        if refinement is None:
            return
        interval = 0.02
        stage_budget_ms = float(self.settings.stage_timeout_s) * 1000.0

        while True:
            pending = [
                stage
                for stage in refinement.active_stages()
                if stage.target_id == target_id
            ]
            if not pending:
                return
            elapsed_ms = (time.monotonic() - active.started_monotonic) * 1000.0
            overrun = [
                stage
                for stage in pending
                if elapsed_ms - stage.dispatched_ms > stage_budget_ms
            ]
            if overrun:
                refinement.request_target_abort(
                    target_id,
                    "REFINE stage deadline exceeded; stopping target stages",
                )
                instances = list(dict.fromkeys(stage.instance for stage in pending))
                self._stop_instances(instances)
                deadline = time.monotonic() + self.settings.drain_timeout_s
                for stage in pending:
                    stage.done.wait(timeout=max(0.0, deadline - time.monotonic()))
                for stage in pending:
                    if stage.done.is_set():
                        continue
                    message = (
                        f"REFINE instance {stage.instance} exceeded the stage "
                        "budget and did not drain after stop"
                    )
                    self.runtime.record_shadow_failure(
                        stage.instance, message, generation=active.generation
                    )
                    refinement.record_completion(
                        stage,
                        completed_ms=(time.monotonic() - active.started_monotonic) * 1000.0,
                        disposition="failed",
                        failure=message,
                    )
                    refinement.set_target_disposition(
                        target_id, "incomplete", message
                    )
                refinement.set_disposition(
                    "incomplete", "REFINE stage deadline exceeded"
                )
                return
            self._wait_slice(pending, interval)

    def _restore_refinement_instances(
        self,
        active: _ActiveRun,
        refinement: RefinementRun,
        target_id: str,
        instances: list[str],
    ) -> bool:
        restored = True
        for instance in dict.fromkeys(instances):
            try:
                if not self.runtime.shadow_available(instance):
                    restored = False
                    continue
                self.runtime.restore_shadow_position(instance)
            except ControllerRuntimeError as exc:
                restored = False
                message = (
                    f"REFINE instance {instance} could not restore external position: {exc}"
                )
                refinement.note(message)
                refinement.set_target_disposition(
                    target_id, "incomplete", message
                )
                refinement.set_disposition("incomplete", message)
            finally:
                with self._lock:
                    active.refinement_positioned.discard(instance)
        return restored

    def _restore_all_refinement_positions(self, active: _ActiveRun) -> bool:
        """Best-effort generation cleanup for temporarily repositioned shadows."""

        ok = True
        with self._lock:
            instances = list(active.refinement_positioned)
        for instance in instances:
            try:
                if not self.runtime.shadow_available(instance):
                    ok = False
                    continue
                self.runtime.restore_shadow_position(instance)
            except ControllerRuntimeError as exc:
                ok = False
                if active.refinement is not None:
                    message = (
                        f"REFINE cleanup could not restore {instance}: {exc}"
                    )
                    active.refinement.note(message)
                    active.refinement.set_disposition("incomplete", message)
            finally:
                with self._lock:
                    active.refinement_positioned.discard(instance)
        return ok

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
            #
            # BOUNDED, like the pre-anchor path. A blocking open here stalls the
            # coordinator after earlier owners may already be searching:
            # `_await_completion` is never reached, so no stage deadline and no
            # active-routing wall check runs while those engines keep spending
            # envelope, and a later quiesce cannot finish either. Past the bound
            # this owner contributes no evidence rather than freezing the ones
            # that do.
            spec = self.runtime.spec(state.instance)
            stream_path = run.run_dir / f"{state.instance}.jsonl"
            opened, stream = self._within_prepare_budget(
                f"{run.run_id}-{state.instance}-stream",
                discard=lambda late: self._release_late_stream(None, late),
                work=lambda: TelemetryStreamWriter(
                    instance=state.instance,
                    family=spec.family,
                    role=spec.role,
                    path=stream_path,
                    adapter_factory=self._adapter_factory(
                        family=spec.family,
                        instance=state.instance,
                        position_id=active.context.position.position_id,
                        variant=active.context.position.variant,
                    ),
                    track_events=self.router is not None,
                ),
            )
            if not opened or stream is None:
                run.note(
                    f"owner {state.owner} was not dispatched: its telemetry stream "
                    f"could not be opened within {self.settings.prepare_budget_s}s"
                )
                return False
            state.stream = stream
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
        budget = self._stage_budget()
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
                # `_cut_loose_overrunning` cancels the run, which sends `stop`
                # to EVERY pending owner -- so every pending owner has to be
                # drained and quarantined, not only the ones that overran. A
                # non-overrunning worker slow to answer `stop` was otherwise
                # left running while the run finalized and cleared `_run`.
                self._cut_loose_overrunning(active, pending, overrun=overrun)
                return
            self._wait_slice(pending, interval)
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

    @staticmethod
    def _wait_slice(pending: list, interval: float) -> None:
        """Wait at most `interval` in total for any pending owner to finish.

        ONE wait per checkpoint interval, not one per owner. Waiting `interval`
        on each pending owner in turn made routing checkpoints and stage
        deadline checks run every `len(pending) * interval`, so an authorized
        stop arrived late and the wall envelope or a per-stage budget could
        overrun by a multiple of the configured interval before anything
        looked.
        """
        slice_end = time.monotonic() + interval
        for state in pending:
            remaining = slice_end - time.monotonic()
            if remaining <= 0.0:
                return
            if state.done.wait(timeout=remaining):
                # An owner finished: reevaluate every owner now rather than
                # spending the rest of this slice waiting on the others.
                return

    def _stage_budget(self) -> float:
        """The declared per-stage cap, used exactly as configured.

        No clamp: configuration already requires a positive, finite duration,
        and silently replacing a declared 50 ms cap with 1 s let a stuck worker
        run twenty times its declared budget before cancellation even began.
        """
        return float(self.settings.stage_timeout_s)

    def _cut_loose_overrunning(
        self, active: _ActiveRun, pending: list, *, overrun: list | None = None
    ) -> None:
        """Stop stages past the per-stage budget, quarantining any that ignore it.

        A worker that overruns and then ignores `stop` used to be waited on for
        one drain timeout and then simply left: nothing recorded it as failed,
        so the run finalized and cleared `_run` while `shadow_available()` still
        called the process healthy, and the next `position` went to an engine
        still executing the previous generation. This is the same handling
        `quiesce()` applies to a worker that misses its drain deadline.
        """
        for state in overrun if overrun is not None else pending:
            active.run.note(
                f"owner {state.owner} exceeded the declared stage budget "
                f"({self.settings.stage_timeout_s}s)"
            )
        self._stop_instances(self._cancel_locked(active, reason="stage_deadline"))
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
