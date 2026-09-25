"""Opt-in ONLINE-1 clock budgets. Timing policy, never chess authority.

All deadlines share the external go receipt origin. Increment is a planning
hint, not spendable time before the current move is returned. The immutable
plan retains the caller's request and the actual bounded anchor request.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from common.search_request import PositionRequest, parse_go_request, SearchRequestError
from controller.budget import ResourceEnvelope, BudgetError

POLICY = "clock_envelope_v1"
MAX_UCI_TIME = 2_147_483_647


class OnlineTimeError(ValueError):
    """The opted-in clock contract cannot admit this configuration/request."""


def _integer(value: Any, name: str, low: int, high: int = MAX_UCI_TIME) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise OnlineTimeError(f"{name} must be an integer in [{low}, {high}]")
    return value


@dataclass(frozen=True)
class OnlineTimeSettings:
    policy: str = POLICY
    moves_horizon: int = 30
    increment_fraction: float = 0.75
    max_move_ms: int = 10_000
    network_reserve_ms: int = 100
    stop_grace_ms: int = 100
    output_margin_ms: int = 10
    prepare_budget_ms: int = 10
    quiesce_budget_ms: int = 50
    cpu_parallelism: int = 4

    def __post_init__(self) -> None:
        if self.policy != POLICY:
            raise OnlineTimeError(f"unsupported online time policy: {self.policy!r}")
        _integer(self.moves_horizon, "moves_horizon", 1, 200)
        _integer(self.cpu_parallelism, "cpu_parallelism", 1, 1024)
        for name in ("max_move_ms", "stop_grace_ms", "output_margin_ms", "prepare_budget_ms", "quiesce_budget_ms"):
            _integer(getattr(self, name), name, 1, 60_000)
        _integer(self.network_reserve_ms, "network_reserve_ms", 0, 60_000)
        fraction = self.increment_fraction
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise OnlineTimeError("increment_fraction must be finite and in [0,1]")
        if self.max_move_ms <= self.stop_grace_ms + self.output_margin_ms:
            raise OnlineTimeError("max_move_ms must leave positive search time after stop/output margins")

    @classmethod
    def from_config(cls, raw: Any) -> OnlineTimeSettings | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise OnlineTimeError("online_time must be an object")
        if not isinstance(raw.get("enabled"), bool):
            raise OnlineTimeError("online_time.enabled must be boolean")
        unknown = set(raw) - {"enabled", *cls.__dataclass_fields__}
        if unknown:
            raise OnlineTimeError(f"unsupported online_time keys: {sorted(unknown)}")
        settings = cls(**{k: v for k, v in raw.items() if k != "enabled"})
        return settings if raw["enabled"] else None


def _limits(command: str) -> tuple[dict[str, int], tuple[str, ...]]:
    try:
        request = parse_go_request(command)
    except SearchRequestError as exc:
        raise OnlineTimeError(str(exc)) from exc
    if request.get("unknown_tokens"):
        raise OnlineTimeError("clock mode refuses unknown or malformed go tokens")
    limits: dict[str, int] = {}
    for item in request.get("limits", []):
        name = item["name"]
        if name in limits:
            raise OnlineTimeError(f"duplicate go limit: {name}")
        limits[name] = _integer(item["value"], name, 0)
    tokens = command.split()
    roots = tuple(request.get("root_moves") or ())
    if tokens.count("searchmoves") > 1 or ("searchmoves" in tokens and not roots):
        raise OnlineTimeError("searchmoves must be one explicit nonempty restriction")
    if len(roots) != len(set(roots)):
        raise OnlineTimeError("duplicate searchmoves")
    # Do not let the permissive legacy parser silently normalize this profile.
    if any(root not in tokens for root in roots):
        raise OnlineTimeError("clock-mode searchmoves must use canonical lowercase UCI")
    return limits, roots


@dataclass(frozen=True)
class TimePlan:
    generation: int
    position_id: str
    side_to_move: str
    request_class: str
    external_go_command: str
    anchor_go_command: str
    received_monotonic: float
    controller_cpu_started_ns: int
    available_clock_ms: int
    increment_ms: int
    moves_to_go: int | None
    soft_budget_ms: int
    hard_budget_ms: int
    prepare_budget_ms: int
    network_reserve_ms: int
    output_margin_ms: int
    envelope: ResourceEnvelope
    declared_envelope: ResourceEnvelope
    settings: OnlineTimeSettings
    policy: str = POLICY

    @property
    def soft_deadline(self) -> float:
        return self.received_monotonic + self.soft_budget_ms / 1000

    @property
    def hard_deadline(self) -> float:
        return self.received_monotonic + self.hard_budget_ms / 1000

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = 1
        result["clock_basis"] = "UCI clocks supplied by caller; any bridge adjustment is upstream"
        result["authority"] = {"timing": True, "outward_move": False}
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        result["plan_id"] = "time-" + hashlib.sha256(encoded.encode()).hexdigest()
        return result


def make_time_plan(*, command: str, position: PositionRequest, generation: int,
                   settings: OnlineTimeSettings, envelope: ResourceEnvelope,
                   received_monotonic: float, controller_cpu_started_ns: int) -> TimePlan:
    _integer(generation, "generation", 1)
    if isinstance(received_monotonic, bool) or not isinstance(received_monotonic, (int, float)) or not math.isfinite(received_monotonic):
        raise OnlineTimeError("receipt clock must be finite")
    _integer(controller_cpu_started_ns, "controller_cpu_started_ns", 0, 2**63 - 1)
    if position.variant != "standard":
        raise OnlineTimeError("ONLINE-1 supports standard chess only")
    side = position.side_to_move
    limits, roots = _limits(command)
    if set(limits) == {"movetime"}:
        available = limits["movetime"]
        increment, moves_to_go = 0, None
        request_class = "movetime_deadline_v1"
        # A fixed movetime is the entire controller budget, not a fresh budget
        # after preparation. Network allowance applies only to game clocks.
        reserve = 0
        desired = available
    elif {"wtime", "btime"} <= set(limits) <= {"wtime", "btime", "winc", "binc", "movestogo"}:
        available = limits[f"{side}time"]
        increment = limits.get(f"{side}inc", 0)
        moves_to_go = limits.get("movestogo")
        if moves_to_go is not None and moves_to_go < 1:
            raise OnlineTimeError("movestogo must be positive")
        request_class = "clock_v1"
        reserve = settings.network_reserve_ms
        horizon = min(moves_to_go, settings.moves_horizon) if moves_to_go else settings.moves_horizon
        desired = int(max(0, available - reserve) / horizon + settings.increment_fraction * increment)
        desired += settings.stop_grace_ms + settings.output_margin_ms
    else:
        raise OnlineTimeError("clock mode requires both clocks or a single movetime; no ponder/infinite/work-limit mixtures")
    hard = min(available - reserve, desired, settings.max_move_ms, int(envelope.wall_ms))
    if hard <= settings.stop_grace_ms + settings.output_margin_ms:
        # Do not fabricate a minimum think time exceeding a nearly spent clock.
        raise OnlineTimeError("insufficient clock for the declared stop/output margins")
    soft = hard - settings.stop_grace_ms - settings.output_margin_ms
    cpu = min(envelope.cpu_ms, hard * settings.cpu_parallelism)
    if envelope.wall_ms <= 0 or envelope.cpu_ms <= 0 or cpu <= 0 or envelope.gpu_ms != 0:
        raise OnlineTimeError("ONLINE-1 requires a positive CPU-only envelope")
    per_move = envelope.bounded_for_move(wall_ms=hard, cpu_parallelism=settings.cpu_parallelism)
    anchor_command = f"go movetime {soft}"
    if roots:
        anchor_command += " searchmoves " + " ".join(roots)
    return TimePlan(
        generation=generation, position_id=position.position_id, side_to_move=side,
        request_class=request_class, external_go_command=command, anchor_go_command=anchor_command,
        received_monotonic=received_monotonic, controller_cpu_started_ns=controller_cpu_started_ns,
        available_clock_ms=available, increment_ms=increment, moves_to_go=moves_to_go,
        soft_budget_ms=soft, hard_budget_ms=hard, prepare_budget_ms=min(settings.prepare_budget_ms, max(0, soft // 4)),
        network_reserve_ms=reserve, output_margin_ms=settings.output_margin_ms, envelope=per_move,
        declared_envelope=envelope, settings=settings,
    )


class ClockSearch:
    """One generation's mutable deadline state around an immutable TimePlan.

    Soft-stop IO cannot delay hard expiry. Late callbacks are also checked by
    the frontend and token-scoped process adapter before touching the engine.
    """
    def __init__(self, plan: TimePlan) -> None:
        self.plan = plan
        self.work_closed = threading.Event()
        self.finished = threading.Event()
        self.dispatched = threading.Event()
        self.measurement_superseded = threading.Event()
        self._lock = threading.Lock()
        self.emitted_ms: float | None = None
        self.emitted_line: str | None = None
        self.failure: str | None = None
        self._thread: threading.Thread | None = None

    def work_open(self) -> bool:
        return not self.work_closed.is_set() and time.monotonic() < self.plan.soft_deadline

    def finish(self, *, line: str | None = None, failure: str | None = None) -> None:
        with self._lock:
            if self.finished.is_set():
                return
            self.emitted_ms = (time.monotonic() - self.plan.received_monotonic) * 1000
            self.emitted_line = line
            self.failure = failure
            self.work_closed.set()
            self.finished.set()

    def outcome(self) -> dict[str, Any]:
        with self._lock:
            return {"emitted_ms": self.emitted_ms, "emitted_line": self.emitted_line,
                    "failure": self.failure,
                    "output_within_deadline": self.finished.is_set() and self.failure is None and self.emitted_line is not None
                    and self.emitted_ms is not None and self.emitted_ms <= self.plan.hard_budget_ms}

    def start(self, on_soft: Callable[[], None], on_hard: Callable[[], None]) -> None:
        if self._thread is not None:
            raise OnlineTimeError("deadline already started")
        def watch() -> None:
            if self.finished.wait(max(0, self.plan.soft_deadline - time.monotonic())):
                return
            self.work_closed.set()
            # A blocked stop write must never consume the hard-expiry thread.
            threading.Thread(target=on_soft, name=f"allfather-clock-stop-{self.plan.generation}", daemon=True).start()
            if not self.finished.wait(max(0, self.plan.hard_deadline - time.monotonic())):
                on_hard()
        self._thread = threading.Thread(target=watch, name=f"allfather-clock-{self.plan.generation}", daemon=True)
        self._thread.start()


def verify_time_manifest(manifest: dict[str, Any]) -> list[str]:
    """Reconstruct rather than trust an online manifest's timing declaration."""
    raw = manifest.get("time_plan")
    if raw is None:
        return []
    try:
        if not isinstance(raw, dict):
            raise OnlineTimeError("time_plan must be an object")
        position = manifest["position"]
        request = PositionRequest(base_fen=position["base_fen"], moves=tuple(position["moves"]),
                                  variant=position["variant"])
        if request.position_id != position["position_id"]:
            raise OnlineTimeError("clock position identity mismatch")
        expected = make_time_plan(
            command=manifest["external_request"]["command"], position=request,
            generation=manifest["generation"], settings=OnlineTimeSettings(**raw["settings"]),
            envelope=ResourceEnvelope(**raw["declared_envelope"]),
            received_monotonic=raw["received_monotonic"],
            controller_cpu_started_ns=raw["controller_cpu_started_ns"],
        )
        if expected.as_dict() != raw:
            raise OnlineTimeError("time_plan identity or reconstructed policy mismatch")
        anchors = [stage for stage in manifest["stages"] if stage.get("role") == "anchor"]
        if len(anchors) != 1 or anchors[0]["command"] != expected.anchor_go_command:
            raise OnlineTimeError("clock anchor dispatch does not match the bounded request")
        outcome = manifest.get("clock_outcome")
        if not isinstance(outcome, dict) or not isinstance(outcome.get("output_within_deadline"), bool):
            raise OnlineTimeError("clock outcome missing or malformed")
        elapsed = outcome.get("emitted_ms")
        if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or elapsed < 0):
            raise OnlineTimeError("clock emitted_ms must be nonnegative and finite or null")
        actual = (outcome.get("failure") is None and isinstance(outcome.get("emitted_line"), str)
                  and elapsed is not None and elapsed <= expected.hard_budget_ms)
        if actual != outcome["output_within_deadline"]:
            raise OnlineTimeError("clock outcome contradicts its deadline")
        if actual:
            words = outcome["emitted_line"].split()
            if len(words) < 2 or words[0] != "bestmove" or words[1] != anchors[0].get("bestmove"):
                raise OnlineTimeError("clock output does not match the recorded anchor bestmove")
        return []
    except (KeyError, TypeError, ValueError, OverflowError, BudgetError) as exc:
        return [f"online time integrity: {exc}"]
