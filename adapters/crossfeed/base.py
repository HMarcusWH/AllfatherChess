"""Common subprocess-safe contract for M14-E cross-feed adapters."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Sequence

from common.prefix_dispatch import compile_descendant_region, compile_prefix_dispatch
from common.search_request import PositionRequest, SearchRequestError
from adapters.crossfeed.evidence import CrossFeedAdapterEvidence


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class CrossFeedAdapterError(RuntimeError):
    """Raised when an adapter proposal would violate the M14-E boundary."""


def _canonical_move(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CrossFeedAdapterError(f"{label} must be a UCI move string")
    move = value.lower()
    if not _MOVE_RE.fullmatch(move):
        raise CrossFeedAdapterError(f"{label} is not canonical UCI: {value!r}")
    return move


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class CandidatePriorityHint:
    """One source-local ordinal hint.

    Rank is comparable only to another hint with the exact same context_key.
    The type intentionally carries that key so later routing cannot silently
    compare ranks from different engines, depths or candidate universes.
    """

    target_family: str
    move: str
    rank: int
    source_family: str
    source_phase: str
    search_id: str
    source_prefix: tuple[str, ...]
    candidate_universe: tuple[str, ...]
    context_key: tuple[Any, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_family": self.target_family,
            "move": self.move,
            "rank": self.rank,
            "source_family": self.source_family,
            "source_phase": self.source_phase,
            "search_id": self.search_id,
            "source_prefix": list(self.source_prefix),
            "candidate_universe": list(self.candidate_universe),
            "context_key": [
                list(item) if isinstance(item, tuple) else item
                for item in self.context_key
            ],
        }


@dataclass(frozen=True)
class TacticalAlarm:
    """Categorical native tactical alarm; no cp/Q threshold conversion."""

    target_family: str
    decision_move: str
    observed_move: str
    source_family: str
    source_phase: str
    search_id: str
    source_prefix: tuple[str, ...]
    semantics: str
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_family": self.target_family,
            "decision_move": self.decision_move,
            "observed_move": self.observed_move,
            "source_family": self.source_family,
            "source_phase": self.source_phase,
            "search_id": self.search_id,
            "source_prefix": list(self.source_prefix),
            "semantics": self.semantics,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class CrossFeedOperation:
    """One deterministic subprocess-safe proposal. It carries no authority."""

    operation_id: str
    operation_kind: str
    target_family: str
    phase: str
    position_command: str
    go_command: str
    searchmoves: tuple[str, ...]
    prefix: tuple[str, ...]
    candidate_roots: tuple[str, ...]
    source_evidence_digest: str
    source_refs: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
            "target_family": self.target_family,
            "phase": self.phase,
            "position_command": self.position_command,
            "go_command": self.go_command,
            "searchmoves": list(self.searchmoves),
            "prefix": list(self.prefix),
            "candidate_roots": list(self.candidate_roots),
            "source_evidence_digest": self.source_evidence_digest,
            "source_refs": list(self.source_refs),
        }


class BaseCrossFeedAdapter:
    """Pure adapter from typed evidence to UCI-safe operation proposals."""

    family: ClassVar[str]
    native_semantics_prefix: ClassVar[str]

    def _require_clean(self, evidence: CrossFeedAdapterEvidence) -> None:
        if evidence.evidence_faults:
            raise CrossFeedAdapterError(
                "adapter refuses evidence carrying explicit faults: "
                + "; ".join(evidence.evidence_faults)
            )

    def _operation(
        self,
        *,
        evidence: CrossFeedAdapterEvidence,
        operation_kind: str,
        phase: str,
        position_command: str,
        go_command: str,
        searchmoves: tuple[str, ...],
        prefix: tuple[str, ...],
        candidate_roots: tuple[str, ...],
        source_refs: tuple[str, ...],
    ) -> CrossFeedOperation:
        core = {
            "operation_kind": operation_kind,
            "target_family": self.family,
            "phase": phase,
            "position_command": position_command,
            "go_command": go_command,
            "searchmoves": list(searchmoves),
            "prefix": list(prefix),
            "candidate_roots": list(candidate_roots),
            "source_evidence_digest": evidence.digest,
            "source_refs": list(source_refs),
        }
        operation_id = f"{self.family}:{operation_kind.lower()}:{_digest(core)[:16]}"
        return CrossFeedOperation(
            operation_id=operation_id,
            operation_kind=operation_kind,
            target_family=self.family,
            phase=phase,
            position_command=position_command,
            go_command=go_command,
            searchmoves=searchmoves,
            prefix=prefix,
            candidate_roots=candidate_roots,
            source_evidence_digest=evidence.digest,
            source_refs=source_refs,
        )

    def compile_verify_set(
        self,
        evidence: CrossFeedAdapterEvidence,
        base_position: PositionRequest,
        candidates: Sequence[str],
        *,
        limit: dict[str, Any],
    ) -> CrossFeedOperation:
        """Compile a common-support or candidate-subset VERIFY proposal."""

        self._require_clean(evidence)
        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise CrossFeedAdapterError("VERIFY candidates must be a move sequence")
        moves = tuple(_canonical_move(move, "VERIFY candidate") for move in candidates)
        if not moves or len(moves) != len(set(moves)):
            raise CrossFeedAdapterError(
                "VERIFY candidates must be non-empty and duplicate-free"
            )
        if any(move not in evidence.candidate_roots for move in moves):
            raise CrossFeedAdapterError(
                "VERIFY proposal cannot introduce a move outside CrossFeed candidate roots"
            )

        refs = tuple(
            sorted(
                {
                    hint.search_id
                    for hint in evidence.hints
                    if hint.source_family == self.family
                    and hint.source_phase == "VERIFY"
                    and hint.candidate_universe_complete
                    and hint.observed_move in moves
                    and hint.stage_disposition == "completed"
                }
            )
        )
        if not refs:
            raise CrossFeedAdapterError(
                f"{self.family} has no clean VERIFY source context for requested set"
            )
        try:
            dispatch = compile_descendant_region(
                base_position,
                parent_prefix=(),
                child_moves=moves,
                limit=limit,
            )
        except SearchRequestError as exc:
            raise CrossFeedAdapterError(str(exc)) from exc
        return self._operation(
            evidence=evidence,
            operation_kind="VERIFY_SET",
            phase="VERIFY",
            position_command=dispatch.position_command,
            go_command=dispatch.go_command,
            searchmoves=dispatch.searchmoves,
            prefix=(),
            candidate_roots=moves,
            source_refs=refs,
        )

    def compile_refine_prefix(
        self,
        evidence: CrossFeedAdapterEvidence,
        base_position: PositionRequest,
        prefix: Sequence[str],
        *,
        limit: dict[str, Any],
    ) -> CrossFeedOperation:
        """Compile a PV-prefix REFINE proposal supported by typed evidence."""

        self._require_clean(evidence)
        if isinstance(prefix, (str, bytes)) or not isinstance(prefix, Sequence):
            raise CrossFeedAdapterError("REFINE prefix must be a move sequence")
        moves = tuple(_canonical_move(move, "REFINE prefix") for move in prefix)
        if len(moves) < 2:
            raise CrossFeedAdapterError(
                "M14-E REFINE_PREFIX requires at least root + descendant move"
            )
        supporting = tuple(
            hint
            for hint in evidence.hints
            if hint.stage_disposition == "completed"
            and hint.supported_full_prefix == moves
        )
        if not supporting:
            raise CrossFeedAdapterError(
                "REFINE prefix is not supported by typed cross-feed evidence"
            )
        try:
            dispatch = compile_prefix_dispatch(base_position, moves, limit=limit)
        except SearchRequestError as exc:
            raise CrossFeedAdapterError(str(exc)) from exc
        refs = tuple(
            sorted(
                {
                    f"{hint.source_family}:{hint.search_id}"
                    for hint in supporting
                }
            )
        )
        return self._operation(
            evidence=evidence,
            operation_kind="REFINE_PREFIX",
            phase="REFINE",
            position_command=dispatch.position_command,
            go_command=dispatch.go_command,
            searchmoves=dispatch.searchmoves,
            prefix=moves,
            candidate_roots=(moves[0],),
            source_refs=refs,
        )

    def priority_hints(
        self,
        evidence: CrossFeedAdapterEvidence,
    ) -> tuple[CandidatePriorityHint, ...]:
        """Expose source-local ordinal hints without combining contexts."""

        self._require_clean(evidence)
        result: list[CandidatePriorityHint] = []
        for hint in evidence.hints:
            if (
                hint.source_family != self.family
                or hint.source_rank is None
                or not hint.candidate_universe_complete
                or hint.stage_disposition != "completed"
            ):
                continue
            result.append(
                CandidatePriorityHint(
                    target_family=self.family,
                    move=hint.observed_move,
                    rank=hint.source_rank,
                    source_family=hint.source_family,
                    source_phase=hint.source_phase,
                    search_id=hint.search_id,
                    source_prefix=hint.source_prefix,
                    candidate_universe=hint.candidate_universe,
                    context_key=hint.context_key,
                )
            )
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.source_phase,
                    item.search_id,
                    item.source_prefix,
                    item.rank,
                    item.move,
                ),
            )
        )

    def tactical_alarms(
        self,
        evidence: CrossFeedAdapterEvidence,
    ) -> tuple[TacticalAlarm, ...]:
        """Return only categorical native mate alarms for this engine family."""

        self._require_clean(evidence)
        alarms: list[TacticalAlarm] = []
        seen: set[tuple[Any, ...]] = set()
        for hint in evidence.hints:
            if (
                hint.source_family != self.family
                or hint.stage_disposition != "completed"
            ):
                continue
            for evaluation in hint.evaluations:
                if (
                    evaluation.kind != "mate"
                    or not evaluation.semantics.startswith(
                        self.native_semantics_prefix
                    )
                ):
                    continue
                key = (
                    hint.search_id,
                    hint.source_prefix,
                    hint.observed_move,
                    evaluation.semantics,
                )
                if key in seen:
                    continue
                seen.add(key)
                alarms.append(
                    TacticalAlarm(
                        target_family=self.family,
                        decision_move=hint.decision_move,
                        observed_move=hint.observed_move,
                        source_family=hint.source_family,
                        source_phase=hint.source_phase,
                        search_id=hint.search_id,
                        source_prefix=hint.source_prefix,
                        semantics=evaluation.semantics,
                        kind=evaluation.kind,
                    )
                )
        return tuple(
            sorted(
                alarms,
                key=lambda alarm: (
                    alarm.source_phase,
                    alarm.search_id,
                    alarm.source_prefix,
                    alarm.observed_move,
                ),
            )
        )
