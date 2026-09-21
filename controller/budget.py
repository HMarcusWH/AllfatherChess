"""One declared resource envelope for the whole controller.

The product objective is equal-envelope superiority, so this ledger exists to
make the envelope *real*: every solver dispatch, every verification reservation,
and the controller's own overhead spend from the same budget `B`.

Two rules follow from the theory and are enforced here:

- **Concurrency cannot inflate the envelope.** Compute is reserved before it is
  spent, under one lock, so two workers cannot each observe "enough budget" and
  both proceed past the ceiling.
- **Controller overhead never disappears.** Metareasoning is charged with a
  monotonic clock to its own lane inside `B`, not treated as free.

Engine-native work counters are recorded separately and are *never* summed
across semantics: Stockfish alpha-beta nodes and LC0 visit-derived counts are
not the same quantity.
"""

from __future__ import annotations

import itertools
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


class BudgetError(RuntimeError):
    """Raised when budget accounting would become dishonest."""


class BudgetExceeded(BudgetError):
    """Raised when a reservation would exceed the declared envelope."""


#: Reserved lane name for the controller's own metareasoning cost.
CONTROLLER_LANE = "controller"
#: Reserved lane name for explicit verification / re-lock work.
VERIFY_LANE = "verify"


@dataclass(frozen=True)
class ResourceEnvelope:
    """The declared total budget `B` for one external search."""

    wall_ms: float
    cpu_ms: float
    gpu_ms: float = 0.0
    verification_reserve_fraction: float = 0.0
    controller_overhead_reserve_ms: float = 0.0

    def __post_init__(self) -> None:
        # `value < 0` alone accepts both infinity and NaN. An infinite budget
        # silently disables the deadline or the ceiling it describes while the
        # run still reports itself compliant, and NaN makes every comparison
        # false, so the accounting stops meaning anything at all.
        for name in (
            "wall_ms",
            "cpu_ms",
            "gpu_ms",
            "verification_reserve_fraction",
            "controller_overhead_reserve_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BudgetError(f"{name} must be a number")
            if not math.isfinite(float(value)):
                raise BudgetError(f"{name} must be finite, got {value!r}")
        for name in ("wall_ms", "cpu_ms", "gpu_ms"):
            if getattr(self, name) < 0:
                raise BudgetError(f"{name} must be a non-negative number")
        if not 0.0 <= self.verification_reserve_fraction < 1.0:
            raise BudgetError("verification_reserve_fraction must be in [0, 1)")
        if self.controller_overhead_reserve_ms < 0:
            raise BudgetError("controller_overhead_reserve_ms must be non-negative")
        if self.controller_overhead_reserve_ms > self.cpu_ms:
            raise BudgetError("controller overhead reserve cannot exceed the CPU envelope")

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "ResourceEnvelope":
        if not config:
            raise BudgetError("active mode requires an explicit budget configuration")
        for key in ("wall_ms", "cpu_ms", "gpu_ms"):
            if key in config and isinstance(config[key], str):
                raise BudgetError(f"budget.{key} must be a number, not a string")
        return cls(
            wall_ms=float(config.get("wall_ms", 0.0)),
            cpu_ms=float(config.get("cpu_ms", 0.0)),
            gpu_ms=float(config.get("gpu_ms", 0.0)),
            verification_reserve_fraction=float(config.get("verification_reserve_fraction", 0.0)),
            controller_overhead_reserve_ms=float(
                config.get("controller_overhead_reserve_ms", 0.0)
            ),
        )

    @property
    def verification_reserve_ms(self) -> float:
        return self.cpu_ms * self.verification_reserve_fraction

    def as_dict(self) -> dict[str, Any]:
        return {
            "wall_ms": self.wall_ms,
            "cpu_ms": self.cpu_ms,
            "gpu_ms": self.gpu_ms,
            "verification_reserve_fraction": self.verification_reserve_fraction,
            "verification_reserve_ms": self.verification_reserve_ms,
            "controller_overhead_reserve_ms": self.controller_overhead_reserve_ms,
        }


@dataclass(frozen=True)
class Reservation:
    """A claim on the envelope held while work is in flight."""

    reservation_id: int
    lane: str
    purpose: str
    cpu_ms: float
    gpu_ms: float


@dataclass
class LaneAccount:
    reserved_cpu_ms: float = 0.0
    reserved_gpu_ms: float = 0.0
    spent_cpu_ms: float = 0.0
    spent_gpu_ms: float = 0.0
    #: engine-native counters, keyed by semantics; never summed across keys.
    native_work: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reserved_cpu_ms": round(self.reserved_cpu_ms, 3),
            "reserved_gpu_ms": round(self.reserved_gpu_ms, 3),
            "spent_cpu_ms": round(self.spent_cpu_ms, 3),
            "spent_gpu_ms": round(self.spent_gpu_ms, 3),
            "native_work": {key: round(value, 3) for key, value in sorted(self.native_work.items())},
        }


class BudgetLedger:
    """Concurrency-safe accounting for one envelope.

    `reserve` is the only way compute enters the ledger. Because reservation and
    the ceiling check happen under the same lock, concurrent workers cannot
    collectively exceed `B`.
    """

    def __init__(
        self,
        envelope: ResourceEnvelope,
        *,
        clock: Callable[[], float] | None = None,
        started: float | None = None,
    ) -> None:
        self.envelope = envelope
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._lanes: dict[str, LaneAccount] = {}
        self._ids = itertools.count(1)
        self._open: dict[int, Reservation] = {}
        # `started` lets the ledger measure from the external `go` rather than
        # from its own construction. Legal-root qualification happens before the
        # router exists, so a self-started clock would hand a slow oracle a free
        # extra envelope.
        self._started = self._clock() if started is None else started
        self._denials: list[dict[str, Any]] = []

    # -- clock ---------------------------------------------------------------

    def elapsed_ms(self) -> float:
        return (self._clock() - self._started) * 1000.0

    def wall_remaining_ms(self) -> float:
        return max(0.0, self.envelope.wall_ms - self.elapsed_ms())

    def wall_exhausted(self) -> bool:
        return self.elapsed_ms() >= self.envelope.wall_ms

    # -- accounting ----------------------------------------------------------

    def _lane(self, lane: str) -> LaneAccount:
        if not isinstance(lane, str) or not lane:
            raise BudgetError("lane must be a non-empty string")
        return self._lanes.setdefault(lane, LaneAccount())

    def _committed(self) -> tuple[float, float]:
        cpu = sum(item.reserved_cpu_ms + item.spent_cpu_ms for item in self._lanes.values())
        gpu = sum(item.reserved_gpu_ms + item.spent_gpu_ms for item in self._lanes.values())
        return cpu, gpu

    def _ceiling(self, purpose: str) -> float:
        """Solver work may not eat the verification or overhead reserves."""
        if purpose in ("verify", "controller"):
            return self.envelope.cpu_ms
        return max(
            0.0,
            self.envelope.cpu_ms
            - self.envelope.verification_reserve_ms
            - self.envelope.controller_overhead_reserve_ms,
        )

    def available_cpu_ms(self, *, purpose: str = "solver") -> float:
        with self._lock:
            committed, _ = self._committed()
            return max(0.0, self._ceiling(purpose) - committed)

    def available_gpu_ms(self) -> float:
        with self._lock:
            _, committed = self._committed()
            return max(0.0, self.envelope.gpu_ms - committed)

    def can_afford(self, *, cpu_ms: float, gpu_ms: float = 0.0, purpose: str = "solver") -> bool:
        with self._lock:
            return (
                cpu_ms <= self.available_cpu_ms(purpose=purpose)
                and gpu_ms <= self.available_gpu_ms()
            )

    def reserve(
        self,
        lane: str,
        *,
        cpu_ms: float,
        gpu_ms: float = 0.0,
        purpose: str = "solver",
    ) -> Reservation:
        """Claim envelope capacity before the work starts."""
        if cpu_ms < 0 or gpu_ms < 0:
            raise BudgetError("reservations must be non-negative")
        with self._lock:
            committed_cpu, committed_gpu = self._committed()
            ceiling = self._ceiling(purpose)
            if committed_cpu + cpu_ms > ceiling:
                self._denials.append(
                    {
                        "lane": lane,
                        "purpose": purpose,
                        "requested_cpu_ms": cpu_ms,
                        "available_cpu_ms": max(0.0, ceiling - committed_cpu),
                        "reason": "cpu envelope",
                    }
                )
                raise BudgetExceeded(
                    f"cpu reservation of {cpu_ms}ms for {lane!r} exceeds the envelope: "
                    f"committed={committed_cpu}, ceiling={ceiling}"
                )
            if committed_gpu + gpu_ms > self.envelope.gpu_ms:
                self._denials.append(
                    {
                        "lane": lane,
                        "purpose": purpose,
                        "requested_gpu_ms": gpu_ms,
                        "available_gpu_ms": max(0.0, self.envelope.gpu_ms - committed_gpu),
                        "reason": "gpu envelope",
                    }
                )
                raise BudgetExceeded(
                    f"gpu reservation of {gpu_ms}ms for {lane!r} exceeds the envelope"
                )
            account = self._lane(lane)
            account.reserved_cpu_ms += cpu_ms
            account.reserved_gpu_ms += gpu_ms
            reservation = Reservation(
                reservation_id=next(self._ids),
                lane=lane,
                purpose=purpose,
                cpu_ms=cpu_ms,
                gpu_ms=gpu_ms,
            )
            self._open[reservation.reservation_id] = reservation
            return reservation

    def settle(
        self,
        reservation: Reservation,
        *,
        actual_cpu_ms: float | None = None,
        actual_gpu_ms: float | None = None,
    ) -> None:
        """Convert a reservation into spend.

        Actual spend above the reservation is still charged: the envelope
        records what was consumed, not what was hoped for.
        """
        with self._lock:
            if self._open.pop(reservation.reservation_id, None) is None:
                raise BudgetError(f"reservation {reservation.reservation_id} is not open")
            account = self._lane(reservation.lane)
            account.reserved_cpu_ms = max(0.0, account.reserved_cpu_ms - reservation.cpu_ms)
            account.reserved_gpu_ms = max(0.0, account.reserved_gpu_ms - reservation.gpu_ms)
            account.spent_cpu_ms += reservation.cpu_ms if actual_cpu_ms is None else actual_cpu_ms
            account.spent_gpu_ms += reservation.gpu_ms if actual_gpu_ms is None else actual_gpu_ms

    def release(self, reservation: Reservation) -> None:
        """Return unspent capacity, for example after a worker is stopped early."""
        with self._lock:
            if self._open.pop(reservation.reservation_id, None) is None:
                return
            account = self._lane(reservation.lane)
            account.reserved_cpu_ms = max(0.0, account.reserved_cpu_ms - reservation.cpu_ms)
            account.reserved_gpu_ms = max(0.0, account.reserved_gpu_ms - reservation.gpu_ms)

    def charge_elapsed(self, lane: str, *, cpu_ms: float, note: str = "") -> None:
        """Record work that happened before the ledger could reserve it.

        Controller startup work — notably the legal-root oracle — runs before
        any reservation exists. Charging it after the fact keeps it inside `B`
        instead of leaving it outside the accounting entirely.
        """
        if cpu_ms < 0 or not math.isfinite(cpu_ms):
            raise BudgetError("charged elapsed time must be finite and non-negative")
        with self._lock:
            account = self._lane(lane)
            account.spent_cpu_ms += cpu_ms
            if note:
                account.native_work[note] = account.native_work.get(note, 0.0) + cpu_ms

    def record_native_work(self, lane: str, *, value: float, semantics: str) -> None:
        """Record an engine-native counter under its own semantics tag."""
        if not isinstance(semantics, str) or not semantics:
            raise BudgetError("native work requires a semantics tag")
        with self._lock:
            account = self._lane(lane)
            account.native_work[semantics] = max(account.native_work.get(semantics, 0.0), float(value))

    def native_work_by_semantics(self) -> dict[str, float]:
        """Per-semantics totals.

        There is deliberately no scalar total: summing alpha-beta nodes with
        LC0 visit-derived counts would assert an equivalence nothing has
        established.
        """
        with self._lock:
            totals: dict[str, float] = {}
            for account in self._lanes.values():
                for semantics, value in account.native_work.items():
                    totals[semantics] = totals.get(semantics, 0.0) + value
            return totals

    @contextmanager
    def controller_overhead(self, label: str = "checkpoint") -> Iterator[None]:
        """Charge the controller's own metareasoning time to the envelope."""
        started = self._clock()
        try:
            yield
        finally:
            elapsed_ms = (self._clock() - started) * 1000.0
            with self._lock:
                account = self._lane(CONTROLLER_LANE)
                account.spent_cpu_ms += elapsed_ms
                account.native_work[f"controller.{label}_ms"] = (
                    account.native_work.get(f"controller.{label}_ms", 0.0) + elapsed_ms
                )

    # -- reporting -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            committed_cpu, committed_gpu = self._committed()
            return {
                "envelope": self.envelope.as_dict(),
                "elapsed_ms": round(self.elapsed_ms(), 3),
                "wall_remaining_ms": round(self.wall_remaining_ms(), 3),
                "committed_cpu_ms": round(committed_cpu, 3),
                "committed_gpu_ms": round(committed_gpu, 3),
                "available_solver_cpu_ms": round(self.available_cpu_ms(), 3),
                "open_reservations": len(self._open),
                "lanes": {name: account.as_dict() for name, account in sorted(self._lanes.items())},
                "native_work_by_semantics": {
                    key: round(value, 3)
                    for key, value in sorted(self.native_work_by_semantics().items())
                },
                "denials": list(self._denials),
                "within_envelope": committed_cpu <= self.envelope.cpu_ms
                and committed_gpu <= self.envelope.gpu_ms,
            }

    def within_envelope(self) -> bool:
        with self._lock:
            committed_cpu, committed_gpu = self._committed()
            return (
                committed_cpu <= self.envelope.cpu_ms and committed_gpu <= self.envelope.gpu_ms
            )
