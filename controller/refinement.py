"""Shadow REFINE execution evidence over PrefixShardLedger v2.

REFINE v1 is an observational child of a completed VERIFY run.  It turns final
VERIFY disagreement into deterministic root targets, enumerates one exact
legal child shell per target with the Stockfish shadow oracle, partitions those
children pairwise-disjointly, and records descendant searches.

This module owns the raw data model and integrity contract only.  Process
positioning, decision-boundary locking, cancellation and UCI dispatch remain in
controller.shadow.  REFINE never chooses or changes the outward move.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.search_request import parse_go_request, parse_position_command
from controller.prefix_shards import PrefixShardLedger
from controller.replay import ReplayError, TelemetryStreamWriter, load_manifest, sha256_file
from controller.runtime import RefinementSettings
from controller.verification import (
    VerificationError,
    VerificationRun,
    load_verification_manifest,
)


REFINEMENT_SCHEMA_VERSION = 2
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class RefinementError(RuntimeError):
    """Raised when REFINE cannot preserve its structural/evidence contract."""


@dataclass(frozen=True)
class RefinementTarget:
    target_id: str
    root_move: str
    source_owner: str


@dataclass(frozen=True)
class RefinementPlan:
    refinement_id: str
    generation: int
    source_run_id: str
    source_verification_id: str
    position_id: str
    owners: tuple[str, ...]
    participants: dict[str, str]
    verify_final_by_owner: dict[str, str]
    candidate_roots: tuple[str, ...]
    targets: tuple[RefinementTarget, ...]
    dispatch_limit: dict[str, object]
    nomination_method: str
    child_partition: str
    max_targets: int
    recursive_nomination_method: str
    max_depth: int
    max_expansions: int


@dataclass
class RefinementStage:
    target_id: str
    owner: str
    instance: str
    family: str
    search_id: str
    position_command: str
    command: str
    child_moves: tuple[str, ...]
    shard_ids: tuple[str, ...]
    prefixes: tuple[tuple[str, ...], ...]
    dispatch_order: int
    dispatched_ms: float
    completed_ms: float | None = None
    completion_order: int | None = None
    disposition: str = "running"
    bestmove: str | None = None
    stop_reason: str | None = None
    failure: str | None = None
    done: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "owner": self.owner,
            "instance": self.instance,
            "family": self.family,
            "search_id": self.search_id,
            "position_command": self.position_command,
            "command": self.command,
            "child_moves": list(self.child_moves),
            "shard_ids": list(self.shard_ids),
            "prefixes": [list(prefix) for prefix in self.prefixes],
            "dispatch_order": self.dispatch_order,
            "dispatched_ms": round(self.dispatched_ms, 3),
            "completed_ms": None if self.completed_ms is None else round(self.completed_ms, 3),
            "completion_order": self.completion_order,
            "disposition": self.disposition,
            "bestmove": self.bestmove,
            "stop_reason": self.stop_reason,
            "failure": self.failure,
        }


@dataclass
class RefinementTargetRecord:
    target: RefinementTarget
    source_shard_id: str
    oracle_position_command: str
    oracle_children: tuple[str, ...]
    terminal: bool
    child_partition: dict[str, tuple[str, ...]]
    child_shards: dict[str, tuple[str, ...]]
    stages: dict[str, RefinementStage] = field(default_factory=dict)
    streams: dict[str, TelemetryStreamWriter] = field(default_factory=dict)
    disposition: str = "running"
    stop_reason: str | None = None
    abort_requested: bool = field(default=False, repr=False, compare=False)

    def snapshot(self, *, refinement_dir: Path) -> dict[str, Any]:
        streams: list[dict[str, Any]] = []
        for instance, writer in sorted(self.streams.items()):
            record = writer.snapshot()
            record["path"] = str(writer.path.relative_to(refinement_dir))
            streams.append(record)
        return {
            "target_id": self.target.target_id,
            "root_move": self.target.root_move,
            "source_owner": self.target.source_owner,
            "source_shard_id": self.source_shard_id,
            "child_oracle": {
                "position_command": self.oracle_position_command,
                "children": list(self.oracle_children),
                "terminal": self.terminal,
            },
            "child_partition": {
                owner: list(self.child_partition.get(owner, ()))
                for owner in self.child_partition
            },
            "child_shards": {
                owner: list(self.child_shards.get(owner, ()))
                for owner in self.child_shards
            },
            "stages": [
                stage.snapshot()
                for stage in sorted(self.stages.values(), key=lambda item: item.dispatch_order)
            ],
            "streams": streams,
            "disposition": {
                "target": self.disposition,
                "stop_reason": self.stop_reason,
            },
        }


@dataclass
class RefinementExpansionRecord:
    """One actual recursive expansion below a completed root REFINE shell."""

    expansion_id: str
    seed_target_id: str
    prefix: tuple[str, ...]
    source_owner: str
    source_shard_id: str
    nomination: dict[str, Any]
    oracle_position_command: str
    oracle_children: tuple[str, ...]
    terminal: bool
    child_partition: dict[str, tuple[str, ...]]
    child_shards: dict[str, tuple[str, ...]]
    stages: dict[str, RefinementStage] = field(default_factory=dict)
    streams: dict[str, TelemetryStreamWriter] = field(default_factory=dict)
    disposition: str = "running"
    stop_reason: str | None = None
    abort_requested: bool = field(default=False, repr=False, compare=False)

    @property
    def depth(self) -> int:
        return len(self.prefix)

    def snapshot(self, *, refinement_dir: Path) -> dict[str, Any]:
        streams: list[dict[str, Any]] = []
        for instance, writer in sorted(self.streams.items()):
            record = writer.snapshot()
            record["path"] = str(writer.path.relative_to(refinement_dir))
            streams.append(record)
        return {
            "expansion_id": self.expansion_id,
            "seed_target_id": self.seed_target_id,
            "prefix": list(self.prefix),
            "depth": self.depth,
            "source_owner": self.source_owner,
            "source_shard_id": self.source_shard_id,
            "nomination": dict(self.nomination),
            "child_oracle": {
                "position_command": self.oracle_position_command,
                "children": list(self.oracle_children),
                "terminal": self.terminal,
            },
            "child_partition": {
                owner: list(self.child_partition.get(owner, ()))
                for owner in self.child_partition
            },
            "child_shards": {
                owner: list(self.child_shards.get(owner, ()))
                for owner in self.child_shards
            },
            "stages": [
                stage.snapshot()
                for stage in sorted(self.stages.values(), key=lambda item: item.dispatch_order)
            ],
            "streams": streams,
            "disposition": {
                "expansion": self.disposition,
                "stop_reason": self.stop_reason,
            },
        }


def build_refinement_plan(
    *,
    settings: RefinementSettings,
    verification: VerificationRun,
) -> RefinementPlan:
    """Nominate root targets from completed VERIFY final disagreement only."""

    if not settings.enabled:
        raise RefinementError("refinement is disabled")
    if settings.nomination_method != "verify_final_disagreement_union_v1":
        raise RefinementError(
            f"unsupported refinement nomination method: {settings.nomination_method!r}"
        )
    if verification.disposition != "completed":
        raise RefinementError("refinement requires a completed VERIFY run")

    verify_plan = verification.plan
    if len(verify_plan.owners) != 3:
        raise RefinementError("refinement v1 requires exactly three VERIFY owners")

    final_by_owner: dict[str, str] = {}
    for owner in verify_plan.owners:
        stage = verification.stage_for_owner(owner)
        if stage is None or stage.disposition != "completed":
            raise RefinementError(f"VERIFY owner {owner!r} did not complete cleanly")
        move = stage.bestmove
        if not isinstance(move, str) or not _MOVE_RE.fullmatch(move):
            raise RefinementError(f"VERIFY owner {owner!r} has invalid final move {move!r}")
        if move not in verify_plan.candidate_roots:
            raise RefinementError(
                f"VERIFY owner {owner!r} final move {move!r} escaped candidate roots"
            )
        final_by_owner[owner] = move

    distinct = set(final_by_owner.values())
    target_moves: list[str] = []
    if len(distinct) > 1:
        for move in verify_plan.candidate_roots:
            if move in distinct and move not in target_moves:
                target_moves.append(move)
            if len(target_moves) >= settings.max_targets:
                break

    source_owner_by_move = {
        move: owner for owner, move in verify_plan.nominees_by_owner.items()
    }
    targets = tuple(
        RefinementTarget(
            target_id=f"target-{index:03d}-{move}",
            root_move=move,
            source_owner=source_owner_by_move[move],
        )
        for index, move in enumerate(target_moves)
    )

    return RefinementPlan(
        refinement_id=f"{verify_plan.source_run_id}:refine-v1",
        generation=verify_plan.generation,
        source_run_id=verify_plan.source_run_id,
        source_verification_id=verify_plan.verification_id,
        position_id=verify_plan.position_id,
        owners=tuple(verify_plan.owners),
        participants=dict(verify_plan.participants),
        verify_final_by_owner=final_by_owner,
        candidate_roots=tuple(verify_plan.candidate_roots),
        targets=targets,
        dispatch_limit=dict(settings.dispatch_limit),
        nomination_method=settings.nomination_method,
        child_partition=settings.child_partition,
        max_targets=settings.max_targets,
        recursive_nomination_method=settings.recursive_nomination_method,
        max_depth=settings.max_depth,
        max_expansions=settings.max_expansions,
    )


def partition_children(
    children: tuple[str, ...],
    owners: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """Policy-free child_index modulo partition for one exact child universe."""

    if not owners:
        raise RefinementError("child partition requires at least one owner")
    buckets: dict[str, list[str]] = {owner: [] for owner in owners}
    seen: set[str] = set()
    for index, move in enumerate(children):
        if not isinstance(move, str) or not _MOVE_RE.fullmatch(move):
            raise RefinementError(f"oracle child is not canonical UCI: {move!r}")
        if move in seen:
            raise RefinementError(f"oracle child set contains duplicate move: {move}")
        seen.add(move)
        buckets[owners[index % len(owners)]].append(move)
    return {owner: tuple(buckets[owner]) for owner in owners}


@dataclass
class RefinementRun:
    """Raw child artifact for one shadow REFINE experiment."""

    plan: RefinementPlan
    run_dir: Path
    ledger: PrefixShardLedger
    oracle_instance: str
    source_root_v1_snapshot: dict[str, Any]
    initial_v2_snapshot: dict[str, Any]
    _targets: dict[str, RefinementTargetRecord] = field(default_factory=dict)
    _expansions: dict[str, RefinementExpansionRecord] = field(default_factory=dict)
    _active_stream_by_instance: dict[str, tuple[str, TelemetryStreamWriter]] = field(
        default_factory=dict
    )
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _dispatch_counter: int = 0
    _completion_counter: int = 0
    disposition: str = "running"
    stop_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    finalized: bool = False

    @property
    def refinement_dir(self) -> Path:
        return self.run_dir / "refinement"

    def register_target(
        self,
        *,
        target: RefinementTarget,
        source_shard_id: str,
        oracle_position_command: str,
        oracle_children: tuple[str, ...],
        child_partition: dict[str, tuple[str, ...]],
        child_shards: dict[str, tuple[str, ...]],
    ) -> RefinementTargetRecord:
        with self._lock:
            if target.target_id in self._targets:
                raise RefinementError(f"target {target.target_id!r} registered twice")
            record = RefinementTargetRecord(
                target=target,
                source_shard_id=source_shard_id,
                oracle_position_command=oracle_position_command,
                oracle_children=tuple(oracle_children),
                terminal=not oracle_children,
                child_partition={
                    owner: tuple(child_partition.get(owner, ()))
                    for owner in self.plan.owners
                },
                child_shards={
                    owner: tuple(child_shards.get(owner, ()))
                    for owner in self.plan.owners
                },
            )
            if record.terminal:
                record.disposition = "terminal"
            self._targets[target.target_id] = record
            return record

    def target_record(self, target_id: str) -> RefinementTargetRecord | None:
        with self._lock:
            return self._targets.get(target_id)

    def targets(self) -> tuple[RefinementTargetRecord, ...]:
        with self._lock:
            order = {target.target_id: index for index, target in enumerate(self.plan.targets)}
            return tuple(
                sorted(self._targets.values(), key=lambda item: order[item.target.target_id])
            )

    def register_expansion(
        self,
        *,
        expansion_id: str,
        seed_target_id: str,
        prefix: tuple[str, ...],
        source_owner: str,
        source_shard_id: str,
        nomination: dict[str, Any],
        oracle_position_command: str,
        oracle_children: tuple[str, ...],
        child_partition: dict[str, tuple[str, ...]],
        child_shards: dict[str, tuple[str, ...]],
    ) -> RefinementExpansionRecord:
        with self._lock:
            if expansion_id in self._expansions:
                raise RefinementError(f"expansion {expansion_id!r} registered twice")
            if seed_target_id not in self._targets:
                raise RefinementError(f"unknown seed target: {seed_target_id!r}")
            record = RefinementExpansionRecord(
                expansion_id=expansion_id,
                seed_target_id=seed_target_id,
                prefix=tuple(prefix),
                source_owner=source_owner,
                source_shard_id=source_shard_id,
                nomination=dict(nomination),
                oracle_position_command=oracle_position_command,
                oracle_children=tuple(oracle_children),
                terminal=not oracle_children,
                child_partition={
                    owner: tuple(child_partition.get(owner, ()))
                    for owner in self.plan.owners
                },
                child_shards={
                    owner: tuple(child_shards.get(owner, ()))
                    for owner in self.plan.owners
                },
            )
            if record.terminal:
                record.disposition = "terminal"
            self._expansions[expansion_id] = record
            return record

    def expansion_record(self, expansion_id: str) -> RefinementExpansionRecord | None:
        with self._lock:
            return self._expansions.get(expansion_id)

    def expansions(self) -> tuple[RefinementExpansionRecord, ...]:
        with self._lock:
            return tuple(self._expansions.values())

    def register_stream(
        self,
        target_id: str,
        writer: TelemetryStreamWriter,
    ) -> None:
        with self._lock:
            target = self._targets.get(target_id)
            if target is None:
                raise RefinementError(f"unknown refinement target: {target_id!r}")
            if writer.instance in target.streams:
                raise RefinementError(
                    f"target {target_id!r} already has stream for {writer.instance!r}"
                )
            target.streams[writer.instance] = writer

    def stream(self, target_id: str, instance: str) -> TelemetryStreamWriter | None:
        with self._lock:
            target = self._targets.get(target_id)
            return None if target is None else target.streams.get(instance)

    def register_expansion_stream(
        self,
        expansion_id: str,
        writer: TelemetryStreamWriter,
    ) -> None:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            if expansion is None:
                raise RefinementError(f"unknown refinement expansion: {expansion_id!r}")
            if writer.instance in expansion.streams:
                raise RefinementError(
                    f"expansion {expansion_id!r} already has stream for {writer.instance!r}"
                )
            expansion.streams[writer.instance] = writer

    def expansion_stream(
        self, expansion_id: str, instance: str
    ) -> TelemetryStreamWriter | None:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            return None if expansion is None else expansion.streams.get(instance)

    def activate_stream(self, target_id: str, instance: str) -> None:
        with self._lock:
            writer = self.stream(target_id, instance)
            if writer is None:
                raise RefinementError(
                    f"no REFINE stream for target={target_id!r}, instance={instance!r}"
                )
            current = self._active_stream_by_instance.get(instance)
            if current is not None:
                raise RefinementError(
                    f"instance {instance!r} already mapped to active REFINE stream "
                    f"{current[0]!r}"
                )
            self._active_stream_by_instance[instance] = (target_id, writer)

    def activate_expansion_stream(self, expansion_id: str, instance: str) -> None:
        with self._lock:
            writer = self.expansion_stream(expansion_id, instance)
            if writer is None:
                raise RefinementError(
                    f"no REFINE stream for expansion={expansion_id!r}, instance={instance!r}"
                )
            current = self._active_stream_by_instance.get(instance)
            if current is not None:
                raise RefinementError(
                    f"instance {instance!r} already mapped to active REFINE stream "
                    f"{current[0]!r}"
                )
            self._active_stream_by_instance[instance] = (expansion_id, writer)

    def deactivate_stream(self, target_id: str, instance: str) -> None:
        with self._lock:
            current = self._active_stream_by_instance.get(instance)
            if current is not None and current[0] == target_id:
                self._active_stream_by_instance.pop(instance, None)

    def observation_stream(self, instance: str) -> TelemetryStreamWriter | None:
        with self._lock:
            current = self._active_stream_by_instance.get(instance)
            return None if current is None else current[1]

    def record_dispatch(
        self,
        *,
        target_id: str,
        owner: str,
        instance: str,
        family: str,
        search_id: str,
        position_command: str,
        command: str,
        child_moves: tuple[str, ...],
        shard_ids: tuple[str, ...],
        prefixes: tuple[tuple[str, ...], ...],
        dispatched_ms: float,
    ) -> RefinementStage:
        with self._lock:
            target = self._targets.get(target_id)
            if target is None:
                raise RefinementError(f"unknown refinement target: {target_id!r}")
            if owner in target.stages:
                raise RefinementError(
                    f"target {target_id!r} owner {owner!r} dispatched twice"
                )
            self._dispatch_counter += 1
            stage = RefinementStage(
                target_id=target_id,
                owner=owner,
                instance=instance,
                family=family,
                search_id=search_id,
                position_command=position_command,
                command=command,
                child_moves=tuple(child_moves),
                shard_ids=tuple(shard_ids),
                prefixes=tuple(prefixes),
                dispatch_order=self._dispatch_counter,
                dispatched_ms=dispatched_ms,
            )
            target.stages[owner] = stage
            return stage

    def record_expansion_dispatch(
        self,
        *,
        expansion_id: str,
        owner: str,
        instance: str,
        family: str,
        search_id: str,
        position_command: str,
        command: str,
        child_moves: tuple[str, ...],
        shard_ids: tuple[str, ...],
        prefixes: tuple[tuple[str, ...], ...],
        dispatched_ms: float,
    ) -> RefinementStage:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            if expansion is None:
                raise RefinementError(f"unknown refinement expansion: {expansion_id!r}")
            if owner in expansion.stages:
                raise RefinementError(
                    f"expansion {expansion_id!r} owner {owner!r} dispatched twice"
                )
            self._dispatch_counter += 1
            stage = RefinementStage(
                target_id=expansion_id,
                owner=owner,
                instance=instance,
                family=family,
                search_id=search_id,
                position_command=position_command,
                command=command,
                child_moves=tuple(child_moves),
                shard_ids=tuple(shard_ids),
                prefixes=tuple(prefixes),
                dispatch_order=self._dispatch_counter,
                dispatched_ms=dispatched_ms,
            )
            expansion.stages[owner] = stage
            return stage

    def record_completion(
        self,
        stage: RefinementStage,
        *,
        completed_ms: float,
        disposition: str,
        bestmove: str | None = None,
        stop_reason: str | None = None,
        failure: str | None = None,
    ) -> None:
        with self._lock:
            if stage.disposition != "running":
                stage.done.set()
                self.deactivate_stream(stage.target_id, stage.instance)
                return
            self._completion_counter += 1
            stage.completed_ms = completed_ms
            stage.completion_order = self._completion_counter
            stage.disposition = disposition
            stage.bestmove = bestmove
            stage.stop_reason = stop_reason
            stage.failure = failure
            stage.done.set()
            self.deactivate_stream(stage.target_id, stage.instance)

    def stage_for_target_owner(
        self, target_id: str, owner: str
    ) -> RefinementStage | None:
        with self._lock:
            target = self._targets.get(target_id)
            return None if target is None else target.stages.get(owner)

    def stage_for_instance(self, instance: str) -> RefinementStage | None:
        with self._lock:
            for target in self._targets.values():
                for stage in target.stages.values():
                    if stage.instance == instance and not stage.done.is_set():
                        return stage
            for expansion in self._expansions.values():
                for stage in expansion.stages.values():
                    if stage.instance == instance and not stage.done.is_set():
                        return stage
            return None

    def active_stages(self) -> tuple[RefinementStage, ...]:
        with self._lock:
            return tuple(
                [
                    stage
                    for target in self._targets.values()
                    for stage in target.stages.values()
                    if not stage.done.is_set()
                ]
                + [
                    stage
                    for expansion in self._expansions.values()
                    for stage in expansion.stages.values()
                    if not stage.done.is_set()
                ]
            )

    def request_target_abort(self, target_id: str, reason: str) -> None:
        """Mark a target's in-flight stages as intentionally being stopped.

        This is transient lifecycle state, not a derived conclusion. Completion
        callbacks use it to avoid sealing shards whose engine answered only
        because a partially dispatched target was being torn down.
        """

        with self._lock:
            target = self._targets.get(target_id)
            if target is None:
                raise RefinementError(f"unknown refinement target: {target_id!r}")
            target.abort_requested = True
            target.disposition = "incomplete"
            target.stop_reason = reason

    def target_abort_requested(self, target_id: str) -> bool:
        with self._lock:
            target = self._targets.get(target_id)
            return False if target is None else target.abort_requested

    def set_target_disposition(
        self,
        target_id: str,
        disposition: str,
        reason: str | None = None,
    ) -> None:
        with self._lock:
            target = self._targets.get(target_id)
            if target is None:
                raise RefinementError(f"unknown refinement target: {target_id!r}")
            target.disposition = disposition
            target.stop_reason = reason

    def stage_for_expansion_owner(
        self, expansion_id: str, owner: str
    ) -> RefinementStage | None:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            return None if expansion is None else expansion.stages.get(owner)

    def request_expansion_abort(self, expansion_id: str, reason: str) -> None:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            if expansion is None:
                raise RefinementError(f"unknown refinement expansion: {expansion_id!r}")
            expansion.abort_requested = True
            expansion.disposition = "incomplete"
            expansion.stop_reason = reason

    def expansion_abort_requested(self, expansion_id: str) -> bool:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            return False if expansion is None else expansion.abort_requested

    def set_expansion_disposition(
        self,
        expansion_id: str,
        disposition: str,
        reason: str | None = None,
    ) -> None:
        with self._lock:
            expansion = self._expansions.get(expansion_id)
            if expansion is None:
                raise RefinementError(f"unknown refinement expansion: {expansion_id!r}")
            expansion.disposition = disposition
            expansion.stop_reason = reason

    def set_scope_disposition(
        self,
        scope_id: str,
        disposition: str,
        reason: str | None = None,
    ) -> None:
        """Set target or recursive-expansion disposition by stage scope id."""

        with self._lock:
            target = self._targets.get(scope_id)
            if target is not None:
                target.disposition = disposition
                target.stop_reason = reason
                return
            expansion = self._expansions.get(scope_id)
            if expansion is not None:
                expansion.disposition = disposition
                expansion.stop_reason = reason
                return
            raise RefinementError(f"unknown refinement scope: {scope_id!r}")

    def set_disposition(self, disposition: str, reason: str | None = None) -> None:
        with self._lock:
            self.disposition = disposition
            self.stop_reason = reason

    def note(self, message: str) -> None:
        with self._lock:
            if len(self.notes) < 64:
                self.notes.append(message)

    def finalize(
        self,
        *,
        source_manifest_sha256: str,
        verification_manifest_sha256: str,
    ) -> dict[str, Any]:
        with self._lock:
            if self.finalized:
                return load_refinement_manifest(self.run_dir)
            self.finalized = True
            targets = list(self._targets.values())
            expansions = list(self._expansions.values())

        for target in targets:
            for stage in target.stages.values():
                if stage.disposition == "running":
                    stage.disposition = "unresolved"
                    stage.failure = stage.failure or "refinement finalized before completion"
                    stage.done.set()
                    self.deactivate_stream(stage.target_id, stage.instance)

        for expansion in expansions:
            for stage in expansion.stages.values():
                if stage.disposition == "running":
                    stage.disposition = "unresolved"
                    stage.failure = stage.failure or "recursive refinement finalized before completion"
                    stage.done.set()
                    self.deactivate_stream(stage.target_id, stage.instance)

        writers = [
            writer
            for target in targets
            for writer in target.streams.values()
        ] + [
            writer
            for expansion in expansions
            for writer in expansion.streams.values()
        ]
        for writer in writers:
            writer.close()

        manifest = {
            "schema_version": REFINEMENT_SCHEMA_VERSION,
            "refinement_id": self.plan.refinement_id,
            "source": {
                "run_id": self.plan.source_run_id,
                "manifest_sha256": source_manifest_sha256,
                "verification_id": self.plan.source_verification_id,
                "verification_manifest_sha256": verification_manifest_sha256,
            },
            "generation": self.plan.generation,
            "position_id": self.plan.position_id,
            "nomination": {
                "method": self.plan.nomination_method,
                "verify_final_by_owner": dict(self.plan.verify_final_by_owner),
                "candidate_roots": list(self.plan.candidate_roots),
                "targets": [target.root_move for target in self.plan.targets],
                "max_targets": self.plan.max_targets,
            },
            "owners": list(self.plan.owners),
            "participants": dict(self.plan.participants),
            "dispatch_limit": dict(self.plan.dispatch_limit),
            "child_partition_method": self.plan.child_partition,
            "child_oracle_instance": self.oracle_instance,
            "recursive_policy": {
                "method": self.plan.recursive_nomination_method,
                "queue": "breadth_first_v1",
                "max_depth": self.plan.max_depth,
                "max_expansions": self.plan.max_expansions,
            },
            "prefix_ledger": {
                "origin": "mirror_of_completed_root_v1",
                "source_root_v1_snapshot": self.source_root_v1_snapshot,
                "initial_v2_snapshot": self.initial_v2_snapshot,
                "final_v2_snapshot": self.ledger.snapshot(),
            },
            "targets": [
                target.snapshot(refinement_dir=self.refinement_dir)
                for target in self.targets()
            ],
            "expansions": [
                expansion.snapshot(refinement_dir=self.refinement_dir)
                for expansion in self.expansions()
            ],
            "disposition": {
                "run": self.disposition,
                "stop_reason": self.stop_reason,
            },
            "notes": list(self.notes),
        }
        self.refinement_dir.mkdir(parents=True, exist_ok=True)
        (self.refinement_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


def load_refinement_manifest(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "refinement" / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefinementError(f"cannot load refinement manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RefinementError("refinement manifest root must be an object")
    version = manifest.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version not in (1, REFINEMENT_SCHEMA_VERSION)
    ):
        raise RefinementError(
            f"unsupported refinement manifest schema_version: {version!r}"
        )
    return manifest


def _expected_targets_from_verification(
    verification: dict[str, Any],
    *,
    max_targets: int,
) -> tuple[list[str], dict[str, str]]:
    nomination = verification.get("nomination") or {}
    candidates = list(nomination.get("candidate_roots") or [])
    stages = verification.get("stages") or []
    final_by_owner = {
        stage.get("owner"): stage.get("bestmove")
        for stage in stages
        if isinstance(stage, dict) and stage.get("disposition") == "completed"
    }
    distinct = {
        move for move in final_by_owner.values()
        if isinstance(move, str)
    }
    if len(distinct) <= 1:
        return [], final_by_owner
    return [move for move in candidates if move in distinct][:max_targets], final_by_owner


def verify_refinement_integrity(run_dir: Path | str) -> list[str]:
    """Verify REFINE provenance, exact child ownership and telemetry containment."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    try:
        parent = load_manifest(run_dir)
        verification = load_verification_manifest(run_dir)
        manifest = load_refinement_manifest(run_dir)
    except (ReplayError, VerificationError, RefinementError) as exc:
        return [str(exc)]

    source = manifest.get("source") or {}
    if source.get("run_id") != parent.get("run_id"):
        problems.append("refinement source run_id does not match parent replay")
    parent_path = run_dir / "manifest.json"
    if parent_path.is_file() and source.get("manifest_sha256") != sha256_file(parent_path):
        problems.append("refinement source parent manifest hash mismatch")

    verification_path = run_dir / "verification" / "manifest.json"
    if source.get("verification_id") != verification.get("verification_id"):
        problems.append("refinement source verification_id mismatch")
    if (
        verification_path.is_file()
        and source.get("verification_manifest_sha256") != sha256_file(verification_path)
    ):
        problems.append("refinement source verification manifest hash mismatch")

    if manifest.get("generation") != parent.get("generation"):
        problems.append("refinement generation does not match parent replay")
    if manifest.get("position_id") != (parent.get("position") or {}).get("position_id"):
        problems.append("refinement position_id does not match parent replay")

    parent_oracle = (parent.get("legal_root_oracle") or {}).get("instance")
    if manifest.get("child_oracle_instance") != parent_oracle:
        problems.append("refinement child oracle differs from parent configured oracle")

    parent_variant = (parent.get("position") or {}).get("variant", "standard")
    if parent_variant not in ("standard", "chess960"):
        problems.append(f"parent replay has unsupported variant: {parent_variant!r}")
        parent_variant = "standard"

    nomination = manifest.get("nomination") or {}
    max_targets = nomination.get("max_targets")
    if isinstance(max_targets, bool) or not isinstance(max_targets, int):
        problems.append("refinement max_targets is invalid")
        max_targets = 0
    expected_targets, expected_finals = _expected_targets_from_verification(
        verification,
        max_targets=max_targets,
    )
    if nomination.get("targets") != expected_targets:
        problems.append("refinement targets differ from deterministic VERIFY disagreement")
    if nomination.get("verify_final_by_owner") != expected_finals:
        problems.append("refinement VERIFY-final provenance differs from source artifact")

    owners = manifest.get("owners")
    if owners != ["stockfish", "reckless", "lc0"]:
        problems.append("refinement owners are not the frozen three-owner order")
        owners = ["stockfish", "reckless", "lc0"]

    participants = manifest.get("participants")
    if not isinstance(participants, dict) or set(participants) != set(owners):
        problems.append("refinement participants are not exactly the three solver owners")
        participants = {}

    prefix_ledger = manifest.get("prefix_ledger") or {}
    source_root_snapshot = prefix_ledger.get("source_root_v1_snapshot")
    if source_root_snapshot != (parent.get("ledger") or {}).get("post_run_snapshot"):
        problems.append("refinement root-v1 source snapshot differs from finalized parent")

    initial_v2 = prefix_ledger.get("initial_v2_snapshot") or {}
    source_candidates = (
        []
        if not isinstance(source_root_snapshot, dict)
        else source_root_snapshot.get("candidate_roots") or []
    )
    if initial_v2.get("schema_version") != 2:
        problems.append("refinement initial PrefixShardLedger schema is not v2")
    if initial_v2.get("generation") != manifest.get("generation"):
        problems.append("refinement initial v2 generation mismatch")
    if initial_v2.get("owners") != owners:
        problems.append("refinement initial v2 owner order mismatch")
    if initial_v2.get("candidate_roots") != source_candidates:
        problems.append("refinement initial v2 candidate universe mismatch")
    initial_shards = initial_v2.get("shards") or []
    if any(
        not isinstance(item, dict)
        or item.get("depth") != 1
        or item.get("state") != "sealed"
        or item.get("child_ids")
        for item in initial_shards
    ):
        problems.append("refinement initial v2 snapshot is not a sealed root-only mirror")

    target_rows = manifest.get("targets") or []
    recorded_targets = [
        row.get("root_move") for row in target_rows if isinstance(row, dict)
    ]
    run_disposition = (manifest.get("disposition") or {}).get("run")
    if run_disposition == "completed":
        if recorded_targets != expected_targets:
            problems.append("completed refinement target records differ from nomination")
    elif recorded_targets != expected_targets[: len(recorded_targets)]:
        problems.append("incomplete refinement target records are not a nomination prefix")

    final_v2 = (manifest.get("prefix_ledger") or {}).get("final_v2_snapshot") or {}
    if final_v2.get("schema_version") != 2:
        problems.append("refinement final PrefixShardLedger schema is not v2")
    if final_v2.get("generation") != manifest.get("generation"):
        problems.append("refinement final v2 generation mismatch")
    if final_v2.get("owners") != owners:
        problems.append("refinement final v2 owner order mismatch")
    if final_v2.get("candidate_roots") != source_candidates:
        problems.append("refinement final v2 candidate universe mismatch")
    final_shards = {
        item.get("id"): item
        for item in final_v2.get("shards", [])
        if isinstance(item, dict)
    }
    frontier_prefixes = [
        tuple(final_shards[shard_id].get("prefix", []))
        for shard_id in final_v2.get("frontier_ids", [])
        if shard_id in final_shards
    ]
    for index, left in enumerate(frontier_prefixes):
        for right in frontier_prefixes[index + 1 :]:
            if (
                len(left) < len(right)
                and right[: len(left)] == left
            ) or (
                len(right) < len(left)
                and left[: len(right)] == right
            ):
                problems.append("refinement final PrefixShardLedger frontier is not prefix-free")

    refinement_dir = run_dir / "refinement"
    for row in target_rows:
        if not isinstance(row, dict):
            problems.append("refinement target is not an object")
            continue
        root_move = row.get("root_move")
        oracle = row.get("child_oracle") or {}
        children = tuple(oracle.get("children") or [])
        if len(children) != len(set(children)):
            problems.append(f"{row.get('target_id')}: duplicate oracle child")
        if any(not isinstance(move, str) or not _MOVE_RE.fullmatch(move) for move in children):
            problems.append(f"{row.get('target_id')}: non-canonical oracle child")
        if bool(oracle.get("terminal")) != (len(children) == 0):
            problems.append(f"{row.get('target_id')}: terminal flag disagrees with child set")

        partition = row.get("child_partition") or {}
        try:
            expected_partition = partition_children(children, tuple(owners))
        except RefinementError as exc:
            problems.append(f"{row.get('target_id')}: invalid oracle child set: {exc}")
            expected_partition = {owner: () for owner in owners}
        union: list[str] = []
        for owner in owners:
            moves = partition.get(owner)
            if not isinstance(moves, list):
                problems.append(
                    f"{row.get('target_id')}: missing child partition for {owner}"
                )
                moves = []
            union.extend(moves)
            if tuple(moves) != expected_partition.get(owner, ()):
                problems.append(
                    f"{row.get('target_id')}:{owner}: child partition differs from child_index_modulo"
                )
        if len(union) != len(set(union)):
            problems.append(f"{row.get('target_id')}: child partition overlaps")
        if set(union) != set(children) or len(union) != len(children):
            problems.append(
                f"{row.get('target_id')}: child partition does not cover exact oracle universe"
            )

        child_shards = row.get("child_shards") or {}
        parent_shard = final_shards.get(row.get("source_shard_id"))
        if parent_shard is None:
            problems.append(
                f"{row.get('target_id')}: source parent shard is missing from final v2"
            )
        elif children and parent_shard.get("state") != "retired":
            problems.append(
                f"{row.get('target_id')}: split source parent is not RETIRED"
            )
        elif not children and parent_shard.get("state") != "sealed":
            problems.append(
                f"{row.get('target_id')}: terminal source root is not SEALED"
            )

        child_shards = row.get("child_shards") or {}
        for owner in owners:
            moves = tuple(partition.get(owner) or [])
            shard_ids = tuple(child_shards.get(owner) or [])
            if len(shard_ids) != len(moves):
                problems.append(
                    f"{row.get('target_id')}:{owner}: child shard count differs from owned moves"
                )
                continue
            for move, shard_id in zip(moves, shard_ids):
                shard = final_shards.get(shard_id)
                if shard is None:
                    problems.append(
                        f"{row.get('target_id')}:{owner}: missing child shard {shard_id!r}"
                    )
                    continue
                if shard.get("prefix") != [root_move, move] or shard.get("owner") != owner:
                    problems.append(
                        f"{row.get('target_id')}:{owner}: child shard identity/owner mismatch"
                    )

        stage_by_instance: dict[str, dict[str, Any]] = {}
        for stage in row.get("stages") or []:
            if not isinstance(stage, dict):
                problems.append(f"{row.get('target_id')}: stage is not an object")
                continue
            owner = stage.get("owner")
            instance = stage.get("instance")
            if participants.get(owner) != instance:
                problems.append(
                    f"{row.get('target_id')}: stage owner/instance mapping mismatch"
                )
            expected_moves = tuple(partition.get(owner) or [])
            if tuple(stage.get("child_moves") or []) != expected_moves:
                problems.append(
                    f"{row.get('target_id')}:{owner}: stage child set differs from partition"
                )
            if tuple(stage.get("shard_ids") or []) != tuple(child_shards.get(owner) or []):
                problems.append(
                    f"{row.get('target_id')}:{owner}: stage shard ids differ from ownership"
                )
            parent_engine = (parent.get("engines") or {}).get(instance) or {}
            if stage.get("family") != parent_engine.get("engine"):
                problems.append(
                    f"{row.get('target_id')}:{owner}: stage family differs from parent engine identity"
                )
            try:
                request = parse_go_request(stage.get("command", ""))
            except Exception as exc:
                problems.append(
                    f"{row.get('target_id')}:{owner}: cannot parse stage command: {exc}"
                )
                continue
            if tuple(request.get("root_moves") or []) != expected_moves:
                problems.append(
                    f"{row.get('target_id')}:{owner}: go searchmoves differ from partition"
                )
            expected_request = {
                "limits": [
                    {
                        "name": name,
                        "value": value,
                        "semantics": f"uci.go.{name}",
                    }
                    for name, value in sorted((manifest.get("dispatch_limit") or {}).items())
                ],
                "raw": stage.get("command", ""),
                "root_moves": list(expected_moves),
            }
            if request.get("limits") != expected_request["limits"]:
                problems.append(
                    f"{row.get('target_id')}:{owner}: stage limits differ from REFINE plan"
                )
            if request.get("unknown_tokens"):
                problems.append(
                    f"{row.get('target_id')}:{owner}: stage command contains unknown tokens"
                )
            try:
                position = parse_position_command(
                    stage.get("position_command", ""),
                    variant=parent_variant,
                )
                oracle_position = parse_position_command(
                    oracle.get("position_command", ""),
                    variant=parent_variant,
                )
            except Exception as exc:
                problems.append(
                    f"{row.get('target_id')}:{owner}: cannot parse descendant position: {exc}"
                )
            else:
                if position != oracle_position:
                    problems.append(
                        f"{row.get('target_id')}:{owner}: stage position differs from oracle position"
                    )
                parent_position = parent.get("position") or {}
                expected_moves_from_parent = tuple(parent_position.get("moves") or []) + (
                    root_move,
                )
                if (
                    oracle_position.base_fen != parent_position.get("base_fen")
                    or oracle_position.moves != expected_moves_from_parent
                    or oracle_position.variant != parent_variant
                ):
                    problems.append(
                        f"{row.get('target_id')}:{owner}: oracle/stage position is not external position + target root"
                    )
            prefixes = stage.get("prefixes") or []
            if prefixes != [[root_move, move] for move in expected_moves]:
                problems.append(
                    f"{row.get('target_id')}:{owner}: full prefixes differ from owned children"
                )
            if instance in stage_by_instance:
                problems.append(
                    f"{row.get('target_id')}: duplicate instance stage {instance!r}"
                )
            stage_by_instance[instance] = stage

        expected_stage_owners = {
            owner for owner in participants if partition.get(owner)
        }
        if (row.get("disposition") or {}).get("target") == "completed":
            actual_stage_owners = {
                stage.get("owner")
                for stage in row.get("stages") or []
                if isinstance(stage, dict)
            }
            if actual_stage_owners != expected_stage_owners:
                problems.append(
                    f"{row.get('target_id')}: completed target missing required owner stages"
                )
            if any(
                stage.get("disposition") != "completed"
                for stage in row.get("stages") or []
                if isinstance(stage, dict)
            ):
                problems.append(
                    f"{row.get('target_id')}: completed target contains non-completed stage"
                )
            expected_stream_instances = {
                participants[owner] for owner in expected_stage_owners
                if owner in participants
            }
            actual_stream_instances = {
                record.get("instance")
                for record in row.get("streams") or []
                if isinstance(record, dict)
            }
            if actual_stream_instances != expected_stream_instances:
                problems.append(
                    f"{row.get('target_id')}: completed target stream set differs from stages"
                )
            for owner in expected_stage_owners:
                for shard_id in child_shards.get(owner) or []:
                    shard = final_shards.get(shard_id)
                    if shard is None:
                        problems.append(
                            f"{row.get('target_id')}:{owner}: completed child shard is missing"
                        )
                    elif manifest.get("schema_version") == 1 and shard.get("state") != "sealed":
                        problems.append(
                            f"{row.get('target_id')}:{owner}: completed child shard is not SEALED"
                        )
                    elif manifest.get("schema_version") >= 2 and shard.get("state") not in (
                        "sealed",
                        "retired",
                    ):
                        problems.append(
                            f"{row.get('target_id')}:{owner}: completed child shard has invalid recursive state"
                        )

        for record in row.get("streams") or []:
            relative = Path(str(record.get("path", "")))
            expected_relative = Path(str(row.get("target_id"))) / (
                f"{record.get('instance')}.jsonl"
            )
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative != expected_relative
            ):
                problems.append(
                    f"{row.get('target_id')}:{record.get('instance')}: "
                    "stream path is not the canonical target-local path"
                )
                continue
            path = refinement_dir / relative
            if not path.is_file():
                problems.append(
                    f"{row.get('target_id')}: missing stream {record.get('path')!r}"
                )
                continue
            if sha256_file(path) != record.get("sha256"):
                problems.append(
                    f"{row.get('target_id')}:{record.get('instance')}: stream hash mismatch"
                )
            if path.stat().st_size != record.get("bytes"):
                problems.append(
                    f"{row.get('target_id')}:{record.get('instance')}: stream size mismatch"
                )
            try:
                events = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(
                    f"{row.get('target_id')}:{record.get('instance')}: cannot parse stream: {exc}"
                )
                continue
            stage = stage_by_instance.get(record.get("instance"))
            if stage is None:
                problems.append(
                    f"{row.get('target_id')}:{record.get('instance')}: stream has no matching stage"
                )
            else:
                if record.get("search_ids") != [stage.get("search_id")]:
                    problems.append(
                        f"{row.get('target_id')}:{record.get('instance')}: stream search_ids differ from stage"
                    )
                if (
                    (row.get("disposition") or {}).get("target") == "completed"
                    and not record.get("contract_validatable")
                ):
                    problems.append(
                        f"{row.get('target_id')}:{record.get('instance')}: completed target stream is not contract-validatable"
                    )
            allowed_order = tuple(
                () if stage is None else stage.get("child_moves") or []
            )
            allowed = set(allowed_order)
            expected_position_id: str | None = None
            if stage is not None:
                try:
                    expected_position_id = parse_position_command(
                        stage.get("position_command", ""),
                        variant=parent_variant,
                    ).position_id
                except Exception:
                    expected_position_id = None
            for event in events:
                if stage is not None and (
                    event.get("search_id") != stage.get("search_id")
                    or event.get("engine_instance") != stage.get("instance")
                ):
                    problems.append(
                        f"{row.get('target_id')}:{record.get('instance')}: telemetry identity differs from stage"
                    )
                    break
                if (
                    expected_position_id is not None
                    and event.get("position_id") != expected_position_id
                ):
                    problems.append(
                        f"{row.get('target_id')}:{record.get('instance')}: telemetry position_id differs"
                    )
                    break
                kind = event.get("event_type")
                if kind == "search.started":
                    controller = event.get("controller") or {}
                    if controller.get("phase") != "REFINE":
                        problems.append(
                            f"{row.get('target_id')}:{record.get('instance')}: phase is not REFINE"
                        )
                    if tuple((event.get("request") or {}).get("root_moves") or []) != allowed_order:
                        problems.append(
                            f"{row.get('target_id')}:{record.get('instance')}: telemetry root set differs"
                        )
                    if stage is not None:
                        try:
                            stage_position = parse_position_command(
                                stage.get("position_command", ""),
                                variant=parent_variant,
                            )
                        except Exception:
                            stage_position = None
                        event_position = event.get("position") or {}
                        if stage_position is not None and (
                            event_position.get("base_fen") != stage_position.base_fen
                            or tuple(event_position.get("moves") or []) != stage_position.moves
                        ):
                            problems.append(
                                f"{row.get('target_id')}:{record.get('instance')}: telemetry position differs"
                            )
                elif kind == "candidate.update":
                    candidate = event.get("candidate") or {}
                    move = candidate.get("move")
                    pv = candidate.get("pv") or []
                    if move not in allowed or (pv and pv[0] not in allowed):
                        problems.append(
                            f"{row.get('target_id')}:{record.get('instance')}: candidate escaped REFINE region"
                        )
                elif kind == "search.complete":
                    move = event.get("bestmove")
                    if move is not None and move not in allowed:
                        problems.append(
                            f"{row.get('target_id')}:{record.get('instance')}: bestmove escaped REFINE region"
                        )
                    if stage is not None and move != stage.get("bestmove"):
                        problems.append(
                            f"{row.get('target_id')}:{record.get('instance')}: telemetry bestmove differs from stage"
                        )


    if manifest.get("schema_version") >= 2:
        recursive = manifest.get("recursive_policy") or {}
        max_depth = recursive.get("max_depth")
        max_expansions = recursive.get("max_expansions")
        if recursive.get("method") != "stage_terminal_bestmove_v1":
            problems.append("refinement recursive nomination policy is not frozen v1")
        if recursive.get("queue") != "breadth_first_v1":
            problems.append("refinement recursive queue policy is not breadth_first_v1")
        if (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 2
            or max_depth > 8
        ):
            problems.append("refinement recursive max_depth is invalid")
            max_depth = 2
        if (
            isinstance(max_expansions, bool)
            or not isinstance(max_expansions, int)
            or max_expansions < 1
            or max_expansions > 64
        ):
            problems.append("refinement recursive max_expansions is invalid")
            max_expansions = 0

        expansion_rows = manifest.get("expansions") or []
        if not isinstance(expansion_rows, list):
            problems.append("refinement expansions is not an array")
            expansion_rows = []
        if len(expansion_rows) > max_expansions:
            problems.append("refinement recorded more recursive expansions than configured")

        target_by_id = {
            row.get("target_id"): row
            for row in target_rows
            if isinstance(row, dict) and isinstance(row.get("target_id"), str)
        }
        expansion_by_id: dict[str, dict[str, Any]] = {}
        seen_prefixes: set[tuple[str, ...]] = set()
        for row in expansion_rows:
            if not isinstance(row, dict):
                problems.append("recursive refinement expansion is not an object")
                continue
            expansion_id = row.get("expansion_id")
            prefix = tuple(row.get("prefix") or [])
            if not isinstance(expansion_id, str) or not expansion_id:
                problems.append("recursive refinement expansion has invalid id")
                continue
            if expansion_id in expansion_by_id:
                problems.append(f"{expansion_id}: duplicate recursive expansion id")
                continue
            expansion_by_id[expansion_id] = row
            if prefix in seen_prefixes:
                problems.append(f"{expansion_id}: duplicate recursive expansion prefix")
            seen_prefixes.add(prefix)
            if row.get("depth") != len(prefix):
                problems.append(f"{expansion_id}: recursive expansion depth mismatch")
            if len(prefix) < 2 or len(prefix) >= max_depth:
                problems.append(f"{expansion_id}: recursive expansion lies outside depth bounds")
            if any(
                not isinstance(move, str) or not _MOVE_RE.fullmatch(move)
                for move in prefix
            ):
                problems.append(f"{expansion_id}: recursive expansion prefix is not canonical")

            nomination_record = row.get("nomination") or {}
            source_scope = nomination_record.get("expansion_id")
            source_owner = row.get("source_owner")
            source_search_id = nomination_record.get("source_search_id")
            source_stage: dict[str, Any] | None = None
            source_parent_prefix: tuple[str, ...] | None = None
            if source_scope in target_by_id:
                source = target_by_id[source_scope]
                source_parent_prefix = (source.get("root_move"),)
                source_stage = next(
                    (
                        stage
                        for stage in source.get("stages") or []
                        if isinstance(stage, dict)
                        and stage.get("owner") == source_owner
                    ),
                    None,
                )
            elif source_scope in expansion_by_id:
                source = expansion_by_id[source_scope]
                source_parent_prefix = tuple(source.get("prefix") or [])
                source_stage = next(
                    (
                        stage
                        for stage in source.get("stages") or []
                        if isinstance(stage, dict)
                        and stage.get("owner") == source_owner
                    ),
                    None,
                )
            elif isinstance(source_scope, str):
                # Breadth-first serialization requires every recursive source
                # expansion to appear before its child.
                problems.append(
                    f"{expansion_id}: nomination source {source_scope!r} is unavailable"
                )
            else:
                problems.append(f"{expansion_id}: nomination source is invalid")

            if source_stage is None:
                problems.append(f"{expansion_id}: nomination source stage is missing")
            else:
                if source_stage.get("disposition") != "completed":
                    problems.append(
                        f"{expansion_id}: nomination came from non-completed stage"
                    )
                if source_stage.get("search_id") != source_search_id:
                    problems.append(
                        f"{expansion_id}: nomination search_id differs from source stage"
                    )
                if source_stage.get("bestmove") != (prefix[-1] if prefix else None):
                    problems.append(
                        f"{expansion_id}: nomination move differs from source terminal"
                    )
            if source_parent_prefix is not None and prefix[:-1] != source_parent_prefix:
                problems.append(
                    f"{expansion_id}: nomination prefix does not extend source scope by one move"
                )

            oracle = row.get("child_oracle") or {}
            children = tuple(oracle.get("children") or [])
            if len(children) != len(set(children)):
                problems.append(f"{expansion_id}: duplicate recursive oracle child")
            if any(
                not isinstance(move, str) or not _MOVE_RE.fullmatch(move)
                for move in children
            ):
                problems.append(f"{expansion_id}: non-canonical recursive oracle child")
            if bool(oracle.get("terminal")) != (len(children) == 0):
                problems.append(f"{expansion_id}: recursive terminal flag mismatch")

            try:
                oracle_position = parse_position_command(
                    oracle.get("position_command", ""),
                    variant=parent_variant,
                )
            except Exception as exc:
                problems.append(
                    f"{expansion_id}: cannot parse recursive oracle position: {exc}"
                )
                oracle_position = None
            if oracle_position is not None:
                parent_position = parent.get("position") or {}
                expected_moves_from_parent = tuple(parent_position.get("moves") or []) + prefix
                if (
                    oracle_position.base_fen != parent_position.get("base_fen")
                    or oracle_position.moves != expected_moves_from_parent
                    or oracle_position.variant != parent_variant
                ):
                    problems.append(
                        f"{expansion_id}: recursive oracle position is not external position + full prefix"
                    )

            partition = row.get("child_partition") or {}
            try:
                expected_partition = partition_children(children, tuple(owners))
            except RefinementError as exc:
                problems.append(f"{expansion_id}: invalid recursive child set: {exc}")
                expected_partition = {owner: () for owner in owners}
            union: list[str] = []
            for owner in owners:
                moves = partition.get(owner)
                if not isinstance(moves, list):
                    problems.append(
                        f"{expansion_id}: missing recursive child partition for {owner}"
                    )
                    moves = []
                union.extend(moves)
                if tuple(moves) != expected_partition.get(owner, ()):
                    problems.append(
                        f"{expansion_id}:{owner}: recursive partition differs from child_index_modulo"
                    )
            if len(union) != len(set(union)) or set(union) != set(children):
                problems.append(
                    f"{expansion_id}: recursive partition is not exact/disjoint"
                )

            source_shard = final_shards.get(row.get("source_shard_id"))
            if source_shard is None:
                problems.append(f"{expansion_id}: recursive source shard is missing")
            elif tuple(source_shard.get("prefix") or []) != prefix:
                problems.append(f"{expansion_id}: recursive source shard prefix mismatch")
            elif source_shard.get("owner") != source_owner:
                problems.append(f"{expansion_id}: recursive source shard owner mismatch")
            elif children and source_shard.get("state") != "retired":
                problems.append(f"{expansion_id}: recursive split source is not RETIRED")
            elif not children and source_shard.get("state") != "sealed":
                problems.append(f"{expansion_id}: recursive terminal source is not SEALED")

            child_shards = row.get("child_shards") or {}
            for owner in owners:
                moves = tuple(partition.get(owner) or [])
                shard_ids = tuple(child_shards.get(owner) or [])
                if len(moves) != len(shard_ids):
                    problems.append(
                        f"{expansion_id}:{owner}: recursive child shard count mismatch"
                    )
                    continue
                for move, shard_id in zip(moves, shard_ids):
                    shard = final_shards.get(shard_id)
                    if shard is None:
                        problems.append(
                            f"{expansion_id}:{owner}: missing recursive child shard {shard_id!r}"
                        )
                        continue
                    if tuple(shard.get("prefix") or []) != prefix + (move,):
                        problems.append(
                            f"{expansion_id}:{owner}: recursive child prefix mismatch"
                        )
                    if shard.get("owner") != owner:
                        problems.append(
                            f"{expansion_id}:{owner}: recursive child owner mismatch"
                        )

            stage_by_instance: dict[str, dict[str, Any]] = {}
            for stage in row.get("stages") or []:
                if not isinstance(stage, dict):
                    problems.append(f"{expansion_id}: recursive stage is not an object")
                    continue
                owner = stage.get("owner")
                instance = stage.get("instance")
                if participants.get(owner) != instance:
                    problems.append(
                        f"{expansion_id}: recursive stage owner/instance mismatch"
                    )
                expected_moves = tuple(partition.get(owner) or [])
                if tuple(stage.get("child_moves") or []) != expected_moves:
                    problems.append(
                        f"{expansion_id}:{owner}: recursive stage child set mismatch"
                    )
                if tuple(stage.get("shard_ids") or []) != tuple(
                    child_shards.get(owner) or []
                ):
                    problems.append(
                        f"{expansion_id}:{owner}: recursive stage shard ids mismatch"
                    )
                if stage.get("prefixes") != [
                    list(prefix + (move,)) for move in expected_moves
                ]:
                    problems.append(
                        f"{expansion_id}:{owner}: recursive full prefixes mismatch"
                    )
                try:
                    request = parse_go_request(stage.get("command", ""))
                except Exception as exc:
                    problems.append(
                        f"{expansion_id}:{owner}: cannot parse recursive stage command: {exc}"
                    )
                else:
                    if tuple(request.get("root_moves") or []) != expected_moves:
                        problems.append(
                            f"{expansion_id}:{owner}: recursive searchmoves mismatch"
                        )
                try:
                    stage_position = parse_position_command(
                        stage.get("position_command", ""),
                        variant=parent_variant,
                    )
                except Exception as exc:
                    problems.append(
                        f"{expansion_id}:{owner}: cannot parse recursive stage position: {exc}"
                    )
                else:
                    if oracle_position is not None and stage_position != oracle_position:
                        problems.append(
                            f"{expansion_id}:{owner}: recursive stage position differs from oracle"
                        )
                if isinstance(instance, str):
                    stage_by_instance[instance] = stage

            if (row.get("disposition") or {}).get("expansion") == "completed":
                expected_stage_owners = {
                    owner for owner in owners if partition.get(owner)
                }
                actual_stage_owners = {
                    stage.get("owner")
                    for stage in row.get("stages") or []
                    if isinstance(stage, dict)
                }
                if actual_stage_owners != expected_stage_owners:
                    problems.append(
                        f"{expansion_id}: completed recursive expansion missing stages"
                    )
                if any(
                    stage.get("disposition") != "completed"
                    for stage in row.get("stages") or []
                    if isinstance(stage, dict)
                ):
                    problems.append(
                        f"{expansion_id}: completed recursive expansion has non-completed stage"
                    )

            for record in row.get("streams") or []:
                if not isinstance(record, dict):
                    problems.append(f"{expansion_id}: recursive stream is not an object")
                    continue
                relative = Path(str(record.get("path", "")))
                expected_relative = (
                    Path("recursive")
                    / expansion_id
                    / f"{record.get('instance')}.jsonl"
                )
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or relative != expected_relative
                ):
                    problems.append(
                        f"{expansion_id}:{record.get('instance')}: non-canonical recursive stream path"
                    )
                    continue
                path = refinement_dir / relative
                if not path.is_file():
                    problems.append(
                        f"{expansion_id}: missing recursive stream {record.get('path')!r}"
                    )
                    continue
                if sha256_file(path) != record.get("sha256"):
                    problems.append(
                        f"{expansion_id}:{record.get('instance')}: recursive stream hash mismatch"
                    )
                if path.stat().st_size != record.get("bytes"):
                    problems.append(
                        f"{expansion_id}:{record.get('instance')}: recursive stream size mismatch"
                    )
                stage = stage_by_instance.get(record.get("instance"))
                if stage is None:
                    problems.append(
                        f"{expansion_id}:{record.get('instance')}: recursive stream has no stage"
                    )
                    continue
                try:
                    events = [
                        json.loads(line)
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                except (OSError, json.JSONDecodeError) as exc:
                    problems.append(
                        f"{expansion_id}:{record.get('instance')}: cannot parse recursive stream: {exc}"
                    )
                    continue
                allowed_order = tuple(stage.get("child_moves") or [])
                allowed = set(allowed_order)
                for event in events:
                    if (
                        event.get("search_id") != stage.get("search_id")
                        or event.get("engine_instance") != stage.get("instance")
                    ):
                        problems.append(
                            f"{expansion_id}:{record.get('instance')}: recursive telemetry identity mismatch"
                        )
                        break
                    kind = event.get("event_type")
                    if kind == "search.started":
                        controller = event.get("controller") or {}
                        if controller.get("phase") != "REFINE":
                            problems.append(
                                f"{expansion_id}:{record.get('instance')}: recursive phase is not REFINE"
                            )
                        if controller.get("expansion_id") != expansion_id:
                            problems.append(
                                f"{expansion_id}:{record.get('instance')}: telemetry expansion id mismatch"
                            )
                        if tuple((event.get("request") or {}).get("root_moves") or []) != allowed_order:
                            problems.append(
                                f"{expansion_id}:{record.get('instance')}: recursive telemetry root set mismatch"
                            )
                    elif kind == "candidate.update":
                        candidate = event.get("candidate") or {}
                        move = candidate.get("move")
                        pv = candidate.get("pv") or []
                        if move not in allowed or (pv and pv[0] not in allowed):
                            problems.append(
                                f"{expansion_id}:{record.get('instance')}: recursive candidate escaped region"
                            )
                    elif kind == "search.complete":
                        move = event.get("bestmove")
                        if move is not None and move not in allowed:
                            problems.append(
                                f"{expansion_id}:{record.get('instance')}: recursive bestmove escaped region"
                            )
                        if move != stage.get("bestmove"):
                            problems.append(
                                f"{expansion_id}:{record.get('instance')}: recursive telemetry bestmove mismatch"
                            )

        recursive_source_ids = {
            row.get("source_shard_id")
            for row in expansion_rows
            if isinstance(row, dict)
        }
        for shard_id, shard in final_shards.items():
            if (
                isinstance(shard, dict)
                and shard.get("state") == "retired"
                and int(shard.get("depth") or 0) >= 2
                and shard_id not in recursive_source_ids
            ):
                problems.append(
                    f"{shard_id}: retired recursive shard has no recorded expansion"
                )

    return problems
