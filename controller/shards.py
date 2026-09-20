"""Root-level controller-owned search-space ledger.

PR #10 deliberately stops at root shards: one legal root move is one shard.
No recursive split/transfer/VERIFY overlap or routing policy is implemented here.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ShardLedgerError(RuntimeError):
    """Raised when a shard ownership/state invariant would be violated."""


class ShardState(str, Enum):
    UNASSIGNED = "unassigned"
    LEASED = "leased"
    ACTIVE = "active"
    SEALED = "sealed"


@dataclass
class _RootShard:
    id: str
    generation: int
    ordinal: int
    prefix: tuple[str, ...]
    owner: str | None = None
    state: ShardState = ShardState.UNASSIGNED


def _validate_move(move: object, *, label: str) -> str:
    if not isinstance(move, str) or not _MOVE_RE.fullmatch(move):
        raise ShardLedgerError(f"{label} must be canonical lowercase UCI: {move!r}")
    return move


class RootShardLedger:
    """Thread-safe one-generation root ownership ledger.

    The candidate universe is immutable. A partition may be leased exactly once
    for the generation. Owner activation/sealing is atomic at the owner level.
    """

    schema_version = 1

    def __init__(
        self,
        candidate_roots: Sequence[str],
        *,
        owners: Sequence[str] = ("stockfish", "reckless", "lc0"),
        generation: int = 1,
    ) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ShardLedgerError("generation must be a positive integer")

        owner_values = tuple(owners)
        if not owner_values:
            raise ShardLedgerError("owners must be non-empty")
        if any(not isinstance(owner, str) or not owner for owner in owner_values):
            raise ShardLedgerError("owners must contain only non-empty strings")
        if len(set(owner_values)) != len(owner_values):
            raise ShardLedgerError("owners must be unique")

        roots: list[str] = []
        seen: set[str] = set()
        for index, raw in enumerate(candidate_roots):
            move = _validate_move(raw, label=f"candidate_roots[{index}]")
            if move in seen:
                raise ShardLedgerError(f"duplicate candidate root: {move}")
            seen.add(move)
            roots.append(move)

        self._lock = threading.RLock()
        self._owners = owner_values
        self._generation = generation
        self._candidate_roots = tuple(roots)
        self._revision = 0
        self._partition_assigned = False
        self._shards = [
            _RootShard(
                id=f"g{generation:06d}:r{ordinal:03d}:{move}",
                generation=generation,
                ordinal=ordinal,
                prefix=(move,),
            )
            for ordinal, move in enumerate(self._candidate_roots)
        ]
        self._by_move = {shard.prefix[0]: shard for shard in self._shards}

    @property
    def owners(self) -> tuple[str, ...]:
        return self._owners

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def candidate_roots(self) -> tuple[str, ...]:
        return self._candidate_roots

    @property
    def partition_assigned(self) -> bool:
        with self._lock:
            return self._partition_assigned

    def _require_owner(self, owner: str) -> None:
        if owner not in self._owners:
            raise ShardLedgerError(f"unknown shard owner: {owner!r}")

    def assign_partition(self, partition: Mapping[str, Sequence[str]]) -> None:
        """Atomically lease the entire candidate universe exactly once."""
        with self._lock:
            if self._partition_assigned:
                raise ShardLedgerError("root partition already assigned for this generation")

            provided = set(partition)
            expected = set(self._owners)
            if provided != expected:
                missing = sorted(expected - provided)
                extra = sorted(provided - expected)
                raise ShardLedgerError(
                    f"partition owner keys must exactly match authorized owners; "
                    f"missing={missing}, extra={extra}"
                )

            validated: dict[str, tuple[str, ...]] = {}
            seen_global: dict[str, str] = {}
            for owner in self._owners:
                raw_moves = partition[owner]
                if isinstance(raw_moves, (str, bytes)) or not isinstance(raw_moves, Sequence):
                    raise ShardLedgerError(f"partition[{owner!r}] must be a move sequence")
                owner_moves: list[str] = []
                seen_owner: set[str] = set()
                for index, raw_move in enumerate(raw_moves):
                    move = _validate_move(raw_move, label=f"partition[{owner!r}][{index}]")
                    if move in seen_owner:
                        raise ShardLedgerError(
                            f"duplicate root within owner {owner!r}: {move}"
                        )
                    seen_owner.add(move)
                    if move not in self._by_move:
                        raise ShardLedgerError(
                            f"owner {owner!r} references unknown candidate root: {move}"
                        )
                    previous = seen_global.get(move)
                    if previous is not None:
                        raise ShardLedgerError(
                            f"root {move} assigned to multiple owners: "
                            f"{previous!r} and {owner!r}"
                        )
                    seen_global[move] = owner
                    owner_moves.append(move)
                validated[owner] = tuple(owner_moves)

            missing_roots = [
                move for move in self._candidate_roots if move not in seen_global
            ]
            if missing_roots:
                raise ShardLedgerError(
                    f"partition does not cover candidate universe: {missing_roots}"
                )
            if len(seen_global) != len(self._candidate_roots):
                raise ShardLedgerError("partition coverage cardinality mismatch")

            # Commit only after the whole partition has validated.
            for owner in self._owners:
                for move in validated[owner]:
                    shard = self._by_move[move]
                    shard.owner = owner
                    shard.state = ShardState.LEASED

            self._partition_assigned = True
            self._revision += 1

    def activate_owner(self, owner: str) -> None:
        """Atomically activate every leased shard owned by one backend."""
        with self._lock:
            self._require_owner(owner)
            owned = [shard for shard in self._shards if shard.owner == owner]
            if not owned:
                raise ShardLedgerError(f"owner {owner!r} has no leased roots to activate")
            invalid = [shard.id for shard in owned if shard.state != ShardState.LEASED]
            if invalid:
                raise ShardLedgerError(
                    f"owner {owner!r} activation requires all shards LEASED; invalid={invalid}"
                )
            for shard in owned:
                shard.state = ShardState.ACTIVE
            self._revision += 1

    def active_roots(self, owner: str) -> tuple[str, ...]:
        """Return the non-empty active dispatch set in candidate-universe order."""
        with self._lock:
            self._require_owner(owner)
            owned = [shard for shard in self._shards if shard.owner == owner]
            if not owned:
                raise ShardLedgerError(f"owner {owner!r} has no roots to dispatch")
            invalid = [shard.id for shard in owned if shard.state != ShardState.ACTIVE]
            if invalid:
                raise ShardLedgerError(
                    f"owner {owner!r} dispatch requires all owned shards ACTIVE; invalid={invalid}"
                )
            return tuple(shard.prefix[0] for shard in owned)

    def seal_owner(self, owner: str) -> None:
        """Atomically seal every active shard owned by one backend."""
        with self._lock:
            self._require_owner(owner)
            owned = [shard for shard in self._shards if shard.owner == owner]
            if not owned:
                raise ShardLedgerError(f"owner {owner!r} has no active roots to seal")
            invalid = [shard.id for shard in owned if shard.state != ShardState.ACTIVE]
            if invalid:
                raise ShardLedgerError(
                    f"owner {owner!r} seal requires all shards ACTIVE; invalid={invalid}"
                )
            for shard in owned:
                shard.state = ShardState.SEALED
            self._revision += 1

    def snapshot(self) -> dict[str, object]:
        """Return a stable JSON-serializable copy of ledger state."""
        with self._lock:
            return {
                "schema_version": self.schema_version,
                "generation": self._generation,
                "revision": self._revision,
                "partition_assigned": self._partition_assigned,
                "owners": list(self._owners),
                "candidate_roots": list(self._candidate_roots),
                "shards": [
                    {
                        "id": shard.id,
                        "ordinal": shard.ordinal,
                        "prefix": list(shard.prefix),
                        "owner": shard.owner,
                        "state": shard.state.value,
                    }
                    for shard in self._shards
                ],
            }
