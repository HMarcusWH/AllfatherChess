"""M14-G1 staged VERIFY extension evidence.

The base VERIFY round remains the existing verification-v1 artifact.  This
module records one fresh extension search on the *same managed backend
processes* and the same candidate universe.  The intervention is deliberately
separate from the older whole-run 64-vs-128 value-of-compute experiment.

Nothing here routes compute or grants outward move authority.  The coordinator
may execute this research intervention only when it is explicitly configured,
resource-authorized, and still before the anchor decision boundary.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.search_request import parse_go_request
from controller.replay import ReplayError, TelemetryStreamWriter, load_manifest, sha256_file
from controller.verification import (
    VerificationError,
    load_verification_manifest,
    verify_verification_integrity,
)


STAGED_VERIFICATION_SCHEMA_VERSION = 1
STAGED_VERIFY_INTERVENTION = "same_process_staged_verify_v1"
OWNER_ORDER = ("stockfish", "reckless", "lc0")


class StagedVerificationError(RuntimeError):
    """Raised when staged VERIFY evidence cannot preserve its contract."""


@dataclass
class StagedVerificationStage:
    owner: str
    instance: str
    family: str
    search_id: str
    command: str
    candidate_roots: tuple[str, ...]
    dispatched_ms: float
    dispatch_order: int
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


@dataclass
class StagedVerificationRun:
    """One base-VERIFY-linked extension round."""

    run_dir: Path
    source_run_id: str
    generation: int
    position_id: str
    verification_id: str
    candidate_roots: tuple[str, ...]
    nominees_by_owner: dict[str, str]
    participants: dict[str, str]
    base_dispatch_limit: dict[str, object]
    extension_dispatch_limit: dict[str, object]
    intervention: str = STAGED_VERIFY_INTERVENTION
    _streams: dict[str, TelemetryStreamWriter] = field(default_factory=dict)
    _mapped_instances: set[str] = field(default_factory=set)
    _stages: dict[str, StagedVerificationStage] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _dispatch_counter: int = 0
    _completion_counter: int = 0
    disposition: str = "running"
    stop_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    finalized: bool = False

    def __post_init__(self) -> None:
        if self.intervention != STAGED_VERIFY_INTERVENTION:
            raise StagedVerificationError(
                f"unsupported staged VERIFY intervention: {self.intervention!r}"
            )
        if set(self.participants) != set(OWNER_ORDER):
            raise StagedVerificationError(
                "staged VERIFY participants must be exactly the three solver owners"
            )
        if len(self.candidate_roots) != 3 or len(set(self.candidate_roots)) != 3:
            raise StagedVerificationError(
                "staged VERIFY requires exactly three distinct base candidates"
            )
        if set(self.nominees_by_owner) != set(OWNER_ORDER):
            raise StagedVerificationError(
                "staged VERIFY requires one base nominee per solver owner"
            )
        base_nodes = self.base_dispatch_limit.get("nodes")
        ext_nodes = self.extension_dispatch_limit.get("nodes")
        if (
            isinstance(base_nodes, bool)
            or not isinstance(base_nodes, int)
            or base_nodes <= 0
            or isinstance(ext_nodes, bool)
            or not isinstance(ext_nodes, int)
            or ext_nodes <= base_nodes
        ):
            raise StagedVerificationError(
                "staged VERIFY requires positive extension nodes greater than base nodes"
            )

    @property
    def artifact_dir(self) -> Path:
        return self.run_dir / "staged_verification"

    def register_stream(self, writer: TelemetryStreamWriter) -> None:
        with self._lock:
            if writer.instance in self._streams:
                raise StagedVerificationError(
                    f"staged VERIFY stream already registered for {writer.instance!r}"
                )
            self._streams[writer.instance] = writer

    def activate_stream(self, instance: str) -> None:
        with self._lock:
            if instance not in self._streams:
                raise StagedVerificationError(
                    f"no staged VERIFY stream for {instance!r}"
                )
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
    ) -> StagedVerificationStage:
        with self._lock:
            if owner in self._stages:
                raise StagedVerificationError(
                    f"staged VERIFY owner {owner!r} dispatched twice"
                )
            if self.participants.get(owner) != instance:
                raise StagedVerificationError(
                    f"staged VERIFY owner {owner!r} mapped to unexpected instance {instance!r}"
                )
            self._dispatch_counter += 1
            stage = StagedVerificationStage(
                owner=owner,
                instance=instance,
                family=family,
                search_id=search_id,
                command=command,
                candidate_roots=self.candidate_roots,
                dispatched_ms=dispatched_ms,
                dispatch_order=self._dispatch_counter,
            )
            self._stages[owner] = stage
            return stage

    def record_completion(
        self,
        stage: StagedVerificationStage,
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

    def stage_for_owner(self, owner: str) -> StagedVerificationStage | None:
        with self._lock:
            return self._stages.get(owner)

    def stage_for_instance(self, instance: str) -> StagedVerificationStage | None:
        with self._lock:
            return next(
                (stage for stage in self._stages.values() if stage.instance == instance),
                None,
            )

    def stages(self) -> tuple[StagedVerificationStage, ...]:
        with self._lock:
            return tuple(
                sorted(self._stages.values(), key=lambda stage: stage.dispatch_order)
            )

    def active_stages(self) -> tuple[StagedVerificationStage, ...]:
        return tuple(stage for stage in self.stages() if not stage.done.is_set())

    def note(self, message: str) -> None:
        with self._lock:
            if len(self.notes) < 64:
                self.notes.append(message)

    def set_disposition(self, disposition: str, reason: str | None = None) -> None:
        with self._lock:
            self.disposition = disposition
            self.stop_reason = reason

    def finalize(
        self,
        *,
        source_manifest_sha256: str,
        verification_manifest_sha256: str,
    ) -> dict[str, Any]:
        with self._lock:
            if self.finalized:
                return load_staged_verification_manifest(self.run_dir)
            self.finalized = True
            streams = list(self._streams.values())
            stages = list(self._stages.values())

        for stage in stages:
            if stage.disposition == "running":
                stage.disposition = "unresolved"
                stage.failure = stage.failure or "staged VERIFY finalized before completion"
                stage.done.set()
        for writer in streams:
            writer.close()

        stream_records = [writer.snapshot() for writer in streams]
        stream_records.sort(key=lambda item: item["instance"])
        stages.sort(key=lambda stage: stage.dispatch_order)

        core = {
            "schema_version": STAGED_VERIFICATION_SCHEMA_VERSION,
            "intervention": self.intervention,
            "source": {
                "run_id": self.source_run_id,
                "manifest_sha256": source_manifest_sha256,
                "verification_id": self.verification_id,
                "verification_manifest_sha256": verification_manifest_sha256,
            },
            "generation": self.generation,
            "position_id": self.position_id,
            "nomination": {
                "nominees_by_owner": dict(self.nominees_by_owner),
                "candidate_roots": list(self.candidate_roots),
            },
            "participants": dict(self.participants),
            "rounds": {
                "base": {
                    "artifact": "verification/manifest.json",
                    "dispatch_limit": dict(self.base_dispatch_limit),
                },
                "extension": {
                    "artifact": "staged_verification/manifest.json",
                    "dispatch_limit": dict(self.extension_dispatch_limit),
                },
            },
            "stages": [stage.snapshot() for stage in stages],
            "streams": stream_records,
            "disposition": {
                "run": self.disposition,
                "stop_reason": self.stop_reason,
            },
            "notes": list(self.notes),
            "authority": {
                "routing": False,
                "outward_move": False,
            },
        }
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        target = self.artifact_dir / "manifest.json"
        target.write_text(
            json.dumps(core, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return core


def load_staged_verification_manifest(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "staged_verification" / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagedVerificationError(
            f"cannot load staged VERIFY manifest {path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise StagedVerificationError("staged VERIFY manifest root must be an object")
    if manifest.get("schema_version") != STAGED_VERIFICATION_SCHEMA_VERSION:
        raise StagedVerificationError(
            "unsupported staged VERIFY manifest schema_version: "
            f"{manifest.get('schema_version')!r}"
        )
    if manifest.get("intervention") != STAGED_VERIFY_INTERVENTION:
        raise StagedVerificationError(
            f"unsupported staged VERIFY intervention: {manifest.get('intervention')!r}"
        )
    return manifest


def verify_staged_verification_integrity(run_dir: Path | str) -> list[str]:
    """Verify the extension against its parent and base VERIFY artifact."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    try:
        parent = load_manifest(run_dir)
        base = load_verification_manifest(run_dir)
        manifest = load_staged_verification_manifest(run_dir)
    except (ReplayError, VerificationError, StagedVerificationError) as exc:
        return [str(exc)]

    base_problems = verify_verification_integrity(run_dir)
    if base_problems:
        problems.append(f"base VERIFY integrity failed: {base_problems}")

    source = manifest.get("source") or {}
    if source.get("run_id") != parent.get("run_id"):
        problems.append("staged VERIFY source run_id does not match parent replay")
    parent_path = run_dir / "manifest.json"
    base_path = run_dir / "verification" / "manifest.json"
    if parent_path.is_file() and source.get("manifest_sha256") != sha256_file(parent_path):
        problems.append("staged VERIFY parent manifest hash mismatch")
    if base_path.is_file() and source.get("verification_manifest_sha256") != sha256_file(base_path):
        problems.append("staged VERIFY base verification manifest hash mismatch")
    if source.get("verification_id") != base.get("verification_id"):
        problems.append("staged VERIFY verification_id differs from base VERIFY")

    if manifest.get("generation") != parent.get("generation"):
        problems.append("staged VERIFY generation does not match parent replay")
    if manifest.get("position_id") != (parent.get("position") or {}).get("position_id"):
        problems.append("staged VERIFY position_id does not match parent replay")

    base_candidates = tuple((base.get("nomination") or {}).get("candidate_roots", ()))
    candidates = tuple((manifest.get("nomination") or {}).get("candidate_roots", ()))
    if candidates != base_candidates:
        problems.append("staged VERIFY candidate universe differs from base VERIFY")
    candidate_set = set(candidates)

    base_participants = base.get("participants") or {}
    participants = manifest.get("participants") or {}
    if participants != base_participants:
        problems.append("staged VERIFY participants differ from base VERIFY")
    if set(participants) != set(OWNER_ORDER):
        problems.append("staged VERIFY participants are not exactly the three solver owners")

    rounds = manifest.get("rounds") or {}
    base_limit = (rounds.get("base") or {}).get("dispatch_limit") or {}
    extension_limit = (rounds.get("extension") or {}).get("dispatch_limit") or {}
    if base_limit != (base.get("dispatch_limit") or {}):
        problems.append("staged VERIFY base dispatch limit differs from base VERIFY")
    base_nodes = base_limit.get("nodes")
    ext_nodes = extension_limit.get("nodes")
    if (
        isinstance(base_nodes, bool)
        or not isinstance(base_nodes, int)
        or isinstance(ext_nodes, bool)
        or not isinstance(ext_nodes, int)
        or ext_nodes <= base_nodes
    ):
        problems.append("staged VERIFY extension budget is not strictly above base")

    disposition = (manifest.get("disposition") or {}).get("run")
    stages = manifest.get("stages") or []
    if disposition == "completed" and len(stages) != 3:
        problems.append("completed staged VERIFY does not contain exactly three stages")

    seen_owners: set[str] = set()
    seen_search_ids: set[str] = set()
    for stage in stages:
        if not isinstance(stage, dict):
            problems.append("staged VERIFY stage is not an object")
            continue
        owner = stage.get("owner")
        if owner in seen_owners:
            problems.append(f"staged VERIFY owner {owner!r} appears twice")
        seen_owners.add(owner)
        if participants.get(owner) != stage.get("instance"):
            problems.append(f"staged VERIFY owner {owner!r} changed process identity")
        search_id = stage.get("search_id")
        if search_id in seen_search_ids:
            problems.append(f"duplicate staged VERIFY search_id: {search_id!r}")
        seen_search_ids.add(search_id)
        if tuple(stage.get("candidate_roots") or ()) != candidates:
            problems.append(f"{owner}: staged VERIFY stage changed candidate roots")
        try:
            request = parse_go_request(str(stage.get("command") or ""))
        except Exception as exc:
            problems.append(f"{owner}: cannot parse staged VERIFY command: {exc}")
            continue
        if tuple(request.get("root_moves") or ()) != candidates:
            problems.append(f"{owner}: staged VERIFY command changed candidate roots")
        limits = request.get("limits") or []
        if (
            len(limits) != 1
            or limits[0].get("name") != "nodes"
            or limits[0].get("value") != ext_nodes
        ):
            problems.append(f"{owner}: staged VERIFY command changed extension budget")

    artifact_dir = run_dir / "staged_verification"
    streams = manifest.get("streams") or []
    if disposition == "completed" and len(streams) != 3:
        problems.append("completed staged VERIFY does not contain three streams")
    for record in streams:
        if not isinstance(record, dict):
            problems.append("staged VERIFY stream record is not an object")
            continue
        path = artifact_dir / str(record.get("path") or "")
        if not path.is_file():
            problems.append(f"missing staged VERIFY stream file: {record.get('path')}")
            continue
        if sha256_file(path) != record.get("sha256"):
            problems.append(f"staged VERIFY stream hash mismatch for {record.get('path')}")
        if path.stat().st_size != record.get("bytes"):
            problems.append(f"staged VERIFY stream size mismatch for {record.get('path')}")
        try:
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"cannot parse staged VERIFY stream {record.get('path')}: {exc}")
            continue
        for event in events:
            kind = event.get("event_type")
            if kind == "search.started":
                controller = event.get("controller") or {}
                if controller.get("phase") != "VERIFY_EXTENSION":
                    problems.append(
                        f"{record.get('instance')}: staged VERIFY phase mismatch"
                    )
                roots = tuple((event.get("request") or {}).get("root_moves") or ())
                if roots != candidates:
                    problems.append(
                        f"{record.get('instance')}: staged VERIFY start changed roots"
                    )
            elif kind == "candidate.update":
                candidate = event.get("candidate") or {}
                move = candidate.get("move")
                pv = candidate.get("pv") or []
                if move not in candidate_set:
                    problems.append(
                        f"{record.get('instance')}: staged candidate escaped roots"
                    )
                if pv and pv[0] not in candidate_set:
                    problems.append(
                        f"{record.get('instance')}: staged PV escaped roots"
                    )
            elif kind == "search.complete":
                move = event.get("bestmove")
                if move is not None and move not in candidate_set:
                    problems.append(
                        f"{record.get('instance')}: staged bestmove escaped roots"
                    )

    return problems
