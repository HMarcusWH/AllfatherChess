"""Engine-neutral telemetry v1 stream construction primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_COMMON_EVENT_KEYS = {
    "schema_version",
    "event_type",
    "search_id",
    "sequence",
    "observed_ms",
    "engine",
    "engine_instance",
    "position_id",
}


class TelemetryError(RuntimeError):
    """Raised when a telemetry stream violates its local lifecycle."""


@dataclass(frozen=True)
class SearchIdentity:
    engine: str
    engine_instance: str
    search_id: str
    position_id: str
    variant: str = "standard"

    @property
    def move_encoding(self) -> str:
        if self.variant == "standard":
            return "uci"
        if self.variant == "chess960":
            return "uci_chess960"
        raise TelemetryError(f"unsupported variant: {self.variant}")


class TelemetryStream:
    """Own sequence numbers and immutable identity for one telemetry search stream."""

    def __init__(self, identity: SearchIdentity):
        self.identity = identity
        self._started = False
        self._completed = False
        self._sequence = -1
        self._last_observed_ms = -1.0

    @property
    def started(self) -> bool:
        return self._started

    @property
    def completed(self) -> bool:
        return self._completed

    def _validate_observed_ms(self, observed_ms: int | float) -> float:
        if isinstance(observed_ms, bool) or not isinstance(observed_ms, (int, float)):
            raise TelemetryError("observed_ms must be numeric")
        try:
            value = float(observed_ms)
        except OverflowError as exc:
            raise TelemetryError("observed_ms must be finite") from exc
        if not math.isfinite(value):
            raise TelemetryError("observed_ms must be finite")
        if value < 0:
            raise TelemetryError("observed_ms must be non-negative")
        if self._started and value < self._last_observed_ms:
            raise TelemetryError("observed_ms must not decrease")
        return value

    def _common(self, event_type: str, observed_ms: int | float) -> dict[str, Any]:
        observed = self._validate_observed_ms(observed_ms)
        self._sequence += 1
        self._last_observed_ms = observed
        return {
            "schema_version": 1,
            "event_type": event_type,
            "search_id": self.identity.search_id,
            "sequence": self._sequence,
            "observed_ms": observed_ms,
            "engine": self.identity.engine,
            "engine_instance": self.identity.engine_instance,
            "position_id": self.identity.position_id,
        }

    def start(
        self,
        *,
        position: dict[str, Any],
        request: dict[str, Any],
        observed_ms: int | float = 0,
        controller: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._started:
            raise TelemetryError("search stream already started")
        move_encoding = self.identity.move_encoding
        event = self._common("search.started", observed_ms)
        event.update(
            {
                "variant": self.identity.variant,
                "move_encoding": move_encoding,
                "position": position,
                "request": request,
            }
        )
        if controller is not None:
            event["controller"] = controller
        self._started = True
        return event

    def emit(
        self,
        event_type: str,
        *,
        observed_ms: int | float,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._started:
            raise TelemetryError("search stream has not started")
        if self._completed:
            raise TelemetryError("cannot emit after search.complete")
        if event_type == "search.started":
            raise TelemetryError("search.started must be emitted by start()")
        if payload:
            collisions = sorted(_COMMON_EVENT_KEYS.intersection(payload))
            if collisions:
                raise TelemetryError(
                    f"payload cannot overwrite immutable stream metadata: {collisions}"
                )
        event = self._common(event_type, observed_ms)
        if payload:
            event.update(payload)
        if event_type == "search.complete":
            self._completed = True
        return event
