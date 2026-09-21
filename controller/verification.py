"""Explicit common-support VERIFY evidence.

This module owns the VERIFY data model, deterministic nomination, raw artifact
format, and integrity checks. Process lifecycle and UCI dispatch remain owned by
:mod:`controller.shadow`, which already owns the generation barrier, the single
telemetry observer, and the lock shared with anchor completion.

VERIFY v1 is instrumentation only. It never chooses or changes the outward move.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.search_request import parse_go_request
from controller.replay import ReplayError, TelemetryStreamWriter, load_manifest, sha256_file
from controller.runtime import VerificationSettings


VERIFICATION_SCHEMA_VERSION = 1
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class VerificationError(RuntimeError):
    """Raised when a VERIFY experiment cannot preserve its contract."""


@dataclass(frozen=True)
class VerificationPlan:
    verification_id: str
    generation: int
    source_run_id: str
    position_id: str
    owners: tuple[str, ...]
    candidate_roots: tuple[str, ...]
    nominees_by_owner: dict[str, str]
    participants: dict[str, str]
    dispatch_limit: dict[str, object]
    nomination_method: str


@dataclass
class VerificationStage:
    owner: str
    instance: str
    family: str
    search_id: str
    command: str
    candidate_roots: tuple[str, ...]
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
            "owner": self.owner,
            "instance": self.instance,
            "family": self.family,
            "search_id": self.search_id,
            "command": self.command,
            "candidate_roots": list(self.candidate_roots),
            "dispatch_order": self.dispatch_order,
            "dispatched_ms": round(self.dispatched_ms, 3),
            "completed_ms": None if self.completed_ms is None else round(self.completed_ms, 3),
            "completion_order": self.completion_order,
            "disposition": self.disposition,
            "bestmove": self.bestmove,
            "stop_reason": self.stop_reason,
            "failure": self.failure,
        }


def build_verification_plan(
    *,
    generation: int,
    source_run_id: str,
    position_id: str,
    settings: VerificationSettings,
    owners: tuple[str, ...],
    instance_by_owner: dict[str, str],
    owner_bestmoves: dict[str, str | None],
    owner_roots: dict[str, tuple[str, ...]],
) -> VerificationPlan:
    """Build the one deterministic VERIFY-v1 candidate set.

    Because EXPLORE ownership is pairwise disjoint, three valid completed
    bestmoves must also be three distinct roots. A duplicate is therefore an
    invariant failure, not consensus.
    """

    if not settings.enabled:
        raise VerificationError("verification is disabled")
    if settings.nomination_method != "owner_bestmove_union_v1":
        raise VerificationError(
            f"unsupported nomination method: {settings.nomination_method!r}"
        )
    if len(owners) != 3:
        raise VerificationError("verification v1 requires exactly three exploration owners")

    nominees: dict[str, str] = {}
    for owner in owners:
        move = owner_bestmoves.get(owner)
        if not isinstance(move, str):
            raise VerificationError(f"owner {owner} has no completed EXPLORE bestmove")
        move = move.lower()
        if not _MOVE_RE.fullmatch(move):
            raise VerificationError(f"owner {owner} produced a non-canonical nominee: {move!r}")
        roots = owner_roots.get(owner, ())
        if move not in roots:
            raise VerificationError(
                f"owner {owner} nominee {move} is outside its EXPLORE roots {list(roots)}"
            )
        nominees[owner] = move

    candidates = tuple(nominees[owner] for owner in owners)
    if len(set(candidates)) != len(candidates):
        raise VerificationError(
            "EXPLORE nominees are not distinct despite pairwise-disjoint ownership"
        )

    participants = {owner: instance_by_owner[owner] for owner in owners}
    # The source run id already contains entropy and generation identity; the
    # suffix is human-readable while remaining unique inside that run.
    verification_id = f"{source_run_id}:verify-v1"

    return VerificationPlan(
        verification_id=verification_id,
        generation=generation,
        source_run_id=source_run_id,
        position_id=position_id,
        owners=tuple(owners),
        candidate_roots=candidates,
        nominees_by_owner=nominees,
        participants=participants,
        dispatch_limit=dict(settings.dispatch_limit),
        nomination_method=settings.nomination_method,
    )


@dataclass
class VerificationRun:
    """Raw evidence container for one deliberate overlap experiment."""

    plan: VerificationPlan
    run_dir: Path
    _streams: dict[str, TelemetryStreamWriter] = field(default_factory=dict)
    _mapped_instances: set[str] = field(default_factory=set)
    _stages: dict[str, VerificationStage] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _dispatch_counter: int = 0
    _completion_counter: int = 0
    disposition: str = "running"
    stop_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    finalized: bool = False

    @property
    def verification_dir(self) -> Path:
        return self.run_dir / "verification"

    def register_stream(self, writer: TelemetryStreamWriter) -> None:
        with self._lock:
            if writer.instance in self._streams:
                raise VerificationError(
                    f"verification stream already registered for {writer.instance!r}"
                )
            self._streams[writer.instance] = writer

    def activate_stream(self, instance: str) -> None:
        with self._lock:
            if instance not in self._streams:
                raise VerificationError(f"no verification stream for {instance!r}")
            self._mapped_instances.add(instance)

    def observation_stream(self, instance: str) -> TelemetryStreamWriter | None:
        with self._lock:
            if instance not in self._mapped_instances:
                return None
            return self._streams.get(instance)

    def stream(self, instance: str) -> TelemetryStreamWriter | None:
        with self._lock:
            return self._streams.get(instance)

    def record_dispatch(
        self,
        *,
        owner: str,
        instance: str,
        family: str,
        search_id: str,
        command: str,
        dispatched_ms: float,
    ) -> VerificationStage:
        with self._lock:
            if owner in self._stages:
                raise VerificationError(f"verification owner {owner!r} dispatched twice")
            self._dispatch_counter += 1
            stage = VerificationStage(
                owner=owner,
                instance=instance,
                family=family,
                search_id=search_id,
                command=command,
                candidate_roots=self.plan.candidate_roots,
                dispatch_order=self._dispatch_counter,
                dispatched_ms=dispatched_ms,
            )
            self._stages[owner] = stage
            return stage

    def record_completion(
        self,
        stage: VerificationStage,
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
                return
            self._completion_counter += 1
            stage.completed_ms = completed_ms
            stage.completion_order = self._completion_counter
            stage.disposition = disposition
            stage.bestmove = bestmove
            stage.stop_reason = stop_reason
            stage.failure = failure
            stage.done.set()

    def stage_for_owner(self, owner: str) -> VerificationStage | None:
        with self._lock:
            return self._stages.get(owner)

    def stage_for_instance(self, instance: str) -> VerificationStage | None:
        with self._lock:
            return next(
                (stage for stage in self._stages.values() if stage.instance == instance),
                None,
            )

    def stages(self) -> tuple[VerificationStage, ...]:
        with self._lock:
            return tuple(sorted(self._stages.values(), key=lambda stage: stage.dispatch_order))

    def active_stages(self) -> tuple[VerificationStage, ...]:
        return tuple(stage for stage in self.stages() if not stage.done.is_set())

    def note(self, message: str) -> None:
        with self._lock:
            if len(self.notes) < 64:
                self.notes.append(message)

    def set_disposition(self, disposition: str, reason: str | None = None) -> None:
        with self._lock:
            self.disposition = disposition
            self.stop_reason = reason

    def finalize(self, *, source_manifest_sha256: str) -> dict[str, Any]:
        with self._lock:
            if self.finalized:
                return load_verification_manifest(self.run_dir)
            self.finalized = True
            streams = list(self._streams.values())
            stages = list(self._stages.values())

        for stage in stages:
            if stage.disposition == "running":
                stage.disposition = "unresolved"
                stage.failure = stage.failure or "verification finalized before completion"
                stage.done.set()

        for writer in streams:
            writer.close()

        stream_records = [writer.snapshot() for writer in streams]
        stream_records.sort(key=lambda item: item["instance"])
        stages.sort(key=lambda stage: stage.dispatch_order)

        manifest = {
            "schema_version": VERIFICATION_SCHEMA_VERSION,
            "verification_id": self.plan.verification_id,
            "source": {
                "run_id": self.plan.source_run_id,
                "manifest_sha256": source_manifest_sha256,
            },
            "generation": self.plan.generation,
            "position_id": self.plan.position_id,
            "nomination": {
                "method": self.plan.nomination_method,
                "nominees_by_owner": dict(self.plan.nominees_by_owner),
                "candidate_roots": list(self.plan.candidate_roots),
            },
            "participants": dict(self.plan.participants),
            "dispatch_limit": dict(self.plan.dispatch_limit),
            "stages": [stage.snapshot() for stage in stages],
            "streams": stream_records,
            "disposition": {
                "run": self.disposition,
                "stop_reason": self.stop_reason,
            },
            "notes": list(self.notes),
        }

        self.verification_dir.mkdir(parents=True, exist_ok=True)
        (self.verification_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


def load_verification_manifest(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "verification" / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot load verification manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise VerificationError("verification manifest root must be an object")
    version = manifest.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != VERIFICATION_SCHEMA_VERSION
    ):
        raise VerificationError(
            f"unsupported verification manifest schema_version: {version!r}"
        )
    return manifest


def verify_verification_integrity(run_dir: Path | str) -> list[str]:
    """Verify provenance, stream hashes, and declared common-root containment."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    try:
        parent = load_manifest(run_dir)
        manifest = load_verification_manifest(run_dir)
    except (VerificationError, ReplayError) as exc:
        return [str(exc)]

    source = manifest.get("source", {})
    if source.get("run_id") != parent.get("run_id"):
        problems.append("verification source run_id does not match parent replay")
    parent_path = run_dir / "manifest.json"
    if parent_path.is_file():
        actual_parent_hash = sha256_file(parent_path)
        if source.get("manifest_sha256") != actual_parent_hash:
            problems.append(
                "verification source manifest hash mismatch: "
                f"manifest={source.get('manifest_sha256')}, actual={actual_parent_hash}"
            )

    if manifest.get("generation") != parent.get("generation"):
        problems.append("verification generation does not match parent replay")
    if manifest.get("position_id") != (parent.get("position") or {}).get("position_id"):
        problems.append("verification position_id does not match parent replay")

    candidates = tuple(manifest.get("nomination", {}).get("candidate_roots", []))
    candidate_set = set(candidates)
    if len(candidates) != 3 or len(candidate_set) != 3:
        problems.append("verification candidate set is not exactly three distinct roots")

    participants = manifest.get("participants")
    if not isinstance(participants, dict) or set(participants) != {
        "stockfish", "reckless", "lc0"
    }:
        problems.append("verification participants are not exactly the three solver owners")
        participants = {}

    streams = manifest.get("streams", [])
    stream_instances = [record.get("instance") for record in streams if isinstance(record, dict)]
    expected_instances = list(participants.values())
    if len(stream_instances) != 3 or set(stream_instances) != set(expected_instances):
        problems.append(
            "verification stream instances do not match the three declared participants"
        )

    stages = manifest.get("stages", [])
    if len(stages) != 3:
        problems.append("verification manifest does not contain exactly three stages")
    seen_stage_owners: set[str] = set()
    seen_search_ids: set[str] = set()
    for stage in stages:
        if not isinstance(stage, dict):
            problems.append("verification stage is not an object")
            continue
        owner = stage.get("owner")
        instance = stage.get("instance")
        search_id = stage.get("search_id")
        if owner in seen_stage_owners:
            problems.append(f"verification owner {owner!r} appears in multiple stages")
        seen_stage_owners.add(owner)
        if participants.get(owner) != instance:
            problems.append(
                f"verification stage {owner!r} uses instance {instance!r}, "
                f"expected {participants.get(owner)!r}"
            )
        if search_id in seen_search_ids:
            problems.append(f"duplicate verification search_id: {search_id!r}")
        seen_search_ids.add(search_id)

    verification_dir = run_dir / "verification"
    for record in streams:
        path = verification_dir / record.get("path", "")
        if not path.is_file():
            problems.append(f"missing verification stream file: {record.get('path')}")
            continue
        if sha256_file(path) != record.get("sha256"):
            problems.append(f"verification stream hash mismatch for {record.get('path')}")
        if path.stat().st_size != record.get("bytes"):
            problems.append(f"verification stream size mismatch for {record.get('path')}")

        try:
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"cannot parse verification stream {record.get('path')}: {exc}")
            continue
        for event in events:
            kind = event.get("event_type")
            if kind == "search.started":
                controller = event.get("controller") or {}
                if controller.get("phase") != "VERIFY":
                    problems.append(
                        f"{record.get('instance')}: verification stream phase is not VERIFY"
                    )
                roots = tuple((event.get("request") or {}).get("root_moves", []))
                if roots != candidates:
                    problems.append(
                        f"{record.get('instance')}: verification root request differs "
                        "from the declared common candidate set"
                    )
            elif kind == "candidate.update":
                candidate = event.get("candidate") or {}
                move = candidate.get("move")
                pv = candidate.get("pv") or []
                if move not in candidate_set:
                    problems.append(
                        f"{record.get('instance')}: candidate {move!r} escaped VERIFY roots"
                    )
                if pv and pv[0] not in candidate_set:
                    problems.append(
                        f"{record.get('instance')}: PV head {pv[0]!r} escaped VERIFY roots"
                    )
            elif kind == "search.complete":
                move = event.get("bestmove")
                if move is not None and move not in candidate_set:
                    problems.append(
                        f"{record.get('instance')}: bestmove {move!r} escaped VERIFY roots"
                    )

    for stage in stages:
        if tuple(stage.get("candidate_roots", [])) != candidates:
            problems.append(
                f"{stage.get('instance')}: stage candidate roots differ from VERIFY plan"
            )
        request = parse_go_request(stage.get("command", ""))
        if tuple(request.get("root_moves", [])) != candidates:
            problems.append(
                f"{stage.get('instance')}: stage command differs from VERIFY plan"
            )

    return problems
