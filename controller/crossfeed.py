"""Typed cross-feed evidence derived from existing VERIFY / REFINE work.

Cross-feed v1 is deliberately not a new search phase. It composes evidence
that the controller already paid for:

    EXPLORE -> common-support VERIFY -> optional REFINE -> CrossFeedView

The view is decision-inert in this milestone. It cannot dispatch an engine,
change search-space ownership, convert native engine scores onto a common scale,
or select the outward move.

CrossFeedView is an immutable in-memory view built from live completed
telemetry. crossfeed/manifest.json is a sealed derived artifact written only
after the parent / VERIFY / REFINE manifests exist and hash-binds those exact
source artifacts and stream identities.

No numeric cross-engine score delta exists in this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from controller.refinement import (
    RefinementRun,
    load_refinement_manifest,
    verify_refinement_integrity,
)
from controller.replay import (
    ReplayError,
    load_manifest,
    sha256_file,
    verify_bundle_integrity,
)
from controller.verification import (
    VerificationError,
    VerificationRun,
    load_verification_manifest,
    verify_verification_integrity,
)


CROSSFEED_SCHEMA_VERSION = 1
CROSSFEED_POLICY = "typed_verify_refine_v1"
_OWNER_ORDER = ("stockfish", "reckless", "lc0")
_PHASE_ORDER = {"VERIFY": 0, "REFINE": 1}
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_FORBIDDEN_DERIVED_KEYS = {
    "score_delta",
    "centipawn_delta",
    "cross_engine_margin",
    "universal_score",
    "combined_score",
    "weighted_score",
    "winner",
    "correct_move",
}


class CrossFeedError(RuntimeError):
    """Raised when typed cross-feed evidence cannot preserve its contract."""


def _finite_number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CrossFeedError(f"{label} must be numeric")
    if not math.isfinite(float(value)):
        raise CrossFeedError(f"{label} must be finite")
    return value


def _canonical_move(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CrossFeedError(f"{label} must be a UCI move string")
    move = value.lower()
    if not _MOVE_RE.fullmatch(move):
        raise CrossFeedError(f"{label} is not canonical UCI: {value!r}")
    return move


@dataclass(frozen=True)
class NativeEvaluation:
    """One source-native evaluation. Semantics are never converted."""

    kind: str
    semantics: str
    bound: str | None = None
    perspective: str | None = None
    value: int | float | None = None
    win: int | float | None = None
    draw: int | float | None = None
    loss: int | float | None = None
    scale: int | float | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "NativeEvaluation":
        if not isinstance(payload, dict):
            raise CrossFeedError("candidate evaluation must be an object")
        kind = payload.get("kind")
        semantics = payload.get("semantics")
        if not isinstance(kind, str) or not kind:
            raise CrossFeedError("candidate evaluation kind must be non-empty")
        if not isinstance(semantics, str) or not semantics:
            raise CrossFeedError("candidate evaluation semantics must be non-empty")
        numeric: dict[str, int | float | None] = {}
        for key in ("value", "win", "draw", "loss", "scale"):
            raw = payload.get(key)
            numeric[key] = None if raw is None else _finite_number(raw, f"evaluation.{key}")
        return cls(
            kind=kind,
            semantics=semantics,
            bound=payload.get("bound") if isinstance(payload.get("bound"), str) else None,
            perspective=(
                payload.get("perspective")
                if isinstance(payload.get("perspective"), str)
                else None
            ),
            **numeric,
        )

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "semantics": self.semantics,
        }
        for key in ("bound", "perspective", "value", "win", "draw", "loss", "scale"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


@dataclass(frozen=True)
class NativeWork:
    """One source-native work counter. Counters are not cross-family additive."""

    value: int | float
    unit: str
    semantics: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "NativeWork":
        if not isinstance(payload, dict):
            raise CrossFeedError("candidate work entry must be an object")
        unit = payload.get("unit")
        semantics = payload.get("semantics")
        if not isinstance(unit, str) or not unit:
            raise CrossFeedError("candidate work unit must be non-empty")
        if not isinstance(semantics, str) or not semantics:
            raise CrossFeedError("candidate work semantics must be non-empty")
        return cls(
            value=_finite_number(payload.get("value"), "work.value"),
            unit=unit,
            semantics=semantics,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "semantics": self.semantics,
        }


@dataclass(frozen=True)
class CandidateHint:
    """One latest source observation about one decision-root candidate."""

    move: str
    observed_move: str
    source_owner: str
    source_instance: str
    source_family: str
    source_phase: str
    source_rank: int | None
    pv_prefix: tuple[str, ...]
    evaluations: tuple[NativeEvaluation, ...]
    work: tuple[NativeWork, ...]
    search_id: str
    source_run_id: str
    sequence: int
    observed_ms: float
    stage_disposition: str

    def __post_init__(self) -> None:
        _canonical_move(self.move, "hint.move")
        _canonical_move(self.observed_move, "hint.observed_move")
        if self.source_owner not in _OWNER_ORDER:
            raise CrossFeedError(f"unknown source owner: {self.source_owner!r}")
        if self.source_family != self.source_owner:
            raise CrossFeedError(
                f"source family {self.source_family!r} does not match owner "
                f"{self.source_owner!r}"
            )
        if self.source_phase not in _PHASE_ORDER:
            raise CrossFeedError(f"unsupported source phase: {self.source_phase!r}")
        if not isinstance(self.source_instance, str) or not self.source_instance:
            raise CrossFeedError("source_instance must be non-empty")
        if not isinstance(self.search_id, str) or not self.search_id:
            raise CrossFeedError("search_id must be non-empty")
        if not isinstance(self.source_run_id, str) or not self.source_run_id:
            raise CrossFeedError("source_run_id must be non-empty")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise CrossFeedError("sequence must be a non-negative integer")
        _finite_number(self.observed_ms, "observed_ms")
        if self.source_rank is not None and (
            isinstance(self.source_rank, bool)
            or not isinstance(self.source_rank, int)
            or self.source_rank < 1
        ):
            raise CrossFeedError("source_rank must be a positive integer when present")
        for index, move in enumerate(self.pv_prefix):
            _canonical_move(move, f"pv_prefix[{index}]")

    def as_dict(self) -> dict[str, Any]:
        return {
            "move": self.move,
            "observed_move": self.observed_move,
            "source_owner": self.source_owner,
            "source_instance": self.source_instance,
            "source_family": self.source_family,
            "source_phase": self.source_phase,
            "source_rank": self.source_rank,
            "pv_prefix": list(self.pv_prefix),
            "evaluations": [item.as_dict() for item in self.evaluations],
            "work": [item.as_dict() for item in self.work],
            "search_id": self.search_id,
            "source_run_id": self.source_run_id,
            "sequence": self.sequence,
            "observed_ms": self.observed_ms,
            "stage_disposition": self.stage_disposition,
        }


@dataclass(frozen=True)
class CrossFeedCandidate:
    """One decision-root candidate with all retained provenance-bearing hints."""

    move: str
    original_owner: str
    hints: tuple[CandidateHint, ...]

    def __post_init__(self) -> None:
        _canonical_move(self.move, "candidate.move")
        if self.original_owner not in _OWNER_ORDER:
            raise CrossFeedError(f"unknown original owner: {self.original_owner!r}")
        if any(hint.move != self.move for hint in self.hints):
            raise CrossFeedError("candidate contains a hint for a different move")

    def as_dict(self) -> dict[str, Any]:
        return {
            "move": self.move,
            "original_owner": self.original_owner,
            "hints": [hint.as_dict() for hint in self.hints],
        }


@dataclass(frozen=True)
class CrossFeedView:
    """Decision-inert in-memory composition of existing specialist evidence."""

    run_id: str
    generation: int
    position_id: str
    policy: str
    verification_id: str
    verification_disposition: str
    verification_complete: bool
    refinement_id: str | None
    refinement_disposition: str
    candidates: tuple[CrossFeedCandidate, ...]
    evidence_faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.policy != CROSSFEED_POLICY:
            raise CrossFeedError(f"unsupported cross-feed policy: {self.policy!r}")
        if not isinstance(self.run_id, str) or not self.run_id:
            raise CrossFeedError("run_id must be non-empty")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise CrossFeedError("generation must be an integer")
        if not isinstance(self.position_id, str) or not self.position_id:
            raise CrossFeedError("position_id must be non-empty")
        moves = [candidate.move for candidate in self.candidates]
        if len(moves) != len(set(moves)):
            raise CrossFeedError("cross-feed view contains duplicate candidates")

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "policy": self.policy,
            "verification": {
                "verification_id": self.verification_id,
                "disposition": self.verification_disposition,
                "complete": self.verification_complete,
            },
            "refinement": {
                "refinement_id": self.refinement_id,
                "disposition": self.refinement_disposition,
            },
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "evidence_faults": list(self.evidence_faults),
        }


def _hint_sort_key(hint: CandidateHint) -> tuple[Any, ...]:
    return (
        _PHASE_ORDER[hint.source_phase],
        _OWNER_ORDER.index(hint.source_owner),
        hint.source_instance,
        hint.search_id,
        hint.observed_move,
        hint.source_rank if hint.source_rank is not None else 1_000_000,
        hint.sequence,
    )


def _extract_hint(
    *,
    event: dict[str, Any],
    root_move: str,
    source_owner: str,
    source_instance: str,
    source_family: str,
    source_phase: str,
    stage_disposition: str,
    source_run_id: str,
    prefix_root: bool,
) -> CandidateHint | None:
    if event.get("event_type") != "candidate.update":
        return None
    if event.get("engine_instance") != source_instance:
        raise CrossFeedError(
            f"{source_phase} event instance {event.get('engine_instance')!r} "
            f"does not match stage instance {source_instance!r}"
        )
    if event.get("engine") != source_family:
        raise CrossFeedError(
            f"{source_phase} event family {event.get('engine')!r} "
            f"does not match stage family {source_family!r}"
        )
    candidate = event.get("candidate")
    if not isinstance(candidate, dict):
        raise CrossFeedError("candidate.update is missing candidate object")
    observed_move = _canonical_move(candidate.get("move"), "candidate.move")
    pv_raw = candidate.get("pv") or []
    if not isinstance(pv_raw, list):
        raise CrossFeedError("candidate.pv must be an array")
    pv = tuple(_canonical_move(move, "candidate.pv") for move in pv_raw)
    if not pv:
        pv = (observed_move,)
    if pv[0] != observed_move:
        raise CrossFeedError("candidate PV head does not match candidate move")

    decision_move = _canonical_move(root_move, "root_move")
    pv_prefix = ((decision_move,) + pv) if prefix_root else pv
    rank = candidate.get("multipv_index")
    if rank is not None and (
        isinstance(rank, bool) or not isinstance(rank, int) or rank < 1
    ):
        raise CrossFeedError("candidate multipv_index must be a positive integer")

    evaluations_raw = candidate.get("evaluations") or []
    if not isinstance(evaluations_raw, list):
        raise CrossFeedError("candidate evaluations must be an array")
    evaluations = tuple(NativeEvaluation.from_payload(item) for item in evaluations_raw)

    work_raw = event.get("work") or []
    if not isinstance(work_raw, list):
        raise CrossFeedError("candidate work must be an array")
    work = tuple(NativeWork.from_payload(item) for item in work_raw)

    search_id = event.get("search_id")
    sequence = event.get("sequence")
    observed_ms = event.get("observed_ms")
    if not isinstance(search_id, str) or not search_id:
        raise CrossFeedError("candidate event is missing search_id")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise CrossFeedError("candidate event sequence must be a non-negative integer")

    return CandidateHint(
        move=decision_move,
        observed_move=observed_move,
        source_owner=source_owner,
        source_instance=source_instance,
        source_family=source_family,
        source_phase=source_phase,
        source_rank=rank,
        pv_prefix=pv_prefix,
        evaluations=evaluations,
        work=work,
        search_id=search_id,
        source_run_id=source_run_id,
        sequence=sequence,
        observed_ms=float(_finite_number(observed_ms, "observed_ms")),
        stage_disposition=stage_disposition,
    )


def _retain_latest(hints: Iterable[CandidateHint]) -> tuple[CandidateHint, ...]:
    """Keep the latest update per source search + observed move."""

    latest: dict[tuple[str, str, str, str, str], CandidateHint] = {}
    for hint in hints:
        key = (
            hint.source_phase,
            hint.source_owner,
            hint.search_id,
            hint.move,
            hint.observed_move,
        )
        current = latest.get(key)
        if current is None or (hint.sequence, hint.observed_ms) > (
            current.sequence,
            current.observed_ms,
        ):
            latest[key] = hint
    return tuple(sorted(latest.values(), key=_hint_sort_key))


def build_crossfeed_view(
    *,
    run_id: str,
    generation: int,
    position_id: str,
    verification: VerificationRun,
    refinement: RefinementRun | None = None,
) -> CrossFeedView:
    """Build the decision-inert in-memory view from live specialist evidence."""

    plan = verification.plan
    if plan.source_run_id != run_id:
        raise CrossFeedError("VERIFY source run does not match cross-feed run")
    if plan.generation != generation:
        raise CrossFeedError("VERIFY generation does not match cross-feed run")
    if plan.position_id != position_id:
        raise CrossFeedError("VERIFY position does not match cross-feed run")
    if tuple(plan.owners) != _OWNER_ORDER:
        raise CrossFeedError("cross-feed v1 requires the three canonical solver owners")

    candidates = tuple(
        _canonical_move(move, "verification candidate")
        for move in plan.candidate_roots
    )
    if len(candidates) != 3 or len(set(candidates)) != 3:
        raise CrossFeedError("cross-feed v1 requires exactly three distinct VERIFY candidates")

    original_owner_by_move: dict[str, str] = {}
    for owner in plan.owners:
        move = _canonical_move(plan.nominees_by_owner.get(owner), f"nominee[{owner}]")
        if move in original_owner_by_move:
            raise CrossFeedError("VERIFY nominees are not pairwise distinct")
        original_owner_by_move[move] = owner
    if set(original_owner_by_move) != set(candidates):
        raise CrossFeedError("VERIFY candidate set differs from its owner nominees")

    hints: list[CandidateHint] = []
    faults: list[str] = []

    for owner in plan.owners:
        stage = verification.stage_for_owner(owner)
        if stage is None:
            faults.append(f"VERIFY stage missing for {owner}")
            continue
        if stage.family != owner:
            raise CrossFeedError(
                f"VERIFY stage family {stage.family!r} does not match owner {owner!r}"
            )
        if stage.instance != plan.participants.get(owner):
            raise CrossFeedError(f"VERIFY participant mismatch for {owner}")
        stream = verification.stream(stage.instance)
        if stream is None:
            faults.append(f"VERIFY stream missing for {owner}")
            continue
        if stream.evidence_lossy:
            faults.append(f"VERIFY stream lossy for {owner}")
        if stream.tracked_events_truncated:
            faults.append(f"VERIFY live view truncated for {owner}")
        for event in stream.tracked_events():
            if event.get("event_type") != "candidate.update":
                continue
            candidate_payload = event.get("candidate")
            root_move = (
                candidate_payload.get("move")
                if isinstance(candidate_payload, dict)
                else None
            )
            hint = _extract_hint(
                event=event,
                root_move=root_move,
                source_owner=owner,
                source_instance=stage.instance,
                source_family=stage.family,
                source_phase="VERIFY",
                stage_disposition=stage.disposition,
                source_run_id=run_id,
                prefix_root=False,
            )
            if hint.move not in original_owner_by_move:
                raise CrossFeedError(
                    f"VERIFY hint {hint.move} is outside the common candidate set"
                )
            if hint.search_id != stage.search_id:
                raise CrossFeedError(
                    f"VERIFY hint search_id {hint.search_id!r} does not match "
                    f"stage search_id {stage.search_id!r}"
                )
            hints.append(hint)

    refinement_id: str | None = None
    refinement_disposition = "absent"
    if refinement is not None:
        if refinement.plan.source_run_id != run_id:
            raise CrossFeedError("REFINE source run does not match cross-feed run")
        if refinement.plan.source_verification_id != plan.verification_id:
            raise CrossFeedError("REFINE source verification does not match")
        refinement_id = refinement.plan.refinement_id
        refinement_disposition = refinement.disposition

        for target in refinement.targets():
            root_move = _canonical_move(target.target.root_move, "refinement root")
            if root_move not in original_owner_by_move:
                raise CrossFeedError(
                    f"REFINE target {root_move} is outside the VERIFY candidate set"
                )
            for owner in plan.owners:
                stage = target.stages.get(owner)
                if stage is None:
                    continue
                if stage.family != owner:
                    raise CrossFeedError(
                        f"REFINE stage family {stage.family!r} does not match owner {owner!r}"
                    )
                stream = refinement.stream(target.target.target_id, stage.instance)
                if stream is None:
                    faults.append(
                        f"REFINE stream missing for {target.target.target_id}/{owner}"
                    )
                    continue
                if stream.evidence_lossy:
                    faults.append(
                        f"REFINE stream lossy for {target.target.target_id}/{owner}"
                    )
                if stream.tracked_events_truncated:
                    faults.append(
                        f"REFINE live view truncated for {target.target.target_id}/{owner}"
                    )
                for event in stream.tracked_events():
                    hint = _extract_hint(
                        event=event,
                        root_move=root_move,
                        source_owner=owner,
                        source_instance=stage.instance,
                        source_family=stage.family,
                        source_phase="REFINE",
                        stage_disposition=stage.disposition,
                        source_run_id=run_id,
                        prefix_root=True,
                    )
                    if hint is not None:
                        if hint.search_id != stage.search_id:
                            raise CrossFeedError(
                                f"REFINE hint search_id {hint.search_id!r} does not match "
                                f"stage search_id {stage.search_id!r}"
                            )
                        hints.append(hint)

    retained = _retain_latest(hints)
    by_move: dict[str, list[CandidateHint]] = {move: [] for move in candidates}
    for hint in retained:
        by_move[hint.move].append(hint)

    view_candidates = tuple(
        CrossFeedCandidate(
            move=move,
            original_owner=original_owner_by_move[move],
            hints=tuple(sorted(by_move[move], key=_hint_sort_key)),
        )
        for move in candidates
    )

    verification_complete = (
        verification.disposition == "completed"
        and not any(fault.startswith("VERIFY") for fault in faults)
        and len(verification.stages()) == 3
        and all(stage.disposition == "completed" for stage in verification.stages())
    )

    return CrossFeedView(
        run_id=run_id,
        generation=generation,
        position_id=position_id,
        policy=CROSSFEED_POLICY,
        verification_id=plan.verification_id,
        verification_disposition=verification.disposition,
        verification_complete=verification_complete,
        refinement_id=refinement_id,
        refinement_disposition=refinement_disposition,
        candidates=view_candidates,
        evidence_faults=tuple(sorted(set(faults))),
    )


class _SealedStream:
    """Read-only stream proxy used to replay a sealed cross-feed derivation."""

    def __init__(self, instance: str, record: dict[str, Any], events: list[dict[str, Any]]):
        self.instance = instance
        self._events = events
        self.evidence_lossy = bool(record.get("dropped_events")) or bool(
            record.get("adapter_errors")
        )
        self.tracked_events_truncated = bool(record.get("live_view_truncated"))

    def tracked_events(self) -> list[dict[str, Any]]:
        return list(self._events)


class _VerificationProxy:
    def __init__(
        self,
        *,
        plan: Any,
        disposition: str,
        stages: tuple[Any, ...],
        streams: dict[str, _SealedStream],
    ) -> None:
        self.plan = plan
        self.disposition = disposition
        self._stages = stages
        self._streams = streams

    def stage_for_owner(self, owner: str) -> Any | None:
        return next((stage for stage in self._stages if stage.owner == owner), None)

    def stream(self, instance: str) -> _SealedStream | None:
        return self._streams.get(instance)

    def stages(self) -> tuple[Any, ...]:
        return self._stages


class _RefinementProxy:
    def __init__(
        self,
        *,
        plan: Any,
        disposition: str,
        targets: tuple[Any, ...],
        streams: dict[tuple[str, str], _SealedStream],
    ) -> None:
        self.plan = plan
        self.disposition = disposition
        self._targets = targets
        self._streams = streams

    def targets(self) -> tuple[Any, ...]:
        return self._targets

    def stream(self, target_id: str, instance: str) -> _SealedStream | None:
        return self._streams.get((target_id, instance))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise CrossFeedError(
                        f"{path}: line {line_number} is not a JSON object"
                    )
                events.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossFeedError(f"cannot replay cross-feed source {path}: {exc}") from exc
    return events


def build_crossfeed_view_from_run(run_dir: Path | str) -> CrossFeedView:
    """Replay the deterministic cross-feed derivation from sealed raw sources.

    This is intentionally independent of the in-memory object that was written
    during the live run. Integrity checking uses it to prove the derived view
    still follows from the exact parent / VERIFY / optional REFINE bytes.
    """

    run_dir = Path(run_dir)
    parent = load_manifest(run_dir)
    verification = load_verification_manifest(run_dir)

    nomination = verification.get("nomination") or {}
    participants = verification.get("participants") or {}
    owners = tuple(owner for owner in _OWNER_ORDER if owner in participants)
    plan = SimpleNamespace(
        verification_id=verification.get("verification_id"),
        generation=verification.get("generation"),
        source_run_id=(verification.get("source") or {}).get("run_id"),
        position_id=verification.get("position_id"),
        owners=owners,
        candidate_roots=tuple(nomination.get("candidate_roots") or ()),
        nominees_by_owner=dict(nomination.get("nominees_by_owner") or {}),
        participants=dict(participants),
    )

    verify_stream_records = {
        str(record.get("instance")): record
        for record in verification.get("streams") or []
        if isinstance(record, dict) and isinstance(record.get("instance"), str)
    }
    verify_streams: dict[str, _SealedStream] = {}
    for instance, record in verify_stream_records.items():
        rel = record.get("path")
        if not isinstance(rel, str) or not rel:
            raise CrossFeedError(f"VERIFY stream {instance!r} has no path")
        verify_streams[instance] = _SealedStream(
            instance,
            record,
            _read_jsonl(run_dir / "verification" / rel),
        )

    verify_stages = tuple(
        SimpleNamespace(
            owner=record.get("owner"),
            instance=record.get("instance"),
            family=record.get("family"),
            search_id=record.get("search_id"),
            disposition=record.get("disposition"),
        )
        for record in sorted(
            (item for item in verification.get("stages") or [] if isinstance(item, dict)),
            key=lambda item: int(item.get("dispatch_order") or 0),
        )
    )
    verification_proxy = _VerificationProxy(
        plan=plan,
        disposition=str((verification.get("disposition") or {}).get("run")),
        stages=verify_stages,
        streams=verify_streams,
    )

    refinement_proxy: _RefinementProxy | None = None
    refine_path = run_dir / "refinement" / "manifest.json"
    if refine_path.is_file():
        refinement = load_refinement_manifest(run_dir)
        source = refinement.get("source") or {}
        refine_plan = SimpleNamespace(
            refinement_id=refinement.get("refinement_id"),
            source_run_id=source.get("run_id"),
            source_verification_id=source.get("verification_id"),
        )
        refine_targets: list[Any] = []
        refine_streams: dict[tuple[str, str], _SealedStream] = {}
        for target_record in refinement.get("targets") or []:
            if not isinstance(target_record, dict):
                continue
            target_id = target_record.get("target_id")
            root_move = target_record.get("root_move")
            stages: dict[str, Any] = {}
            for stage_record in target_record.get("stages") or []:
                if not isinstance(stage_record, dict):
                    continue
                owner = stage_record.get("owner")
                if not isinstance(owner, str):
                    continue
                stages[owner] = SimpleNamespace(
                    owner=owner,
                    instance=stage_record.get("instance"),
                    family=stage_record.get("family"),
                    search_id=stage_record.get("search_id"),
                    disposition=stage_record.get("disposition"),
                )
            for stream_record in target_record.get("streams") or []:
                if not isinstance(stream_record, dict):
                    continue
                instance = stream_record.get("instance")
                rel = stream_record.get("path")
                if not isinstance(instance, str) or not isinstance(rel, str) or not rel:
                    raise CrossFeedError(
                        f"REFINE target {target_id!r} has malformed stream identity"
                    )
                refine_streams[(str(target_id), instance)] = _SealedStream(
                    instance,
                    stream_record,
                    _read_jsonl(run_dir / "refinement" / rel),
                )
            refine_targets.append(
                SimpleNamespace(
                    target=SimpleNamespace(
                        target_id=target_id,
                        root_move=root_move,
                    ),
                    stages=stages,
                )
            )
        refinement_proxy = _RefinementProxy(
            plan=refine_plan,
            disposition=str((refinement.get("disposition") or {}).get("run")),
            targets=tuple(refine_targets),
            streams=refine_streams,
        )

    return build_crossfeed_view(
        run_id=str(parent.get("run_id")),
        generation=int(parent.get("generation")),
        position_id=str((parent.get("position") or {}).get("position_id")),
        verification=verification_proxy,  # type: ignore[arg-type]
        refinement=refinement_proxy,  # type: ignore[arg-type]
    )


def _stream_refs(
    verification: dict[str, Any],
    refinement: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    verify_refs: list[dict[str, Any]] = []
    for stream in verification.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        verify_refs.append(
            {
                key: stream.get(key)
                for key in (
                    "instance",
                    "engine",
                    "role",
                    "path",
                    "search_ids",
                    "sha256",
                    "bytes",
                    "complete",
                    "contract_validatable",
                )
            }
        )
    verify_refs.sort(key=lambda item: (str(item.get("instance")), str(item.get("path"))))

    refine_refs: list[dict[str, Any]] = []
    if refinement is not None:
        for target in refinement.get("targets") or []:
            if not isinstance(target, dict):
                continue
            target_id = target.get("target_id")
            for stream in target.get("streams") or []:
                if not isinstance(stream, dict):
                    continue
                record = {
                    "target_id": target_id,
                    **{
                        key: stream.get(key)
                        for key in (
                            "instance",
                            "engine",
                            "role",
                            "path",
                            "search_ids",
                            "sha256",
                            "bytes",
                            "complete",
                            "contract_validatable",
                        )
                    },
                }
                refine_refs.append(record)
        refine_refs.sort(
            key=lambda item: (
                str(item.get("target_id")),
                str(item.get("instance")),
                str(item.get("path")),
            )
        )
    return {"verification": verify_refs, "refinement": refine_refs}


def _artifact_core(
    *,
    view: CrossFeedView,
    parent_sha: str,
    verification_manifest: dict[str, Any],
    verification_sha: str,
    refinement_manifest: dict[str, Any] | None,
    refinement_sha: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": CROSSFEED_SCHEMA_VERSION,
        "policy": CROSSFEED_POLICY,
        "source": {
            "run_id": view.run_id,
            "parent_manifest_sha256": parent_sha,
            "verification_id": verification_manifest.get("verification_id"),
            "verification_manifest_sha256": verification_sha,
            "refinement_id": (
                None
                if refinement_manifest is None
                else refinement_manifest.get("refinement_id")
            ),
            "refinement_manifest_sha256": refinement_sha,
        },
        "generation": view.generation,
        "position_id": view.position_id,
        "view": view.as_dict(),
        "source_streams": _stream_refs(verification_manifest, refinement_manifest),
    }


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def seal_crossfeed_artifact(
    view: CrossFeedView,
    run_dir: Path | str,
) -> dict[str, Any]:
    """Seal a cross-feed artifact after all source manifests have finalized."""

    run_dir = Path(run_dir)
    parent_path = run_dir / "manifest.json"
    verify_path = run_dir / "verification" / "manifest.json"
    if not parent_path.is_file() or not verify_path.is_file():
        raise CrossFeedError(
            "cross-feed sealing requires finalized parent and VERIFY manifests"
        )

    parent_problems = verify_bundle_integrity(run_dir)
    verify_problems = verify_verification_integrity(run_dir)
    if parent_problems:
        raise CrossFeedError(f"parent replay integrity failed: {parent_problems}")
    if verify_problems:
        raise CrossFeedError(f"VERIFY integrity failed: {verify_problems}")

    parent = load_manifest(run_dir)
    verification = load_verification_manifest(run_dir)
    if parent.get("run_id") != view.run_id:
        raise CrossFeedError("cross-feed view run_id does not match parent manifest")
    if parent.get("generation") != view.generation:
        raise CrossFeedError("cross-feed view generation does not match parent manifest")
    if (parent.get("position") or {}).get("position_id") != view.position_id:
        raise CrossFeedError("cross-feed view position does not match parent manifest")
    if verification.get("verification_id") != view.verification_id:
        raise CrossFeedError("cross-feed view verification_id does not match manifest")

    refinement_manifest: dict[str, Any] | None = None
    refinement_sha: str | None = None
    refine_path = run_dir / "refinement" / "manifest.json"
    if view.refinement_id is not None:
        if not refine_path.is_file():
            raise CrossFeedError(
                "cross-feed view references REFINE but its manifest is missing"
            )
        refine_problems = verify_refinement_integrity(run_dir)
        if refine_problems:
            raise CrossFeedError(f"REFINE integrity failed: {refine_problems}")
        refinement_manifest = load_refinement_manifest(run_dir)
        if refinement_manifest.get("refinement_id") != view.refinement_id:
            raise CrossFeedError("cross-feed view refinement_id does not match manifest")
        refinement_sha = sha256_file(refine_path)
    elif refine_path.is_file():
        raise CrossFeedError(
            "REFINE manifest exists but cross-feed view did not bind it"
        )

    core = _artifact_core(
        view=view,
        parent_sha=sha256_file(parent_path),
        verification_manifest=verification,
        verification_sha=sha256_file(verify_path),
        refinement_manifest=refinement_manifest,
        refinement_sha=refinement_sha,
    )
    digest = _canonical_digest(core)
    manifest = {
        **core,
        "crossfeed_id": f"{view.run_id}:crossfeed-v1:{digest[:16]}",
        "content_sha256": digest,
    }

    target_dir = run_dir / "crossfeed"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "manifest.json"
    target.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_crossfeed_manifest(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "crossfeed" / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossFeedError(f"cannot load cross-feed manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CrossFeedError("cross-feed manifest root must be an object")
    version = data.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != CROSSFEED_SCHEMA_VERSION
    ):
        raise CrossFeedError(f"unsupported cross-feed schema_version: {version!r}")
    return data


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def verify_crossfeed_integrity(run_dir: Path | str) -> list[str]:
    """Verify source binding, stream hashes, digest stability and scale firewall."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    try:
        manifest = load_crossfeed_manifest(run_dir)
        parent = load_manifest(run_dir)
        verification = load_verification_manifest(run_dir)
    except (CrossFeedError, ReplayError, VerificationError) as exc:
        return [str(exc)]

    source = manifest.get("source") or {}
    parent_path = run_dir / "manifest.json"
    verify_path = run_dir / "verification" / "manifest.json"
    if source.get("run_id") != parent.get("run_id"):
        problems.append("cross-feed source run_id mismatch")
    if (
        parent_path.is_file()
        and source.get("parent_manifest_sha256") != sha256_file(parent_path)
    ):
        problems.append("cross-feed parent manifest hash mismatch")
    if source.get("verification_id") != verification.get("verification_id"):
        problems.append("cross-feed verification_id mismatch")
    if (
        verify_path.is_file()
        and source.get("verification_manifest_sha256") != sha256_file(verify_path)
    ):
        problems.append("cross-feed VERIFY manifest hash mismatch")

    refinement: dict[str, Any] | None = None
    refine_id = source.get("refinement_id")
    refine_path = run_dir / "refinement" / "manifest.json"
    if refine_id is not None:
        try:
            refinement = load_refinement_manifest(run_dir)
        except Exception as exc:
            problems.append(f"cross-feed REFINE manifest unavailable: {exc}")
        else:
            if refinement.get("refinement_id") != refine_id:
                problems.append("cross-feed refinement_id mismatch")
            if source.get("refinement_manifest_sha256") != sha256_file(refine_path):
                problems.append("cross-feed REFINE manifest hash mismatch")
    elif source.get("refinement_manifest_sha256") is not None:
        problems.append("cross-feed has REFINE hash without refinement_id")

    if manifest.get("generation") != parent.get("generation"):
        problems.append("cross-feed generation mismatch")
    if manifest.get("position_id") != (parent.get("position") or {}).get("position_id"):
        problems.append("cross-feed position mismatch")

    source_streams = manifest.get("source_streams") or {}
    for record in source_streams.get("verification") or []:
        path = run_dir / "verification" / str(record.get("path"))
        if not path.is_file() or record.get("sha256") != sha256_file(path):
            problems.append(
                f"cross-feed VERIFY stream hash mismatch: {record.get('path')}"
            )
    for record in source_streams.get("refinement") or []:
        path = run_dir / "refinement" / str(record.get("path"))
        if not path.is_file() or record.get("sha256") != sha256_file(path):
            problems.append(
                f"cross-feed REFINE stream hash mismatch: {record.get('path')}"
            )

    try:
        replayed_view = build_crossfeed_view_from_run(run_dir)
    except (CrossFeedError, ReplayError, VerificationError) as exc:
        problems.append(f"cross-feed deterministic replay failed: {exc}")
    else:
        if replayed_view.as_dict() != manifest.get("view"):
            problems.append(
                "cross-feed derived view does not match deterministic replay of source evidence"
            )

    keys = {key.lower() for key in _walk_keys(manifest.get("view"))}
    forbidden = sorted(keys.intersection(_FORBIDDEN_DERIVED_KEYS))
    if forbidden:
        problems.append(
            f"cross-feed view contains forbidden derived score keys: {forbidden}"
        )

    core = {
        key: value
        for key, value in manifest.items()
        if key not in {"crossfeed_id", "content_sha256"}
    }
    actual_digest = _canonical_digest(core)
    if manifest.get("content_sha256") != actual_digest:
        problems.append("cross-feed content digest mismatch")
    expected_id = f"{source.get('run_id')}:crossfeed-v1:{actual_digest[:16]}"
    if manifest.get("crossfeed_id") != expected_id:
        problems.append("cross-feed id does not match content digest")

    for problem in verify_bundle_integrity(run_dir):
        problems.append(f"parent: {problem}")
    for problem in verify_verification_integrity(run_dir):
        problems.append(f"VERIFY: {problem}")
    if refinement is not None:
        for problem in verify_refinement_integrity(run_dir):
            problems.append(f"REFINE: {problem}")

    return problems
