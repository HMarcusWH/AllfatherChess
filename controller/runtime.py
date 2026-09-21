"""Transactional managed-engine runtime for the Generation 1 UCI shell.

The runtime owns process identity, authority separation, synchronized chess
state, and the legal-root oracle. It deliberately owns no routing policy, no
residual computation, and no replay analysis.
"""

from __future__ import annotations

import glob
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from adapters.process import UciProcess, UciProcessError
from adapters.telemetry import SUPPORTED_SCORE_TYPES


class RuntimeError(RuntimeError):
    """Raised when the managed backend runtime cannot preserve its contract."""


_PERFT_ROOT_RE = re.compile(r"^([a-h][1-8][a-h][1-8][qrbn]?):\s+(\d+)$")
_PERFT_TOTAL_RE = re.compile(r"^Nodes searched:\s+(\d+)$")

SOLVER_FAMILIES = ("stockfish", "reckless", "lc0")

#: ``anchor`` holds outward decision authority. ``managed`` instances are
#: synchronized authority-critical backends from the legacy anchor profile.
#: ``shadow`` instances are restricted observational workers whose death is
#: evidence rather than an authority failure.
INSTANCE_ROLES = ("anchor", "managed", "shadow")

AUTHORITY_ROLES = ("anchor", "managed")

EXECUTION_MODES = ("anchor", "shadow", "active")

#: telemetry v1 ``controller.execution_mode`` value for each runtime mode.
TELEMETRY_EXECUTION_MODE = {
    "anchor": "baseline",
    "shadow": "shadow",
    "active": "active",
}


@dataclass(frozen=True)
class BackendSpec:
    """One managed engine instance.

    ``name`` is the process-role identity (for example ``stockfish-shadow``).
    ``family`` is the solver-family identity (for example ``stockfish``) and
    carries the score/work semantics. The two must never be conflated.
    """

    name: str
    family: str
    role: str
    binary: Path
    cwd: Path
    args: tuple[str, ...]
    options: dict[str, object]


@dataclass(frozen=True)
class ShadowSettings:
    """Shadow/active execution settings.

    ``owners`` are the *solver families* that own ledger shards. The unrestricted
    anchor is intentionally absent: it is not an exploration owner.
    """

    owners: tuple[str, ...]
    instance_by_owner: dict[str, str]
    oracle: str
    replay_root: Path
    partition: str
    dispatch_limit: dict[str, object]
    lc0_score_type: str
    oracle_timeout_s: float
    drain_timeout_s: float
    on_anchor_complete: str

    def instance(self, owner: str) -> str:
        return self.instance_by_owner[owner]


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    root: Path
    mode: str
    anchor: str
    backends: dict[str, BackendSpec]
    shadow: ShadowSettings | None = None
    budget: dict[str, object] | None = None
    routing: dict[str, object] | None = None

    @property
    def instances(self) -> dict[str, BackendSpec]:
        """Alias for ``backends``; both are keyed by process-role identity."""
        return self.backends

    def instances_with_role(self, *roles: str) -> tuple[BackendSpec, ...]:
        wanted = set(roles)
        return tuple(spec for spec in self.backends.values() if spec.role in wanted)

    @property
    def telemetry_execution_mode(self) -> str:
        return TELEMETRY_EXECUTION_MODE[self.mode]


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _require_positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} must be a positive number")
    number = float(value)
    if not number > 0:
        raise RuntimeError(f"{label} must be a positive number")
    return number


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


def _build_spec(
    *,
    root: Path,
    raw: dict[str, object],
    name: str,
    family: str,
    role: str,
) -> BackendSpec:
    cwd_value = raw.get("cwd", ".")
    if not isinstance(cwd_value, str) or not cwd_value:
        raise RuntimeError(f"backend {name}: cwd must be a non-empty string")
    args_value = raw.get("args", [])
    if not isinstance(args_value, list) or not all(isinstance(item, str) for item in args_value):
        raise RuntimeError(f"backend {name}: args must be an array of strings")
    options = _require_object(raw.get("options", {}), f"backend {name}.options")
    return BackendSpec(
        name=name,
        family=family,
        role=role,
        binary=_resolve_binary(root, raw, name),
        cwd=(root / cwd_value).resolve(),
        args=tuple(args_value),
        options=dict(options),
    )


def _load_legacy_backends(data: dict[str, object], root: Path) -> tuple[str, dict[str, BackendSpec]]:
    """Load the frozen PR #9/#10 anchor profile.

    Instance identity equals solver-family identity in this shape, so existing
    configs, contracts, and tests keep working unchanged.
    """
    mode = data.get("mode")
    if mode != "anchor":
        raise RuntimeError(f"runtime config schema_version 1 supports only mode='anchor', got {mode!r}")
    anchor = data.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise RuntimeError("runtime config anchor must be a non-empty string")

    raw_backends = _require_object(data.get("backends"), "backends")
    expected = set(SOLVER_FAMILIES)
    if set(raw_backends) != expected:
        raise RuntimeError(
            f"runtime config backends must be exactly {sorted(expected)}, got {sorted(raw_backends)}"
        )
    if anchor not in expected:
        raise RuntimeError(f"anchor backend is not configured: {anchor}")

    specs: dict[str, BackendSpec] = {}
    for family in SOLVER_FAMILIES:
        raw = _require_object(raw_backends[family], f"backend {family}")
        specs[family] = _build_spec(
            root=root,
            raw=raw,
            name=family,
            family=family,
            role="anchor" if family == anchor else "managed",
        )
    return anchor, specs


def _load_instances(data: dict[str, object], root: Path) -> tuple[str, dict[str, BackendSpec]]:
    anchor = data.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise RuntimeError("runtime config anchor must be a non-empty string")

    raw_instances = _require_object(data.get("instances"), "instances")
    if not raw_instances:
        raise RuntimeError("runtime config instances must not be empty")

    specs: dict[str, BackendSpec] = {}
    for name in sorted(raw_instances):
        if not name:
            raise RuntimeError("instance names must be non-empty strings")
        raw = _require_object(raw_instances[name], f"instance {name}")
        family = raw.get("family")
        if family not in SOLVER_FAMILIES:
            raise RuntimeError(f"instance {name}: family must be one of {list(SOLVER_FAMILIES)}, got {family!r}")
        role = raw.get("role")
        if role not in INSTANCE_ROLES:
            raise RuntimeError(f"instance {name}: role must be one of {list(INSTANCE_ROLES)}, got {role!r}")
        specs[name] = _build_spec(root=root, raw=raw, name=name, family=str(family), role=str(role))

    anchors = sorted(name for name, spec in specs.items() if spec.role == "anchor")
    if anchors != [anchor]:
        raise RuntimeError(
            f"exactly one instance must hold the anchor role and it must be {anchor!r}; found {anchors}"
        )
    return anchor, specs


def _load_shadow_settings(
    data: dict[str, object],
    *,
    specs: dict[str, BackendSpec],
    anchor: str,
    root: Path,
) -> ShadowSettings:
    raw = _require_object(data.get("shadow"), "shadow")

    raw_owners = raw.get("owners")
    if not isinstance(raw_owners, list) or not raw_owners:
        raise RuntimeError("shadow.owners must be a non-empty array of solver families")
    owners: list[str] = []
    for owner in raw_owners:
        if owner not in SOLVER_FAMILIES:
            raise RuntimeError(f"shadow.owners contains unknown solver family: {owner!r}")
        if owner in owners:
            raise RuntimeError(f"shadow.owners contains a duplicate owner: {owner!r}")
        owners.append(str(owner))

    raw_map = _require_object(raw.get("instance_by_owner"), "shadow.instance_by_owner")
    if sorted(raw_map) != sorted(owners):
        raise RuntimeError(
            f"shadow.instance_by_owner keys must match shadow.owners exactly; "
            f"owners={sorted(owners)}, keys={sorted(raw_map)}"
        )
    instance_by_owner: dict[str, str] = {}
    seen_instances: set[str] = set()
    for owner in owners:
        instance = raw_map[owner]
        if not isinstance(instance, str) or instance not in specs:
            raise RuntimeError(f"shadow owner {owner!r} references unknown instance: {instance!r}")
        spec = specs[instance]
        if spec.role != "shadow":
            raise RuntimeError(f"shadow owner {owner!r} instance {instance!r} must have role 'shadow'")
        if spec.family != owner:
            raise RuntimeError(
                f"shadow owner {owner!r} instance {instance!r} has solver family {spec.family!r}"
            )
        if instance in seen_instances:
            raise RuntimeError(f"shadow instance {instance!r} is assigned to more than one owner")
        seen_instances.add(instance)
        instance_by_owner[owner] = instance

    unmapped = sorted(
        name for name, spec in specs.items() if spec.role == "shadow" and name not in seen_instances
    )
    if unmapped:
        raise RuntimeError(f"shadow instances without a ledger owner are forbidden: {unmapped}")

    oracle = raw.get("oracle")
    if not isinstance(oracle, str) or oracle not in specs:
        raise RuntimeError(f"shadow.oracle must name a configured instance, got {oracle!r}")
    if specs[oracle].family != "stockfish":
        raise RuntimeError("shadow.oracle must be a Stockfish-family instance (perft oracle)")
    if oracle == anchor:
        raise RuntimeError(
            "shadow.oracle must not be the outward anchor: the anchor may not be blocked by 'go perft 1'"
        )

    partition = raw.get("partition", "root_index_modulo")
    if partition != "root_index_modulo":
        raise RuntimeError(f"unsupported shadow.partition: {partition!r}")

    dispatch = _require_object(raw.get("dispatch_limit", {"nodes": 20000}), "shadow.dispatch_limit")
    if sorted(dispatch) != ["nodes"]:
        raise RuntimeError("shadow.dispatch_limit currently supports exactly the 'nodes' key")
    nodes = dispatch["nodes"]
    if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < 1:
        raise RuntimeError("shadow.dispatch_limit.nodes must be a positive integer")

    score_type = raw.get("lc0_score_type", "centipawn")
    if not isinstance(score_type, str) or not score_type:
        raise RuntimeError("shadow.lc0_score_type must be a non-empty string")
    if score_type not in SUPPORTED_SCORE_TYPES:
        # Caught here rather than inside the telemetry writer thread, where a
        # typo would surface as an adapter error after every engine had already
        # started and the LC0 search had been dispatched, leaving the run with
        # no usable LC0 evidence instead of a refusal.
        raise RuntimeError(
            f"shadow.lc0_score_type {score_type!r} is not a supported LC0 ScoreType; "
            f"supported: {sorted(SUPPORTED_SCORE_TYPES)}"
        )

    replay_value = raw.get("replay_root", "build/replays")
    if not isinstance(replay_value, str) or not replay_value:
        raise RuntimeError("shadow.replay_root must be a non-empty path string")
    replay_root = Path(replay_value)
    if not replay_root.is_absolute():
        replay_root = (root / replay_value).resolve()

    on_anchor_complete = raw.get("on_anchor_complete", "drain")
    if on_anchor_complete not in ("drain", "cancel"):
        raise RuntimeError(
            f"shadow.on_anchor_complete must be 'drain' or 'cancel', got {on_anchor_complete!r}"
        )

    return ShadowSettings(
        owners=tuple(owners),
        instance_by_owner=instance_by_owner,
        oracle=oracle,
        replay_root=replay_root,
        partition=str(partition),
        dispatch_limit={"nodes": int(nodes)},
        lc0_score_type=score_type,
        oracle_timeout_s=_require_positive_number(raw.get("oracle_timeout_s", 10.0), "shadow.oracle_timeout_s"),
        drain_timeout_s=_require_positive_number(raw.get("drain_timeout_s", 5.0), "shadow.drain_timeout_s"),
        on_anchor_complete=str(on_anchor_complete),
    )


def load_runtime_config(path: Path) -> RuntimeConfig:
    path = path.resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load runtime config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("runtime config root must be an object")
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in (1, 2):
        raise RuntimeError(f"unsupported runtime config schema_version: {version!r}")

    root_value = data.get("root", "..")
    if not isinstance(root_value, str) or not root_value:
        raise RuntimeError("runtime config root must be a non-empty path string")
    root = (path.parent / root_value).resolve()

    if version == 1:
        anchor, specs = _load_legacy_backends(data, root)
        return RuntimeConfig(path=path, root=root, mode="anchor", anchor=anchor, backends=specs)

    mode = data.get("mode")
    if mode not in EXECUTION_MODES:
        raise RuntimeError(f"runtime config mode must be one of {list(EXECUTION_MODES)}, got {mode!r}")
    anchor, specs = _load_instances(data, root)

    shadow: ShadowSettings | None = None
    if mode in ("shadow", "active"):
        shadow = _load_shadow_settings(data, specs=specs, anchor=anchor, root=root)
    elif "shadow" in data:
        raise RuntimeError("shadow settings are only valid in shadow/active mode")

    budget = data.get("budget")
    if budget is not None:
        budget = _require_object(budget, "budget")
    routing = data.get("routing")
    if routing is not None:
        routing = _require_object(routing, "routing")
    if mode == "active" and routing is None:
        raise RuntimeError("active mode requires an explicit routing configuration")
    if mode == "active" and budget is None:
        raise RuntimeError("active mode requires an explicit budget configuration")

    return RuntimeConfig(
        path=path,
        root=root,
        mode=mode,
        anchor=anchor,
        backends=specs,
        shadow=shadow,
        budget=budget,
        routing=routing,
    )


@dataclass
class ShadowHealth:
    """Observational health of one shadow instance.

    A shadow failure is recorded evidence. It never grants outward authority and
    never fails the anchor search.
    """

    instance: str
    alive: bool = True
    failure: str | None = None
    failed_generation: int | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "instance": self.instance,
            "alive": self.alive,
            "failure": self.failure,
            "failed_generation": self.failed_generation,
        }


class BackendManager:
    """Own every managed engine process and separate authority from observation."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.backends: dict[str, UciProcess] = {}
        self._lock = threading.RLock()
        self._started = False
        self._closing = False
        self._unhealthy_reason: str | None = None
        self._failure_handler: Callable[[str, int | None], None] | None = None
        self._shadow_health: dict[str, ShadowHealth] = {
            spec.name: ShadowHealth(instance=spec.name)
            for spec in config.instances_with_role("shadow")
        }
        self._shadow_exit_handler: Callable[[str, int | None, int | None], None] | None = None
        self._observer: Callable[[str, int, str, float], None] | None = None
        self._position_command: str | None = None
        self._chess960 = False

        # Deterministic startup/shutdown ordering: the outward anchor starts
        # first so its readiness is never gated behind observational workers.
        anchor_first = [config.anchor]
        anchor_first += [name for name in config.backends if name != config.anchor]
        self._startup_order: tuple[str, ...] = tuple(anchor_first)

    @classmethod
    def from_path(cls, path: Path) -> "BackendManager":
        return cls(load_runtime_config(path))

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def anchor_name(self) -> str:
        return self.config.anchor

    @property
    def shadow_instances(self) -> tuple[str, ...]:
        if self.config.shadow is None:
            return ()
        return tuple(
            self.config.shadow.instance(owner) for owner in self.config.shadow.owners
        )

    @property
    def authority_instances(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self._startup_order
            if self.config.backends[name].role in AUTHORITY_ROLES
        )

    @property
    def position_command(self) -> str | None:
        with self._lock:
            return self._position_command

    @property
    def chess960(self) -> bool:
        with self._lock:
            return self._chess960

    def spec(self, instance: str) -> BackendSpec:
        try:
            return self.config.backends[instance]
        except KeyError as exc:
            raise RuntimeError(f"unknown engine instance: {instance!r}") from exc

    def process(self, instance: str) -> UciProcess:
        try:
            return self.backends[instance]
        except KeyError as exc:
            raise RuntimeError(f"engine instance is not running: {instance!r}") from exc

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def set_failure_handler(self, handler: Callable[[str, int | None], None]) -> None:
        with self._lock:
            self._failure_handler = handler

    def set_shadow_exit_handler(
        self, handler: Callable[[str, int | None, int | None], None] | None
    ) -> None:
        """Install the shadow coordinator's unexpected-exit notification hook."""
        with self._lock:
            self._shadow_exit_handler = handler

    def set_instance_observer(
        self, observer: Callable[[str, int, str, float], None] | None
    ) -> None:
        """Observe every dispatched search line as it is dequeued by the reader.

        The observer runs on the owning process's stdout reader thread. It must
        therefore never block: implementations hand work to their own writer
        thread.
        """
        with self._lock:
            self._observer = observer

    @property
    def healthy(self) -> bool:
        """Authority health.

        Shadow instances are deliberately excluded: an observational worker
        death is recorded evidence and must not fail the outward search.
        """
        with self._lock:
            if not self._started or self._closing or self._unhealthy_reason is not None:
                return False
            authority = self.authority_instances
            if len(authority) != len([
                name for name in self.config.backends if self.config.backends[name].role in AUTHORITY_ROLES
            ]):
                return False
            return all(
                name in self.backends and self.backends[name].alive for name in authority
            )

    @property
    def unhealthy_reason(self) -> str | None:
        with self._lock:
            return self._unhealthy_reason

    def shadow_health(self) -> dict[str, ShadowHealth]:
        with self._lock:
            return {name: ShadowHealth(**vars(health)) for name, health in self._shadow_health.items()}

    def shadow_available(self, instance: str) -> bool:
        with self._lock:
            health = self._shadow_health.get(instance)
            if health is None or not health.alive:
                return False
            process = self.backends.get(instance)
            return process is not None and process.alive

    def record_shadow_failure(self, instance: str, message: str, *, generation: int | None = None) -> None:
        with self._lock:
            health = self._shadow_health.get(instance)
            if health is None:
                raise RuntimeError(f"unknown shadow instance: {instance!r}")
            health.alive = False
            if health.failure is None:
                health.failure = message
                health.failed_generation = generation

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
            spec = self.config.backends.get(name)
            shadow_handler = self._shadow_exit_handler
        if spec is not None and spec.role == "shadow":
            # Observational health only. The outward search keeps running.
            self.record_shadow_failure(
                name,
                f"shadow instance {name} exited unexpectedly; rc={rc}",
                generation=active_token,
            )
            if shadow_handler is not None:
                try:
                    shadow_handler(name, rc, active_token)
                except Exception:  # pragma: no cover - coordinator isolation
                    pass
            return
        self._notify_failure(f"backend {name} exited unexpectedly; rc={rc}", active_token)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("backend runtime already started")
            self._started = True
            self._closing = False
            self._unhealthy_reason = None

        try:
            for name in self._startup_order:
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

    def _require_started(self) -> None:
        with self._lock:
            if not self._started or self._closing:
                raise RuntimeError("backend runtime is not active")

    def _for_each_instance(
        self,
        action: Callable[[str, UciProcess], None],
        *,
        label: str,
        instances: Iterable[str] | None = None,
    ) -> None:
        """Apply one synchronization action across managed instances.

        Authority failures escalate and fail closed. Shadow failures are
        recorded as observational evidence so an outward search survives them.
        """
        names = self._startup_order if instances is None else tuple(instances)
        for name in names:
            process = self.backends.get(name)
            if process is None:
                continue
            spec = self.config.backends[name]
            if spec.role == "shadow":
                if not self.shadow_available(name):
                    continue
                try:
                    action(name, process)
                except UciProcessError as exc:
                    self.record_shadow_failure(name, f"{label} failed: {exc}")
                continue
            try:
                action(name, process)
            except UciProcessError as exc:
                self._notify_failure(f"{label} failed for {name}: {exc}", None)
                raise RuntimeError(str(exc)) from exc

    def ready_all(self) -> None:
        self._require_started()
        self._for_each_instance(lambda name, process: process.ready(), label="backend readiness")
        self._require_healthy()

    def set_chess960(self, enabled: bool) -> None:
        self._require_healthy()
        self._for_each_instance(
            lambda name, process: process.set_option("UCI_Chess960", enabled),
            label="UCI_Chess960 synchronization",
        )
        with self._lock:
            self._chess960 = bool(enabled)
        self.ready_all()

    def new_game(self) -> None:
        self._require_healthy()
        self._for_each_instance(lambda name, process: process.new_game(), label="ucinewgame synchronization")
        with self._lock:
            self._position_command = None
        self.ready_all()

    def set_position(self, command: str) -> None:
        self._require_healthy()
        self._for_each_instance(
            lambda name, process: process.send_position(command),
            label="position synchronization",
        )
        with self._lock:
            self._position_command = command

    # ------------------------------------------------------------------
    # legal-root oracle
    # ------------------------------------------------------------------

    @property
    def oracle_name(self) -> str:
        """Instance that answers ``go perft 1``.

        In shadow/active mode this is a dedicated Stockfish shadow process so
        the outward anchor is never blocked by root qualification.
        """
        if self.config.shadow is not None:
            return self.config.shadow.oracle
        return self.config.anchor

    def legal_root_moves(self, *, instance: str | None = None, timeout: float | None = None) -> tuple[str, ...]:
        """Return canonical legal root moves from a Stockfish depth-1 perft oracle."""
        self._require_healthy()
        name = self.oracle_name if instance is None else instance
        spec = self.spec(name)
        if spec.family != "stockfish":
            raise RuntimeError("root legal-move oracle requires a Stockfish-family instance")
        process = self.backends.get(name)
        if process is None or not process.alive:
            raise RuntimeError(f"legal-root oracle instance is unavailable: {name}")
        if timeout is None:
            timeout = 10.0 if self.config.shadow is None else self.config.shadow.oracle_timeout_s

        shadow_oracle = spec.role == "shadow"
        try:
            lines = process.run_idle_request(
                "go perft 1",
                lambda line: _PERFT_TOTAL_RE.fullmatch(line) is not None,
                label=f"{name} go perft 1",
                timeout=timeout,
            )
        except UciProcessError as exc:
            message = f"legal-root oracle process failure: {exc}"
            if shadow_oracle:
                self.record_shadow_failure(name, message)
            else:
                self._notify_failure(message, None)
            raise RuntimeError(str(exc)) from exc

        def _fail(message: str) -> None:
            if shadow_oracle:
                self.record_shadow_failure(name, message)
            else:
                self._notify_failure(message, None)

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
                _fail(f"legal-root oracle returned depth-1 count {count} for {move}")
                raise RuntimeError(
                    f"Stockfish perft-1 root {move} reported count {count}, expected 1"
                )
            if move in seen:
                _fail(f"legal-root oracle returned duplicate root {move}")
                raise RuntimeError(f"Stockfish perft-1 returned duplicate root {move}")
            seen.add(move)
            roots.append(move)

        if total is None:
            _fail("legal-root oracle omitted Nodes searched total")
            raise RuntimeError("Stockfish perft-1 omitted Nodes searched total")
        if total != len(roots):
            _fail(f"legal-root oracle total mismatch: total={total}, roots={len(roots)}")
            raise RuntimeError(
                f"Stockfish perft-1 total mismatch: Nodes searched={total}, "
                f"parsed roots={len(roots)}"
            )
        return tuple(roots)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def _observed_callbacks(
        self,
        instance: str,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> tuple[Callable[[int, str], None], Callable[[int, str], None]]:
        def _info(token: int, line: str) -> None:
            with self._lock:
                observer = self._observer
            if observer is not None:
                observer(instance, token, line, time.monotonic())
            on_info(token, line)

        def _complete(token: int, line: str) -> None:
            with self._lock:
                observer = self._observer
            if observer is not None:
                observer(instance, token, line, time.monotonic())
            on_complete(token, line)

        return _info, _complete

    def start_anchor_search(
        self,
        command: str,
        *,
        token: int,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> None:
        self._require_healthy()
        info_cb, complete_cb = self._observed_callbacks(self.config.anchor, on_info, on_complete)
        try:
            self.anchor.start_search(
                command,
                token=token,
                on_info=info_cb,
                on_complete=complete_cb,
            )
        except UciProcessError as exc:
            self._notify_failure(f"anchor search dispatch failed: {exc}", token)
            raise RuntimeError(str(exc)) from exc

    def start_shadow_search(
        self,
        instance: str,
        command: str,
        *,
        token: int,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> bool:
        """Dispatch one restricted observational search.

        Returns ``False`` and records a shadow failure instead of raising: a
        shadow dispatch problem is evidence, never an authority failure.
        """
        spec = self.spec(instance)
        if spec.role != "shadow":
            raise RuntimeError(f"instance {instance!r} is not a shadow worker")
        if not self.shadow_available(instance):
            return False
        info_cb, complete_cb = self._observed_callbacks(instance, on_info, on_complete)
        try:
            self.backends[instance].start_search(
                command,
                token=token,
                on_info=info_cb,
                on_complete=complete_cb,
            )
        except UciProcessError as exc:
            self.record_shadow_failure(instance, f"shadow dispatch failed: {exc}", generation=token)
            return False
        return True

    def stop_instance(self, instance: str) -> None:
        process = self.backends.get(instance)
        if process is None:
            return
        try:
            process.stop()
        except UciProcessError as exc:
            spec = self.config.backends[instance]
            if spec.role == "shadow":
                self.record_shadow_failure(instance, f"shadow stop failed: {exc}")
                return
            self._notify_failure(f"{instance} stop failed: {exc}", process.active_token)
            raise RuntimeError(str(exc)) from exc

    def stop_anchor(self) -> None:
        self._require_started()
        self.stop_instance(self.config.anchor)

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
            for name in reversed(self._startup_order):
                process = self.backends.get(name)
                if process is not None:
                    process.close()
        finally:
            with self._lock:
                self._started = False
