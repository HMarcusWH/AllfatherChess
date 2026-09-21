"""Recursive controller-owned prefix-shard ledger.

PrefixShardLedger v2 is intentionally separate from the live RootShardLedger v1.
It qualifies recursive move-prefix ownership, split, transfer and replayable
snapshots without changing the current shadow/runtime execution path.

A shard prefix p denotes the chess search region containing continuations whose
move sequence begins with p.  Live frontier shards are always prefix-free: a
parent and one of its descendants can never both be dispatchable regions.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class PrefixShardLedgerError(RuntimeError):
    """Raised when a recursive ownership/state invariant would be violated."""


class PrefixShardState(str, Enum):
    UNASSIGNED = "unassigned"
    LEASED = "leased"
    ACTIVE = "active"
    SEALED = "sealed"
    RETIRED = "retired"


@dataclass
class _PrefixShard:
    id: str
    generation: int
    prefix: tuple[str, ...]
    depth: int
    root_ordinal: int
    sibling_ordinal: int | None
    parent_id: str | None
    child_ids: list[str] = field(default_factory=list)
    owner: str | None = None
    state: PrefixShardState = PrefixShardState.UNASSIGNED


def _validate_move(move: object, *, label: str) -> str:
    if not isinstance(move, str) or not _MOVE_RE.fullmatch(move):
        raise PrefixShardLedgerError(
            f"{label} must be canonical lowercase UCI: {move!r}"
        )
    return move


def _validate_owner_values(owners: Sequence[str]) -> tuple[str, ...]:
    values = tuple(owners)
    if not values:
        raise PrefixShardLedgerError("owners must be non-empty")
    if any(not isinstance(owner, str) or not owner for owner in values):
        raise PrefixShardLedgerError("owners must contain only non-empty strings")
    if len(set(values)) != len(values):
        raise PrefixShardLedgerError("owners must be unique")
    return values


def _is_strict_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) < len(right) and right[: len(left)] == left


class PrefixShardLedger:
    """Thread-safe recursive ownership ledger for one synchronized position.

    Root partition assignment is exact and one-shot.  Recursive mutations are
    per-shard.  A sealed leaf may be split atomically into leased children that
    inherit its owner; leased frontier shards may then be transferred atomically
    between authorized owners.

    The ledger is structural only.  It never generates legal chess moves and it
    carries no routing, residual, score, budget or RELOCK meaning.
    """

    schema_version = 2

    def __init__(
        self,
        candidate_roots: Sequence[str],
        *,
        owners: Sequence[str] = ("stockfish", "reckless", "lc0"),
        generation: int = 1,
    ) -> None:
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise PrefixShardLedgerError("generation must be a positive integer")
        owner_values = _validate_owner_values(owners)

        roots: list[str] = []
        seen: set[str] = set()
        for index, raw in enumerate(candidate_roots):
            move = _validate_move(raw, label=f"candidate_roots[{index}]")
            if move in seen:
                raise PrefixShardLedgerError(f"duplicate candidate root: {move}")
            seen.add(move)
            roots.append(move)

        self._lock = threading.RLock()
        self._generation = generation
        self._owners = owner_values
        self._candidate_roots = tuple(roots)
        self._partition_assigned = False
        self._revision = 0
        self._by_id: dict[str, _PrefixShard] = {}
        self._by_prefix: dict[tuple[str, ...], _PrefixShard] = {}
        self._root_ids: list[str] = []

        for root_ordinal, move in enumerate(self._candidate_roots):
            shard = _PrefixShard(
                id=self._id_for((move,)),
                generation=generation,
                prefix=(move,),
                depth=1,
                root_ordinal=root_ordinal,
                sibling_ordinal=None,
                parent_id=None,
            )
            self._by_id[shard.id] = shard
            self._by_prefix[shard.prefix] = shard
            self._root_ids.append(shard.id)

        self._validate_invariants_locked()

    @property
    def owners(self) -> tuple[str, ...]:
        return self._owners

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def candidate_roots(self) -> tuple[str, ...]:
        return self._candidate_roots

    @property
    def partition_assigned(self) -> bool:
        with self._lock:
            return self._partition_assigned

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def _id_for(self, prefix: tuple[str, ...]) -> str:
        return (
            f"g{self._generation:06d}:d{len(prefix):03d}:"
            + ".".join(prefix)
        )

    def _require_owner(self, owner: str) -> None:
        if owner not in self._owners:
            raise PrefixShardLedgerError(f"unknown shard owner: {owner!r}")

    def _require_shard(self, shard_id: str) -> _PrefixShard:
        try:
            return self._by_id[shard_id]
        except KeyError as exc:
            raise PrefixShardLedgerError(f"unknown shard id: {shard_id!r}") from exc

    def _snapshot_shard(self, shard: _PrefixShard) -> dict[str, object]:
        return {
            "id": shard.id,
            "generation": shard.generation,
            "prefix": list(shard.prefix),
            "depth": shard.depth,
            "root_ordinal": shard.root_ordinal,
            "sibling_ordinal": shard.sibling_ordinal,
            "parent_id": shard.parent_id,
            "child_ids": list(shard.child_ids),
            "owner": shard.owner,
            "state": shard.state.value,
        }

    def _ordered_ids_locked(self) -> list[str]:
        ordered: list[str] = []

        def visit(shard_id: str) -> None:
            ordered.append(shard_id)
            shard = self._by_id[shard_id]
            for child_id in shard.child_ids:
                visit(child_id)

        for root_id in self._root_ids:
            visit(root_id)
        return ordered

    def _frontier_shards_locked(self) -> list[_PrefixShard]:
        return [
            self._by_id[shard_id]
            for shard_id in self._ordered_ids_locked()
            if not self._by_id[shard_id].child_ids
            and self._by_id[shard_id].state != PrefixShardState.RETIRED
        ]

    def assign_root_partition(
        self, partition: Mapping[str, Sequence[str]]
    ) -> None:
        """Atomically lease the immutable root universe exactly once."""

        with self._lock:
            if self._partition_assigned:
                raise PrefixShardLedgerError(
                    "root partition already assigned for this generation"
                )
            provided = set(partition)
            expected = set(self._owners)
            if provided != expected:
                raise PrefixShardLedgerError(
                    "partition owner keys must exactly match authorized owners; "
                    f"missing={sorted(expected - provided)}, "
                    f"extra={sorted(provided - expected)}"
                )

            validated: dict[str, tuple[str, ...]] = {}
            seen_global: dict[str, str] = {}
            for owner in self._owners:
                raw_moves = partition[owner]
                if isinstance(raw_moves, (str, bytes)) or not isinstance(
                    raw_moves, Sequence
                ):
                    raise PrefixShardLedgerError(
                        f"partition[{owner!r}] must be a move sequence"
                    )
                local: list[str] = []
                seen_owner: set[str] = set()
                for index, raw_move in enumerate(raw_moves):
                    move = _validate_move(
                        raw_move, label=f"partition[{owner!r}][{index}]"
                    )
                    if move in seen_owner:
                        raise PrefixShardLedgerError(
                            f"duplicate root within owner {owner!r}: {move}"
                        )
                    seen_owner.add(move)
                    if (move,) not in self._by_prefix:
                        raise PrefixShardLedgerError(
                            f"owner {owner!r} references unknown candidate root: {move}"
                        )
                    previous = seen_global.get(move)
                    if previous is not None:
                        raise PrefixShardLedgerError(
                            f"root {move} assigned to multiple owners: "
                            f"{previous!r} and {owner!r}"
                        )
                    seen_global[move] = owner
                    local.append(move)
                validated[owner] = tuple(local)

            missing = [
                move for move in self._candidate_roots if move not in seen_global
            ]
            if missing:
                raise PrefixShardLedgerError(
                    f"partition does not cover candidate universe: {missing}"
                )
            if len(seen_global) != len(self._candidate_roots):
                raise PrefixShardLedgerError("partition coverage cardinality mismatch")

            for owner in self._owners:
                for move in validated[owner]:
                    shard = self._by_prefix[(move,)]
                    shard.owner = owner
                    shard.state = PrefixShardState.LEASED
            self._partition_assigned = True
            self._revision += 1
            self._validate_invariants_locked()

    def activate_shard(self, shard_id: str, *, owner: str) -> None:
        with self._lock:
            self._require_owner(owner)
            shard = self._require_shard(shard_id)
            if shard.child_ids:
                raise PrefixShardLedgerError(
                    f"cannot activate non-frontier shard {shard_id!r}"
                )
            if shard.owner != owner:
                raise PrefixShardLedgerError(
                    f"shard {shard_id!r} belongs to {shard.owner!r}, not {owner!r}"
                )
            if shard.state != PrefixShardState.LEASED:
                raise PrefixShardLedgerError(
                    f"activation requires LEASED shard, got {shard.state.value}"
                )
            shard.state = PrefixShardState.ACTIVE
            self._revision += 1
            self._validate_invariants_locked()

    def seal_shard(self, shard_id: str, *, owner: str) -> None:
        with self._lock:
            self._require_owner(owner)
            shard = self._require_shard(shard_id)
            if shard.child_ids:
                raise PrefixShardLedgerError(
                    f"cannot seal non-frontier shard {shard_id!r}"
                )
            if shard.owner != owner:
                raise PrefixShardLedgerError(
                    f"shard {shard_id!r} belongs to {shard.owner!r}, not {owner!r}"
                )
            if shard.state != PrefixShardState.ACTIVE:
                raise PrefixShardLedgerError(
                    f"seal requires ACTIVE shard, got {shard.state.value}"
                )
            shard.state = PrefixShardState.SEALED
            self._revision += 1
            self._validate_invariants_locked()

    def split_shard(
        self,
        shard_id: str,
        child_moves: Sequence[str],
        *,
        owner: str,
    ) -> tuple[str, ...]:
        """Replace one sealed leaf with the complete declared child frontier.

        Chess legality is intentionally external.  This method validates only
        structural/canonical input and commits all children or none.
        """

        with self._lock:
            self._require_owner(owner)
            parent = self._require_shard(shard_id)
            if parent.owner != owner:
                raise PrefixShardLedgerError(
                    f"shard {shard_id!r} belongs to {parent.owner!r}, not {owner!r}"
                )
            if parent.state != PrefixShardState.SEALED:
                raise PrefixShardLedgerError(
                    f"split requires SEALED parent, got {parent.state.value}"
                )
            if parent.child_ids:
                raise PrefixShardLedgerError("cannot split a shard more than once")
            if isinstance(child_moves, (str, bytes)) or not isinstance(
                child_moves, Sequence
            ):
                raise PrefixShardLedgerError("child_moves must be a move sequence")
            if not child_moves:
                raise PrefixShardLedgerError(
                    "split requires a non-empty exact legal child set"
                )

            validated: list[str] = []
            seen: set[str] = set()
            proposed: list[tuple[str, ...]] = []
            for index, raw_move in enumerate(child_moves):
                move = _validate_move(
                    raw_move, label=f"child_moves[{index}]"
                )
                if move in seen:
                    raise PrefixShardLedgerError(
                        f"duplicate split child move: {move}"
                    )
                seen.add(move)
                prefix = parent.prefix + (move,)
                if prefix in self._by_prefix:
                    raise PrefixShardLedgerError(
                        f"split would recreate existing prefix: {prefix}"
                    )
                validated.append(move)
                proposed.append(prefix)

            # A full proposed frontier must remain prefix-free against every
            # current frontier shard other than the parent being replaced.
            current_frontier = [
                shard
                for shard in self._frontier_shards_locked()
                if shard.id != parent.id
            ]
            for prefix in proposed:
                for other in current_frontier:
                    if _is_strict_prefix(prefix, other.prefix) or _is_strict_prefix(
                        other.prefix, prefix
                    ):
                        raise PrefixShardLedgerError(
                            f"split would violate prefix-free frontier: "
                            f"{prefix} vs {other.prefix}"
                        )

            child_ids: list[str] = []
            children: list[_PrefixShard] = []
            for sibling_ordinal, prefix in enumerate(proposed):
                child = _PrefixShard(
                    id=self._id_for(prefix),
                    generation=self._generation,
                    prefix=prefix,
                    depth=len(prefix),
                    root_ordinal=parent.root_ordinal,
                    sibling_ordinal=sibling_ordinal,
                    parent_id=parent.id,
                    owner=owner,
                    state=PrefixShardState.LEASED,
                )
                child_ids.append(child.id)
                children.append(child)

            # Commit only after the entire mutation validates.
            parent.state = PrefixShardState.RETIRED
            parent.child_ids = list(child_ids)
            for child in children:
                self._by_id[child.id] = child
                self._by_prefix[child.prefix] = child
            self._revision += 1
            self._validate_invariants_locked()
            return tuple(child_ids)

    def transfer_shards(
        self,
        shard_ids: Sequence[str],
        *,
        from_owner: str,
        to_owner: str,
    ) -> None:
        """Atomically transfer leased frontier shards between owners."""

        with self._lock:
            self._require_owner(from_owner)
            self._require_owner(to_owner)
            if from_owner == to_owner:
                raise PrefixShardLedgerError("transfer requires distinct owners")
            if isinstance(shard_ids, (str, bytes)) or not isinstance(
                shard_ids, Sequence
            ):
                raise PrefixShardLedgerError("shard_ids must be a sequence")
            if not shard_ids:
                raise PrefixShardLedgerError("transfer requires at least one shard id")
            if len(set(shard_ids)) != len(shard_ids):
                raise PrefixShardLedgerError("transfer shard_ids must be unique")

            shards: list[_PrefixShard] = []
            for shard_id in shard_ids:
                shard = self._require_shard(shard_id)
                if shard.child_ids:
                    raise PrefixShardLedgerError(
                        f"transfer requires frontier leaf, got parent {shard_id!r}"
                    )
                if shard.state != PrefixShardState.LEASED:
                    raise PrefixShardLedgerError(
                        f"transfer requires LEASED shard {shard_id!r}, "
                        f"got {shard.state.value}"
                    )
                if shard.owner != from_owner:
                    raise PrefixShardLedgerError(
                        f"shard {shard_id!r} belongs to {shard.owner!r}, "
                        f"not {from_owner!r}"
                    )
                shards.append(shard)

            for shard in shards:
                shard.owner = to_owner
            self._revision += 1
            self._validate_invariants_locked()

    def get(self, shard_id: str) -> dict[str, object]:
        with self._lock:
            return self._snapshot_shard(self._require_shard(shard_id))

    def parent(self, shard_id: str) -> dict[str, object] | None:
        with self._lock:
            shard = self._require_shard(shard_id)
            if shard.parent_id is None:
                return None
            return self._snapshot_shard(self._by_id[shard.parent_id])

    def children(self, shard_id: str) -> tuple[dict[str, object], ...]:
        with self._lock:
            shard = self._require_shard(shard_id)
            return tuple(
                self._snapshot_shard(self._by_id[child_id])
                for child_id in shard.child_ids
            )

    def frontier(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(
                self._snapshot_shard(shard)
                for shard in self._frontier_shards_locked()
            )

    def frontier_for_owner(self, owner: str) -> tuple[dict[str, object], ...]:
        with self._lock:
            self._require_owner(owner)
            return tuple(
                self._snapshot_shard(shard)
                for shard in self._frontier_shards_locked()
                if shard.owner == owner
            )

    def validate_invariants(self) -> None:
        with self._lock:
            self._validate_invariants_locked()

    def _validate_invariants_locked(self) -> None:
        if len(self._root_ids) != len(self._candidate_roots):
            raise PrefixShardLedgerError("root id cardinality mismatch")
        if len(self._by_id) != len(self._by_prefix):
            raise PrefixShardLedgerError("id/prefix index cardinality mismatch")

        for root_ordinal, root_id in enumerate(self._root_ids):
            root = self._by_id.get(root_id)
            if root is None:
                raise PrefixShardLedgerError(f"missing root shard {root_id!r}")
            expected_prefix = (self._candidate_roots[root_ordinal],)
            if root.prefix != expected_prefix or root.parent_id is not None:
                raise PrefixShardLedgerError(
                    f"root shard does not match immutable universe: {root_id}"
                )

        for shard_id, shard in self._by_id.items():
            if shard.id != shard_id:
                raise PrefixShardLedgerError("shard id index mismatch")
            if shard.generation != self._generation:
                raise PrefixShardLedgerError(
                    f"mixed generation in shard {shard.id!r}"
                )
            if shard.depth != len(shard.prefix) or shard.depth < 1:
                raise PrefixShardLedgerError(
                    f"invalid depth for shard {shard.id!r}"
                )
            for index, move in enumerate(shard.prefix):
                _validate_move(move, label=f"{shard.id}.prefix[{index}]")
            if self._id_for(shard.prefix) != shard.id:
                raise PrefixShardLedgerError(
                    f"non-deterministic shard id for prefix {shard.prefix}"
                )
            if self._by_prefix.get(shard.prefix) is not shard:
                raise PrefixShardLedgerError(
                    f"prefix index does not round-trip for {shard.id!r}"
                )
            if shard.owner is not None and shard.owner not in self._owners:
                raise PrefixShardLedgerError(
                    f"unauthorized owner on shard {shard.id!r}: {shard.owner!r}"
                )

            if shard.parent_id is None:
                if shard.depth != 1:
                    raise PrefixShardLedgerError(
                        f"non-root shard lacks parent: {shard.id!r}"
                    )
            else:
                parent = self._by_id.get(shard.parent_id)
                if parent is None:
                    raise PrefixShardLedgerError(
                        f"missing parent for shard {shard.id!r}"
                    )
                if shard.prefix[:-1] != parent.prefix:
                    raise PrefixShardLedgerError(
                        f"child does not extend parent by one move: {shard.id!r}"
                    )
                if shard.id not in parent.child_ids:
                    raise PrefixShardLedgerError(
                        f"parent does not reference child {shard.id!r}"
                    )

            if shard.child_ids:
                if shard.state != PrefixShardState.RETIRED:
                    raise PrefixShardLedgerError(
                        f"shard with children must be RETIRED: {shard.id!r}"
                    )
                seen_children: set[str] = set()
                for child_id in shard.child_ids:
                    if child_id in seen_children:
                        raise PrefixShardLedgerError(
                            f"duplicate child id under {shard.id!r}"
                        )
                    seen_children.add(child_id)
                    child = self._by_id.get(child_id)
                    if child is None or child.parent_id != shard.id:
                        raise PrefixShardLedgerError(
                            f"child relationship does not round-trip: {child_id!r}"
                        )
            elif shard.state == PrefixShardState.RETIRED:
                raise PrefixShardLedgerError(
                    f"RETIRED shard must retain its children: {shard.id!r}"
                )

        # Parent pointers always shorten the prefix by exactly one, so cycles
        # are impossible if the preceding relationship checks hold.  Walk them
        # anyway so snapshot corruption fails loudly instead of being assumed.
        for shard in self._by_id.values():
            seen: set[str] = set()
            cursor = shard
            while cursor.parent_id is not None:
                if cursor.id in seen:
                    raise PrefixShardLedgerError(
                        f"cycle detected at shard {cursor.id!r}"
                    )
                seen.add(cursor.id)
                cursor = self._by_id[cursor.parent_id]

        frontier = self._frontier_shards_locked()
        for index, left in enumerate(frontier):
            for right in frontier[index + 1 :]:
                if _is_strict_prefix(left.prefix, right.prefix) or _is_strict_prefix(
                    right.prefix, left.prefix
                ):
                    raise PrefixShardLedgerError(
                        f"frontier is not prefix-free: "
                        f"{left.prefix} vs {right.prefix}"
                    )

        if self._partition_assigned:
            roots = [self._by_id[root_id] for root_id in self._root_ids]
            if any(root.owner is None for root in roots):
                raise PrefixShardLedgerError(
                    "assigned root partition contains unowned root"
                )
        elif any(self._by_id[root_id].owner is not None for root_id in self._root_ids):
            raise PrefixShardLedgerError(
                "unassigned root partition contains owned root"
            )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._validate_invariants_locked()
            frontier = self._frontier_shards_locked()
            return {
                "schema_version": self.schema_version,
                "generation": self._generation,
                "revision": self._revision,
                "partition_assigned": self._partition_assigned,
                "owners": list(self._owners),
                "candidate_roots": list(self._candidate_roots),
                "frontier_ids": [shard.id for shard in frontier],
                "shards": [
                    self._snapshot_shard(self._by_id[shard_id])
                    for shard_id in self._ordered_ids_locked()
                ],
            }
