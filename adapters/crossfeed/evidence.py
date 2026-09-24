"""Decision-inert evidence projection for engine-specific cross-feed adapters.

M14-E deliberately does not change controller.crossfeed.CrossFeedView.  That
object is already hashed into M14-C DecisionEvidence.  Instead this module
projects the frozen CrossFeedView plus optional M14-D recursive REFINE evidence
into a separate adapter-only evidence object.

The projection preserves engine-native semantics, source search context and
candidate-universe identity.  It grants no resource, routing, process or move
authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from controller.crossfeed import (
    CrossFeedView,
    NativeEvaluation,
    NativeWork,
    build_crossfeed_view_from_run,
)
from controller.refinement import RefinementRun, load_refinement_manifest


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_OWNER_ORDER = ("stockfish", "reckless", "lc0")


class CrossFeedAdapterEvidenceError(RuntimeError):
    """Raised when adapter evidence would lose provenance or type safety."""


def _canonical_move(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CrossFeedAdapterEvidenceError(f"{label} must be a UCI move string")
    move = value.lower()
    if not _MOVE_RE.fullmatch(move):
        raise CrossFeedAdapterEvidenceError(
            f"{label} is not canonical lowercase UCI: {value!r}"
        )
    return move


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CrossFeedAdapterEvidenceError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise CrossFeedAdapterEvidenceError(f"{label} must be finite")
    return number


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdapterSourceHint:
    """One provenance-complete observation consumable by an engine adapter."""

    decision_move: str
    observed_move: str
    source_owner: str
    source_instance: str
    source_family: str
    source_phase: str
    source_scope_id: str
    source_prefix: tuple[str, ...]
    source_depth: int
    candidate_universe: tuple[str, ...]
    candidate_universe_complete: bool
    source_rank: int | None
    pv_prefix: tuple[str, ...]
    evaluations: tuple[NativeEvaluation, ...]
    work: tuple[NativeWork, ...]
    search_id: str
    sequence: int
    observed_ms: float
    stage_disposition: str

    def __post_init__(self) -> None:
        _canonical_move(self.decision_move, "decision_move")
        _canonical_move(self.observed_move, "observed_move")
        if self.source_owner not in _OWNER_ORDER:
            raise CrossFeedAdapterEvidenceError(
                f"unknown source owner: {self.source_owner!r}"
            )
        if self.source_family != self.source_owner:
            raise CrossFeedAdapterEvidenceError(
                f"source family {self.source_family!r} does not match owner "
                f"{self.source_owner!r}"
            )
        if self.source_phase not in ("VERIFY", "REFINE"):
            raise CrossFeedAdapterEvidenceError(
                f"unsupported source phase: {self.source_phase!r}"
            )
        if not self.source_scope_id:
            raise CrossFeedAdapterEvidenceError("source_scope_id must be non-empty")
        if not self.source_instance:
            raise CrossFeedAdapterEvidenceError("source_instance must be non-empty")
        if not self.search_id:
            raise CrossFeedAdapterEvidenceError("search_id must be non-empty")
        if self.source_depth != len(self.source_prefix):
            raise CrossFeedAdapterEvidenceError("source_depth/prefix mismatch")
        for index, move in enumerate(self.source_prefix):
            _canonical_move(move, f"source_prefix[{index}]")
        seen: set[str] = set()
        for index, move in enumerate(self.candidate_universe):
            canonical = _canonical_move(move, f"candidate_universe[{index}]")
            if canonical in seen:
                raise CrossFeedAdapterEvidenceError(
                    "candidate_universe contains duplicate moves"
                )
            seen.add(canonical)
        if self.source_rank is not None and (
            isinstance(self.source_rank, bool)
            or not isinstance(self.source_rank, int)
            or self.source_rank < 1
        ):
            raise CrossFeedAdapterEvidenceError(
                "source_rank must be a positive integer when present"
            )
        for index, move in enumerate(self.pv_prefix):
            _canonical_move(move, f"pv_prefix[{index}]")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise CrossFeedAdapterEvidenceError(
                "sequence must be a non-negative integer"
            )
        _finite_number(self.observed_ms, "observed_ms")

    @property
    def context_key(self) -> tuple[Any, ...]:
        """Ranks are ordinal only inside this exact source context."""

        return (
            self.source_family,
            self.source_phase,
            self.search_id,
            self.source_prefix,
            self.candidate_universe,
        )

    @property
    def supported_full_prefix(self) -> tuple[str, ...]:
        if self.source_phase == "VERIFY":
            return (self.observed_move,)
        return self.source_prefix + (self.observed_move,)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_move": self.decision_move,
            "observed_move": self.observed_move,
            "source_owner": self.source_owner,
            "source_instance": self.source_instance,
            "source_family": self.source_family,
            "source_phase": self.source_phase,
            "source_scope_id": self.source_scope_id,
            "source_prefix": list(self.source_prefix),
            "source_depth": self.source_depth,
            "candidate_universe": list(self.candidate_universe),
            "candidate_universe_complete": self.candidate_universe_complete,
            "source_rank": self.source_rank,
            "pv_prefix": list(self.pv_prefix),
            "evaluations": [item.as_dict() for item in self.evaluations],
            "work": [item.as_dict() for item in self.work],
            "search_id": self.search_id,
            "sequence": self.sequence,
            "observed_ms": self.observed_ms,
            "stage_disposition": self.stage_disposition,
        }


@dataclass(frozen=True)
class CrossFeedAdapterEvidence:
    run_id: str
    generation: int
    position_id: str
    candidate_roots: tuple[str, ...]
    source_view_digest: str
    hints: tuple[AdapterSourceHint, ...]
    evidence_faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.run_id:
            raise CrossFeedAdapterEvidenceError("run_id must be non-empty")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise CrossFeedAdapterEvidenceError("generation must be an integer")
        if not self.position_id:
            raise CrossFeedAdapterEvidenceError("position_id must be non-empty")
        if len(self.source_view_digest) != 64:
            raise CrossFeedAdapterEvidenceError(
                "source_view_digest must be a SHA-256 hex digest"
            )
        roots = tuple(
            _canonical_move(move, "candidate_root") for move in self.candidate_roots
        )
        if len(roots) != len(set(roots)):
            raise CrossFeedAdapterEvidenceError("candidate_roots contain duplicates")

    @property
    def digest(self) -> str:
        return _canonical_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "candidate_roots": list(self.candidate_roots),
            "source_view_digest": self.source_view_digest,
            "hints": [hint.as_dict() for hint in self.hints],
            "evidence_faults": list(self.evidence_faults),
        }


def _latest_per_context(hints: Iterable[AdapterSourceHint]) -> tuple[AdapterSourceHint, ...]:
    latest: dict[tuple[Any, ...], AdapterSourceHint] = {}
    for hint in hints:
        key = (
            hint.source_family,
            hint.source_phase,
            hint.search_id,
            hint.source_prefix,
            hint.observed_move,
        )
        current = latest.get(key)
        if current is None or (hint.sequence, hint.observed_ms) > (
            current.sequence,
            current.observed_ms,
        ):
            latest[key] = hint
    return tuple(
        sorted(
            latest.values(),
            key=lambda item: (
                item.source_depth,
                _OWNER_ORDER.index(item.source_owner),
                item.source_phase,
                item.search_id,
                item.source_prefix,
                item.source_rank if item.source_rank is not None else 1_000_000,
                item.observed_move,
                item.sequence,
            ),
        )
    )


def _project_view(view: CrossFeedView) -> list[AdapterSourceHint]:
    roots = tuple(candidate.move for candidate in view.candidates)

    # Root-shell REFINE CandidateHint does not carry the exact child universe.
    # Preserve the observed universe but mark it incomplete so rank consumers do
    # not accidentally compare ordinals across an unknown set.
    refine_universe: dict[tuple[str, str], tuple[str, ...]] = {}
    refine_grouped: dict[tuple[str, str], list[str]] = {}
    for candidate in view.candidates:
        for hint in candidate.hints:
            if hint.source_phase != "REFINE":
                continue
            key = (hint.source_owner, hint.search_id)
            refine_grouped.setdefault(key, [])
            if hint.observed_move not in refine_grouped[key]:
                refine_grouped[key].append(hint.observed_move)
    for key, moves in refine_grouped.items():
        refine_universe[key] = tuple(moves)

    projected: list[AdapterSourceHint] = []
    for candidate in view.candidates:
        for hint in candidate.hints:
            if hint.source_phase == "VERIFY":
                prefix: tuple[str, ...] = ()
                universe = roots
                complete = True
            else:
                prefix = (candidate.move,)
                universe = refine_universe.get(
                    (hint.source_owner, hint.search_id),
                    (hint.observed_move,),
                )
                complete = False
            projected.append(
                AdapterSourceHint(
                    decision_move=candidate.move,
                    observed_move=hint.observed_move,
                    source_owner=hint.source_owner,
                    source_instance=hint.source_instance,
                    source_family=hint.source_family,
                    source_phase=hint.source_phase,
                    source_scope_id=hint.search_id,
                    source_prefix=prefix,
                    source_depth=len(prefix),
                    candidate_universe=universe,
                    candidate_universe_complete=complete,
                    source_rank=hint.source_rank,
                    pv_prefix=hint.pv_prefix,
                    evaluations=hint.evaluations,
                    work=hint.work,
                    search_id=hint.search_id,
                    sequence=hint.sequence,
                    observed_ms=hint.observed_ms,
                    stage_disposition=hint.stage_disposition,
                )
            )
    return projected


def _event_hint(
    *,
    event: dict[str, Any],
    decision_move: str,
    owner: str,
    instance: str,
    family: str,
    expansion_id: str,
    prefix: tuple[str, ...],
    child_moves: tuple[str, ...],
    search_id: str,
    stage_disposition: str,
) -> AdapterSourceHint | None:
    if event.get("event_type") != "candidate.update":
        return None
    if event.get("engine_instance") != instance:
        raise CrossFeedAdapterEvidenceError(
            f"{expansion_id}: event instance does not match stage instance"
        )
    if event.get("engine") != family:
        raise CrossFeedAdapterEvidenceError(
            f"{expansion_id}: event family does not match stage family"
        )
    if event.get("search_id") != search_id:
        raise CrossFeedAdapterEvidenceError(
            f"{expansion_id}: event search_id does not match stage"
        )
    candidate = event.get("candidate")
    if not isinstance(candidate, dict):
        raise CrossFeedAdapterEvidenceError(
            f"{expansion_id}: candidate.update is missing candidate"
        )
    observed_move = _canonical_move(candidate.get("move"), "candidate.move")
    if observed_move not in child_moves:
        raise CrossFeedAdapterEvidenceError(
            f"{expansion_id}: candidate {observed_move} escaped stage universe"
        )
    pv_raw = candidate.get("pv") or []
    if not isinstance(pv_raw, list):
        raise CrossFeedAdapterEvidenceError("candidate.pv must be an array")
    pv = tuple(_canonical_move(move, "candidate.pv") for move in pv_raw)
    if not pv:
        pv = (observed_move,)
    if pv[0] != observed_move:
        raise CrossFeedAdapterEvidenceError(
            f"{expansion_id}: candidate PV head differs from observed move"
        )
    evaluations_raw = candidate.get("evaluations") or []
    if not isinstance(evaluations_raw, list):
        raise CrossFeedAdapterEvidenceError("candidate evaluations must be an array")
    work_raw = event.get("work") or []
    if not isinstance(work_raw, list):
        raise CrossFeedAdapterEvidenceError("candidate work must be an array")
    rank = candidate.get("multipv_index")
    sequence = event.get("sequence")
    observed_ms = event.get("observed_ms")
    return AdapterSourceHint(
        decision_move=_canonical_move(decision_move, "decision_move"),
        observed_move=observed_move,
        source_owner=owner,
        source_instance=instance,
        source_family=family,
        source_phase="REFINE",
        source_scope_id=expansion_id,
        source_prefix=prefix,
        source_depth=len(prefix),
        candidate_universe=child_moves,
        candidate_universe_complete=True,
        source_rank=rank,
        pv_prefix=prefix + pv,
        evaluations=tuple(
            NativeEvaluation.from_payload(item) for item in evaluations_raw
        ),
        work=tuple(NativeWork.from_payload(item) for item in work_raw),
        search_id=search_id,
        sequence=sequence,
        observed_ms=float(_finite_number(observed_ms, "observed_ms")),
        stage_disposition=stage_disposition,
    )


def _append_live_recursive(
    hints: list[AdapterSourceHint],
    faults: list[str],
    refinement: RefinementRun,
) -> None:
    for expansion in refinement.expansions():
        prefix = tuple(expansion.prefix)
        decision_move = prefix[0]
        for owner, stage in sorted(
            expansion.stages.items(),
            key=lambda item: _OWNER_ORDER.index(item[0]),
        ):
            stream = refinement.expansion_stream(expansion.expansion_id, stage.instance)
            if stream is None:
                faults.append(
                    f"REFINE recursive stream missing for {expansion.expansion_id}/{owner}"
                )
                continue
            if stream.evidence_lossy:
                faults.append(
                    f"REFINE recursive stream lossy for {expansion.expansion_id}/{owner}"
                )
            if stream.tracked_events_truncated:
                faults.append(
                    "REFINE recursive live view truncated for "
                    f"{expansion.expansion_id}/{owner}"
                )
            for event in stream.tracked_events():
                projected = _event_hint(
                    event=event,
                    decision_move=decision_move,
                    owner=owner,
                    instance=stage.instance,
                    family=stage.family,
                    expansion_id=expansion.expansion_id,
                    prefix=prefix,
                    child_moves=tuple(stage.child_moves),
                    search_id=stage.search_id,
                    stage_disposition=stage.disposition,
                )
                if projected is not None:
                    hints.append(projected)


def build_adapter_evidence(
    view: CrossFeedView,
    *,
    refinement: RefinementRun | None = None,
) -> CrossFeedAdapterEvidence:
    """Project one live CrossFeedView into the separate M14-E adapter plane."""

    hints = _project_view(view)
    faults = list(view.evidence_faults)
    if refinement is not None:
        _append_live_recursive(hints, faults, refinement)
    return CrossFeedAdapterEvidence(
        run_id=view.run_id,
        generation=view.generation,
        position_id=view.position_id,
        candidate_roots=tuple(candidate.move for candidate in view.candidates),
        source_view_digest=_canonical_digest(view.as_dict()),
        hints=_latest_per_context(hints),
        evidence_faults=tuple(sorted(set(faults))),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise CrossFeedAdapterEvidenceError(
                        f"{path}: line {line_number} is not a JSON object"
                    )
                events.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossFeedAdapterEvidenceError(
            f"cannot read adapter evidence stream {path}: {exc}"
        ) from exc
    return events


def build_adapter_evidence_from_run(
    run_dir: Path | str,
) -> CrossFeedAdapterEvidence:
    """Rebuild adapter evidence from sealed CrossFeed + REFINE-v2 artifacts."""

    run_dir = Path(run_dir)
    view = build_crossfeed_view_from_run(run_dir)
    hints = _project_view(view)
    faults = list(view.evidence_faults)

    refine_path = run_dir / "refinement" / "manifest.json"
    if refine_path.is_file():
        refinement = load_refinement_manifest(run_dir)
        for expansion in refinement.get("expansions") or []:
            if not isinstance(expansion, dict):
                raise CrossFeedAdapterEvidenceError(
                    "recursive REFINE expansion is not an object"
                )
            expansion_id = expansion.get("expansion_id")
            prefix = tuple(expansion.get("prefix") or ())
            if not isinstance(expansion_id, str) or not expansion_id:
                raise CrossFeedAdapterEvidenceError(
                    "recursive REFINE expansion has no expansion_id"
                )
            if not prefix:
                raise CrossFeedAdapterEvidenceError(
                    f"{expansion_id}: recursive prefix is empty"
                )
            decision_move = _canonical_move(prefix[0], "recursive decision root")
            stream_by_instance = {
                record.get("instance"): record
                for record in expansion.get("streams") or []
                if isinstance(record, dict)
                and isinstance(record.get("instance"), str)
            }
            for stage in expansion.get("stages") or []:
                if not isinstance(stage, dict):
                    continue
                owner = stage.get("owner")
                instance = stage.get("instance")
                family = stage.get("family")
                search_id = stage.get("search_id")
                child_moves = tuple(stage.get("child_moves") or ())
                if (
                    not isinstance(owner, str)
                    or not isinstance(instance, str)
                    or not isinstance(family, str)
                    or not isinstance(search_id, str)
                ):
                    raise CrossFeedAdapterEvidenceError(
                        f"{expansion_id}: malformed recursive stage identity"
                    )
                stream = stream_by_instance.get(instance)
                if not isinstance(stream, dict):
                    faults.append(
                        f"REFINE recursive stream missing for {expansion_id}/{owner}"
                    )
                    continue
                if stream.get("dropped_events") or stream.get("adapter_errors"):
                    faults.append(
                        f"REFINE recursive stream lossy for {expansion_id}/{owner}"
                    )
                if stream.get("live_view_truncated"):
                    faults.append(
                        f"REFINE recursive live view truncated for {expansion_id}/{owner}"
                    )
                rel = stream.get("path")
                if not isinstance(rel, str) or not rel:
                    raise CrossFeedAdapterEvidenceError(
                        f"{expansion_id}: malformed recursive stream path"
                    )
                for event in _read_jsonl(run_dir / "refinement" / rel):
                    projected = _event_hint(
                        event=event,
                        decision_move=decision_move,
                        owner=owner,
                        instance=instance,
                        family=family,
                        expansion_id=expansion_id,
                        prefix=tuple(
                            _canonical_move(move, "recursive prefix")
                            for move in prefix
                        ),
                        child_moves=tuple(
                            _canonical_move(move, "recursive child")
                            for move in child_moves
                        ),
                        search_id=search_id,
                        stage_disposition=str(stage.get("disposition")),
                    )
                    if projected is not None:
                        hints.append(projected)

    return CrossFeedAdapterEvidence(
        run_id=view.run_id,
        generation=view.generation,
        position_id=view.position_id,
        candidate_roots=tuple(candidate.move for candidate in view.candidates),
        source_view_digest=_canonical_digest(view.as_dict()),
        hints=_latest_per_context(hints),
        evidence_faults=tuple(sorted(set(faults))),
    )
