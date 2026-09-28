"""Immutable game-environment contract for M14-J.

This module records what the controller is allowed to know about the surrounding
match before resource selection.  It performs no host probing and makes no
allocation decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _mapping,
    _nonnegative_int,
    _positive_int,
    _safe_id,
)


class EnvironmentSource(str, Enum):
    BRIDGE_PREDECLARED = "bridge_predeclared"
    UCI_OBSERVED = "uci_observed"
    UNKNOWN = "unknown"


def _source(value: Any) -> EnvironmentSource:
    if isinstance(value, EnvironmentSource):
        return value
    try:
        return EnvironmentSource(value)
    except (TypeError, ValueError) as exc:
        raise OrchestrationContractError(
            f"environment source must be one of "
            f"{[item.value for item in EnvironmentSource]}, got {value!r}"
        ) from exc


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, label)


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, label)


@dataclass(frozen=True)
class GameEnvironment:
    source: EnvironmentSource
    base_ms: int | None
    increment_ms: int | None
    moves_to_go: int | None
    white_time_ms: int | None
    black_time_ms: int | None
    concurrency: int
    network_policy: str
    network_reserve_ms: int
    host_profile_id: str | None = None
    candidate_composition_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source = _source(self.source)
        object.__setattr__(self, "source", source)

        base_ms = _optional_positive_int(self.base_ms, "base_ms")
        increment_ms = _optional_nonnegative_int(self.increment_ms, "increment_ms")
        moves_to_go = _optional_positive_int(self.moves_to_go, "moves_to_go")
        white_time_ms = _optional_nonnegative_int(self.white_time_ms, "white_time_ms")
        black_time_ms = _optional_nonnegative_int(self.black_time_ms, "black_time_ms")
        object.__setattr__(self, "base_ms", base_ms)
        object.__setattr__(self, "increment_ms", increment_ms)
        object.__setattr__(self, "moves_to_go", moves_to_go)
        object.__setattr__(self, "white_time_ms", white_time_ms)
        object.__setattr__(self, "black_time_ms", black_time_ms)

        _positive_int(self.concurrency, "concurrency")
        _safe_id(self.network_policy, "network_policy")
        _nonnegative_int(self.network_reserve_ms, "network_reserve_ms")

        if self.host_profile_id is not None:
            _safe_id(self.host_profile_id, "host_profile_id")

        candidates = tuple(self.candidate_composition_ids)
        for index, value in enumerate(candidates):
            _safe_id(value, f"candidate_composition_ids[{index}]")
        if len(candidates) != len(set(candidates)):
            raise OrchestrationContractError(
                "candidate_composition_ids must be unique"
            )
        object.__setattr__(self, "candidate_composition_ids", tuple(sorted(candidates)))

        if source is EnvironmentSource.BRIDGE_PREDECLARED:
            if base_ms is None or increment_ms is None:
                raise OrchestrationContractError(
                    "bridge_predeclared environment requires base_ms and increment_ms"
                )
        elif source is EnvironmentSource.UCI_OBSERVED:
            if white_time_ms is None or black_time_ms is None or increment_ms is None:
                raise OrchestrationContractError(
                    "uci_observed environment requires both remaining clocks and increment_ms"
                )
        else:
            timing = (base_ms, increment_ms, moves_to_go, white_time_ms, black_time_ms)
            if any(value is not None for value in timing):
                raise OrchestrationContractError(
                    "unknown environment may not assert timing facts"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "source": self.source.value,
            "base_ms": self.base_ms,
            "increment_ms": self.increment_ms,
            "moves_to_go": self.moves_to_go,
            "white_time_ms": self.white_time_ms,
            "black_time_ms": self.black_time_ms,
            "concurrency": self.concurrency,
            "network_policy": self.network_policy,
            "network_reserve_ms": self.network_reserve_ms,
            "host_profile_id": self.host_profile_id,
            "candidate_composition_ids": list(self.candidate_composition_ids),
            "authority": {
                "resource_context": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GameEnvironment":
        raw = _mapping(raw, "game environment")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                f"unsupported game environment schema_version: "
                f"{raw.get('schema_version')!r}"
            )
        authority = raw.get("authority")
        if authority is not None and authority != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError("game environment authority marker is invalid")
        candidates = raw.get("candidate_composition_ids", [])
        if not isinstance(candidates, list):
            raise OrchestrationContractError(
                "candidate_composition_ids must be an array"
            )
        return cls(
            source=raw.get("source"),
            base_ms=raw.get("base_ms"),
            increment_ms=raw.get("increment_ms"),
            moves_to_go=raw.get("moves_to_go"),
            white_time_ms=raw.get("white_time_ms"),
            black_time_ms=raw.get("black_time_ms"),
            concurrency=raw.get("concurrency"),
            network_policy=raw.get("network_policy"),
            network_reserve_ms=raw.get("network_reserve_ms"),
            host_profile_id=raw.get("host_profile_id"),
            candidate_composition_ids=tuple(candidates),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    @property
    def environment_id(self) -> str:
        return f"game-env/{self.digest[:16]}"
