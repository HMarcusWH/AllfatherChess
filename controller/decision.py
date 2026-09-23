"""Pure hybrid-decision proposal and bounded authorization semantics.

This module owns no engine process, UCI output, routing, budget reservation, or
filesystem mutation. It maps already-collected typed evidence into a frozen
proposal and, for M14-C, applies a separate fail-closed authorization gate over
already-frozen in-memory facts.

Authority firewall
------------------
DecisionProposal != DecisionAuthorization. Resource authorization, observation,
proposal generation, and outward move authority remain separate objects.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from controller.crossfeed import CrossFeedView


DECISION_SCHEMA_VERSION = 1
DECISION_EVIDENCE_VERSION = "decision-evidence-v1"
COUNTERFACTUAL_POLICY = "unanimous_verify_v1"
AUTHORIZATION_POLICY = "bounded_preanchor_v0"
HYBRID_AUTHORITY = "HYBRID"
ANCHOR_FALLBACK = "ANCHOR_FALLBACK"
OWNER_ORDER = ("stockfish", "reckless", "lc0")
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class DecisionError(RuntimeError):
    """Raised when decision evidence cannot preserve the declared semantics."""


def _canonical_move(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DecisionError(f"{label} must be a UCI move string")
    move = value.lower()
    if not _MOVE_RE.fullmatch(move):
        raise DecisionError(f"{label} is not canonical UCI: {value!r}")
    return move


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DecisionCandidate:
    move: str
    original_owner: str

    def __post_init__(self) -> None:
        _canonical_move(self.move, "decision candidate")
        if self.original_owner not in OWNER_ORDER:
            raise DecisionError(
                f"unknown candidate original owner: {self.original_owner!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {"move": self.move, "original_owner": self.original_owner}


@dataclass(frozen=True)
class VerificationTerminalEvidence:
    """Terminal VERIFY facts, kept distinct from telemetry candidate updates."""

    verification_id: str
    candidate_roots: tuple[str, ...]
    final_by_owner: tuple[tuple[str, str | None], ...]
    stage_disposition_by_owner: tuple[tuple[str, str], ...]
    run_disposition: str
    complete: bool
    faults: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.verification_id, str) or not self.verification_id:
            raise DecisionError("verification_id must be non-empty")
        roots = tuple(
            _canonical_move(move, "verification candidate root")
            for move in self.candidate_roots
        )
        if len(roots) != 3 or len(set(roots)) != 3:
            raise DecisionError(
                "counterfactual decision v1 requires exactly three distinct VERIFY roots"
            )
        final_owners = tuple(owner for owner, _ in self.final_by_owner)
        stage_owners = tuple(owner for owner, _ in self.stage_disposition_by_owner)
        if final_owners != OWNER_ORDER or stage_owners != OWNER_ORDER:
            raise DecisionError(
                "terminal VERIFY evidence must contain canonical owners in canonical order"
            )
        for owner, move in self.final_by_owner:
            if move is not None:
                _canonical_move(move, f"VERIFY final move for {owner}")
        if not isinstance(self.run_disposition, str) or not self.run_disposition:
            raise DecisionError("VERIFY run disposition must be non-empty")
        if not isinstance(self.complete, bool):
            raise DecisionError("VERIFY terminal complete must be boolean")
        if any(not isinstance(item, str) or not item for item in self.faults):
            raise DecisionError("VERIFY terminal faults must be non-empty strings")

    def finals(self) -> dict[str, str | None]:
        return dict(self.final_by_owner)

    def stage_dispositions(self) -> dict[str, str]:
        return dict(self.stage_disposition_by_owner)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "candidate_roots": list(self.candidate_roots),
            "final_by_owner": dict(self.final_by_owner),
            "stage_disposition_by_owner": dict(self.stage_disposition_by_owner),
            "run_disposition": self.run_disposition,
            "complete": self.complete,
            "faults": list(self.faults),
        }


@dataclass(frozen=True)
class DecisionEvidence:
    """Everything policy v1 is allowed to inspect."""

    evidence_version: str
    run_id: str
    generation: int
    position_id: str
    crossfeed_policy: str
    crossfeed_view_digest: str
    crossfeed_verification_complete: bool
    candidates: tuple[DecisionCandidate, ...]
    verification_terminal: VerificationTerminalEvidence
    evidence_faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.evidence_version != DECISION_EVIDENCE_VERSION:
            raise DecisionError(
                f"unsupported decision evidence version: {self.evidence_version!r}"
            )
        if not isinstance(self.run_id, str) or not self.run_id:
            raise DecisionError("decision run_id must be non-empty")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise DecisionError("decision generation must be an integer")
        if not isinstance(self.position_id, str) or not self.position_id:
            raise DecisionError("decision position_id must be non-empty")
        if not isinstance(self.crossfeed_policy, str) or not self.crossfeed_policy:
            raise DecisionError("crossfeed policy must be non-empty")
        if not isinstance(self.crossfeed_verification_complete, bool):
            raise DecisionError("crossfeed_verification_complete must be boolean")
        if (
            not isinstance(self.crossfeed_view_digest, str)
            or len(self.crossfeed_view_digest) != 64
        ):
            raise DecisionError("crossfeed view digest must be a SHA-256 hex string")
        moves = tuple(candidate.move for candidate in self.candidates)
        if len(moves) != 3 or len(set(moves)) != 3:
            raise DecisionError(
                "decision evidence requires exactly three distinct candidates"
            )
        if moves != self.verification_terminal.candidate_roots:
            raise DecisionError(
                "decision candidates do not match terminal VERIFY candidate order"
            )
        if any(not isinstance(item, str) or not item for item in self.evidence_faults):
            raise DecisionError("decision evidence faults must be non-empty strings")

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_version": self.evidence_version,
            "run_id": self.run_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "crossfeed_policy": self.crossfeed_policy,
            "crossfeed_view_digest": self.crossfeed_view_digest,
            "crossfeed_verification_complete": self.crossfeed_verification_complete,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "verification_terminal": self.verification_terminal.as_dict(),
            "evidence_faults": list(self.evidence_faults),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


@dataclass(frozen=True)
class DecisionDisposition:
    code: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise DecisionError("decision disposition code must be non-empty")
        if not isinstance(self.reason, str) or not self.reason:
            raise DecisionError("decision disposition reason must be non-empty")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class DecisionEvaluation:
    """Policy result before the runtime stamps the causal decision boundary."""

    policy: str
    disposition: DecisionDisposition
    move: str | None
    source_owner: str | None
    evidence_digest: str

    def __post_init__(self) -> None:
        if self.policy != COUNTERFACTUAL_POLICY:
            raise DecisionError(f"unsupported decision policy: {self.policy!r}")
        if self.move is not None:
            _canonical_move(self.move, "decision evaluation move")
        if self.source_owner is not None and self.source_owner not in OWNER_ORDER:
            raise DecisionError(f"invalid decision source owner: {self.source_owner!r}")
        if not isinstance(self.evidence_digest, str) or len(self.evidence_digest) != 64:
            raise DecisionError("decision evaluation requires evidence SHA-256")


@dataclass(frozen=True)
class DecisionProposal:
    """Frozen counterfactual proposal; still carries no outward authority."""

    policy: str
    disposition: DecisionDisposition
    move: str | None
    source_owner: str | None
    evidence_digest: str
    frozen_observed_ms: float
    frozen_before_anchor: bool

    def __post_init__(self) -> None:
        if self.policy != COUNTERFACTUAL_POLICY:
            raise DecisionError(f"unsupported decision policy: {self.policy!r}")
        if self.move is not None:
            _canonical_move(self.move, "decision proposal move")
        if self.source_owner is not None and self.source_owner not in OWNER_ORDER:
            raise DecisionError(f"invalid proposal source owner: {self.source_owner!r}")
        if not isinstance(self.evidence_digest, str) or len(self.evidence_digest) != 64:
            raise DecisionError("decision proposal requires evidence SHA-256")
        if (
            isinstance(self.frozen_observed_ms, bool)
            or not isinstance(self.frozen_observed_ms, (int, float))
            or not math.isfinite(float(self.frozen_observed_ms))
            or float(self.frozen_observed_ms) < 0
        ):
            raise DecisionError("frozen_observed_ms must be a finite non-negative number")
        if not isinstance(self.frozen_before_anchor, bool):
            raise DecisionError("frozen_before_anchor must be boolean")
        proposed = self.disposition.code == "PROPOSED"
        if proposed != (self.move is not None):
            raise DecisionError(
                "PROPOSED disposition and proposal move presence must agree"
            )
        if proposed != (self.source_owner is not None):
            raise DecisionError(
                "PROPOSED disposition and source owner presence must agree"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "disposition": self.disposition.as_dict(),
            "move": self.move,
            "source_owner": self.source_owner,
            "evidence_digest": self.evidence_digest,
            "frozen_observed_ms": round(float(self.frozen_observed_ms), 6),
            "frozen_before_anchor": self.frozen_before_anchor,
        }


@dataclass(frozen=True)
class DecisionAuthorizationSnapshot:
    """Frozen in-memory facts the M14-C live authority gate may inspect.

    This object deliberately contains no file paths, handles, callbacks, engine
    objects, or lazy computations. Building it may read already-owned controller
    state, but authorization itself is pure and bounded.
    """

    run_id: str
    generation: int
    position_id: str
    request_class: str
    request_eligible: bool
    request_reason: str
    legal_roots: tuple[str, ...]
    external_root_restriction: tuple[str, ...]
    anchor_request_bounded: bool
    anchor_reserved: bool
    open_anchor_reservations: int
    budget_within_envelope: bool
    partitions_within_caps: bool
    wall_within_envelope: bool
    specialist_settlement_complete: bool
    open_specialist_reservations: int
    open_solver_reservations: int
    gpu_accounted: bool
    measurement_enabled: bool
    open_non_anchor_measurement_stages: int
    measurement_provider_available: bool
    measurement_known_failure: bool
    backend_generation_current: bool
    controller_fallback_latched: bool

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise DecisionError("authorization snapshot run_id must be non-empty")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise DecisionError("authorization snapshot generation must be an integer")
        if not isinstance(self.position_id, str) or not self.position_id:
            raise DecisionError("authorization snapshot position_id must be non-empty")
        if not isinstance(self.request_class, str) or not self.request_class:
            raise DecisionError("authorization snapshot request_class must be non-empty")
        if not isinstance(self.request_reason, str) or not self.request_reason:
            raise DecisionError("authorization snapshot request_reason must be non-empty")
        for label, moves in (
            ("legal_roots", self.legal_roots),
            ("external_root_restriction", self.external_root_restriction),
        ):
            seen: set[str] = set()
            for move in moves:
                canonical = _canonical_move(move, f"authorization snapshot {label}")
                if canonical in seen:
                    raise DecisionError(f"authorization snapshot {label} contains duplicates")
                seen.add(canonical)
        for label, value in (
            ("open_anchor_reservations", self.open_anchor_reservations),
            ("open_specialist_reservations", self.open_specialist_reservations),
            ("open_solver_reservations", self.open_solver_reservations),
            (
                "open_non_anchor_measurement_stages",
                self.open_non_anchor_measurement_stages,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise DecisionError(
                    f"authorization snapshot {label} must be >= 0"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "request_class": self.request_class,
            "request_eligible": self.request_eligible,
            "request_reason": self.request_reason,
            "legal_roots": list(self.legal_roots),
            "external_root_restriction": list(self.external_root_restriction),
            "anchor_request_bounded": self.anchor_request_bounded,
            "anchor_reserved": self.anchor_reserved,
            "open_anchor_reservations": self.open_anchor_reservations,
            "budget_within_envelope": self.budget_within_envelope,
            "partitions_within_caps": self.partitions_within_caps,
            "wall_within_envelope": self.wall_within_envelope,
            "specialist_settlement_complete": self.specialist_settlement_complete,
            "open_specialist_reservations": self.open_specialist_reservations,
            "open_solver_reservations": self.open_solver_reservations,
            "gpu_accounted": self.gpu_accounted,
            "measurement_enabled": self.measurement_enabled,
            "open_non_anchor_measurement_stages": (
                self.open_non_anchor_measurement_stages
            ),
            "measurement_provider_available": self.measurement_provider_available,
            "measurement_known_failure": self.measurement_known_failure,
            "backend_generation_current": self.backend_generation_current,
            "controller_fallback_latched": self.controller_fallback_latched,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


@dataclass(frozen=True)
class DecisionAuthorization:
    """Independent outward-move authority result."""

    policy: str
    authorized: bool
    move: str | None
    reason: str
    snapshot_digest: str

    def __post_init__(self) -> None:
        if self.policy != AUTHORIZATION_POLICY:
            raise DecisionError(f"unsupported authorization policy: {self.policy!r}")
        if self.authorized:
            if self.move is None:
                raise DecisionError("granted DecisionAuthorization requires a move")
            _canonical_move(self.move, "authorized decision move")
        elif self.move is not None:
            raise DecisionError("denied DecisionAuthorization cannot carry a move")
        if not isinstance(self.reason, str) or not self.reason:
            raise DecisionError("DecisionAuthorization reason must be non-empty")
        if not isinstance(self.snapshot_digest, str) or len(self.snapshot_digest) != 64:
            raise DecisionError("DecisionAuthorization requires snapshot SHA-256")

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "authorized": self.authorized,
            "move": self.move,
            "reason": self.reason,
            "snapshot_digest": self.snapshot_digest,
        }


@dataclass(frozen=True)
class FinalDecision:
    """One already-selected outward move and the authority that selected it."""

    authority: str
    emitted_move: str
    anchor_move: str
    proposal_move: str | None
    authorization: DecisionAuthorization
    authorization_snapshot: DecisionAuthorizationSnapshot

    def __post_init__(self) -> None:
        _canonical_move(self.emitted_move, "final emitted move")
        _canonical_move(self.anchor_move, "final anchor move")
        if self.proposal_move is not None:
            _canonical_move(self.proposal_move, "final proposal move")
        if self.authority not in (HYBRID_AUTHORITY, ANCHOR_FALLBACK):
            raise DecisionError(f"unknown final-decision authority: {self.authority!r}")
        if self.authorization.snapshot_digest != self.authorization_snapshot.digest:
            raise DecisionError(
                "DecisionAuthorization does not bind the supplied authorization snapshot"
            )
        if self.authority == HYBRID_AUTHORITY:
            if not self.authorization.authorized:
                raise DecisionError("HYBRID final decision requires granted authorization")
            if self.proposal_move is None:
                raise DecisionError("HYBRID final decision requires a proposal move")
            if self.authorization.move != self.proposal_move:
                raise DecisionError("authorization move and proposal move disagree")
            if self.emitted_move != self.proposal_move:
                raise DecisionError("HYBRID emitted move must equal the proposal move")
        else:
            if self.authorization.authorized:
                raise DecisionError("ANCHOR_FALLBACK cannot carry granted authorization")
            if self.emitted_move != self.anchor_move:
                raise DecisionError("ANCHOR_FALLBACK must emit the anchor move")

    def as_dict(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "emitted_move": self.emitted_move,
            "anchor_move": self.anchor_move,
            "proposal_move": self.proposal_move,
            "authorization": self.authorization.as_dict(),
            "authorization_snapshot": self.authorization_snapshot.as_dict(),
        }


@dataclass(frozen=True)
class CounterfactualDecision:
    """Legacy PR #22 proposal-versus-anchor comparison, not live authority.

    The outward_authority field is retained for replay-v1 compatibility and
    names the comparison baseline used by the counterfactual laboratory. M14-C
    actual authority is recorded separately by FinalDecision.
    """

    proposal: DecisionProposal
    anchor_move: str
    proposal_matches_anchor: bool | None
    would_change_outward_move: bool | None
    outward_authority: str = "stockfish-anchor"

    def __post_init__(self) -> None:
        _canonical_move(self.anchor_move, "anchor move")
        if self.outward_authority != "stockfish-anchor":
            raise DecisionError(
                "counterfactual replay-v1 comparison baseline must remain stockfish-anchor"
            )
        if self.proposal.move is None:
            if (
                self.proposal_matches_anchor is not None
                or self.would_change_outward_move is not None
            ):
                raise DecisionError(
                    "no-proposal decision cannot claim an anchor relation"
                )
        else:
            expected = self.proposal.move == self.anchor_move
            if self.proposal_matches_anchor is not expected:
                raise DecisionError("proposal_matches_anchor is inconsistent")
            if self.would_change_outward_move is not (not expected):
                raise DecisionError("would_change_outward_move is inconsistent")

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.as_dict(),
            "anchor_move": self.anchor_move,
            "proposal_matches_anchor": self.proposal_matches_anchor,
            "would_change_outward_move": self.would_change_outward_move,
            "outward_authority": self.outward_authority,
        }


def build_decision_evidence(
    view: CrossFeedView,
    terminal: VerificationTerminalEvidence,
) -> DecisionEvidence:
    """Compose the only evidence policy v1 may inspect."""

    if terminal.verification_id != view.verification_id:
        raise DecisionError("terminal VERIFY identity does not match cross-feed view")
    candidate_moves = tuple(candidate.move for candidate in view.candidates)
    if candidate_moves != terminal.candidate_roots:
        raise DecisionError(
            "terminal VERIFY candidate order does not match cross-feed view"
        )

    faults = list(view.evidence_faults)
    faults.extend(terminal.faults)
    return DecisionEvidence(
        evidence_version=DECISION_EVIDENCE_VERSION,
        run_id=view.run_id,
        generation=view.generation,
        position_id=view.position_id,
        crossfeed_policy=view.policy,
        crossfeed_view_digest=canonical_digest(view.as_dict()),
        crossfeed_verification_complete=view.verification_complete,
        candidates=tuple(
            DecisionCandidate(
                move=candidate.move,
                original_owner=candidate.original_owner,
            )
            for candidate in view.candidates
        ),
        verification_terminal=terminal,
        evidence_faults=tuple(sorted(set(faults))),
    )


def evaluate_decision_policy(
    evidence: DecisionEvidence,
    *,
    policy: str = COUNTERFACTUAL_POLICY,
) -> DecisionEvaluation:
    """Apply the intentionally narrow counterfactual policy v1."""

    if policy != COUNTERFACTUAL_POLICY:
        raise DecisionError(f"unsupported decision policy: {policy!r}")

    if evidence.evidence_faults:
        disposition = DecisionDisposition(
            "NO_PROPOSAL_EVIDENCE_FAULT",
            "typed evidence carries explicit loss, truncation, or terminal faults",
        )
        return DecisionEvaluation(
            policy,
            disposition,
            None,
            None,
            evidence.digest,
        )

    terminal = evidence.verification_terminal
    if not evidence.crossfeed_verification_complete or not terminal.complete:
        disposition = DecisionDisposition(
            "NO_PROPOSAL_VERIFY_INCOMPLETE",
            "VERIFY evidence is not complete in both cross-feed and terminal views",
        )
        return DecisionEvaluation(
            policy,
            disposition,
            None,
            None,
            evidence.digest,
        )

    finals = terminal.finals()
    moves = tuple(finals[owner] for owner in OWNER_ORDER)
    if any(move is None for move in moves):
        disposition = DecisionDisposition(
            "NO_PROPOSAL_TERMINAL_INCOMPLETE",
            "at least one completed verifier lacks a terminal bestmove",
        )
        return DecisionEvaluation(
            policy,
            disposition,
            None,
            None,
            evidence.digest,
        )

    candidate_set = set(terminal.candidate_roots)
    if any(move not in candidate_set for move in moves):
        disposition = DecisionDisposition(
            "INVALID_EVIDENCE",
            "a terminal verifier move escaped the declared common candidate set",
        )
        return DecisionEvaluation(
            policy,
            disposition,
            None,
            None,
            evidence.digest,
        )

    distinct = set(moves)
    if len(distinct) != 1:
        disposition = DecisionDisposition(
            "NO_PROPOSAL_NONUNANIMOUS",
            "completed VERIFY terminal bestmoves are not unanimous",
        )
        return DecisionEvaluation(
            policy,
            disposition,
            None,
            None,
            evidence.digest,
        )

    move = str(moves[0])
    source_owner = next(
        candidate.original_owner
        for candidate in evidence.candidates
        if candidate.move == move
    )
    disposition = DecisionDisposition(
        "PROPOSED",
        "all three completed verifiers independently ended on one common-support candidate",
    )
    return DecisionEvaluation(
        policy,
        disposition,
        move,
        source_owner,
        evidence.digest,
    )


def freeze_decision_proposal(
    evaluation: DecisionEvaluation,
    *,
    frozen_observed_ms: float,
    frozen_before_anchor: bool,
) -> DecisionProposal:
    """Stamp a pure policy result at the coordinator's causal boundary."""

    return DecisionProposal(
        policy=evaluation.policy,
        disposition=evaluation.disposition,
        move=evaluation.move,
        source_owner=evaluation.source_owner,
        evidence_digest=evaluation.evidence_digest,
        frozen_observed_ms=frozen_observed_ms,
        frozen_before_anchor=frozen_before_anchor,
    )


def authorize_decision(
    proposal: DecisionProposal,
    evidence: DecisionEvidence,
    snapshot: DecisionAuthorizationSnapshot,
    *,
    policy: str = AUTHORIZATION_POLICY,
) -> DecisionAuthorization:
    """Apply the M14-C fail-closed outward authority gate.

    Every input is already frozen in memory. This function performs no engine
    work, filesystem access, waiting, calibration loading, or resource sampling.
    """

    if policy != AUTHORIZATION_POLICY:
        raise DecisionError(f"unsupported authorization policy: {policy!r}")

    reasons: list[str] = []
    if proposal.disposition.code != "PROPOSED" or proposal.move is None:
        reasons.append("no frozen hybrid proposal")
    if not proposal.frozen_before_anchor:
        reasons.append("proposal was not frozen before anchor completion")
    if proposal.evidence_digest != evidence.digest:
        reasons.append("proposal/evidence digest mismatch")
    if evidence.run_id != snapshot.run_id:
        reasons.append("run_id mismatch")
    if evidence.generation != snapshot.generation:
        reasons.append("generation mismatch")
    if evidence.position_id != snapshot.position_id:
        reasons.append("position_id mismatch")
    if evidence.evidence_faults:
        reasons.append("decision evidence carries explicit faults")
    if (
        not evidence.crossfeed_verification_complete
        or not evidence.verification_terminal.complete
    ):
        reasons.append("required VERIFY evidence is incomplete")

    if proposal.move is not None:
        if proposal.move not in set(snapshot.legal_roots):
            reasons.append("proposal move is outside the qualified legal-root universe")
        if (
            snapshot.external_root_restriction
            and proposal.move not in set(snapshot.external_root_restriction)
        ):
            reasons.append("proposal move is outside external searchmoves")

    if snapshot.request_class != "movetime_v0" or not snapshot.request_eligible:
        reasons.append(f"unsupported request class: {snapshot.request_reason}")
    if not snapshot.anchor_request_bounded:
        reasons.append("anchor request is not bounded by the declared wall envelope")
    if not snapshot.anchor_reserved:
        reasons.append("anchor resource reservation is missing")
    if snapshot.open_anchor_reservations != 1:
        reasons.append("exactly one in-flight anchor reservation is required")
    if not snapshot.budget_within_envelope:
        reasons.append("known budget state exceeds the declared resource envelope")
    if not snapshot.partitions_within_caps:
        reasons.append("known specialist partition cap is exceeded")
    if not snapshot.wall_within_envelope:
        reasons.append("wall envelope is already exhausted")
    if not snapshot.specialist_settlement_complete:
        reasons.append("specialist settlement is incomplete")
    if snapshot.open_specialist_reservations:
        reasons.append("indispensable specialist reservation remains open")
    if snapshot.open_solver_reservations:
        reasons.append("completed EXPLORE solver reservation remains open")
    if not snapshot.gpu_accounted:
        reasons.append("declared GPU resource is not accounted")
    if not snapshot.measurement_enabled:
        reasons.append("physical resource measurement is disabled")
    if snapshot.open_non_anchor_measurement_stages:
        reasons.append("non-anchor physical measurement stage remains open")
    if not snapshot.measurement_provider_available:
        reasons.append("physical resource measurement provider is unavailable")
    if snapshot.measurement_known_failure:
        reasons.append("completed physical resource evidence contains a known failure")
    if not snapshot.backend_generation_current:
        reasons.append("backend generation is stale or unhealthy")
    if snapshot.controller_fallback_latched:
        reasons.append("controller has already latched anchor-only fallback")

    if reasons:
        return DecisionAuthorization(
            policy=policy,
            authorized=False,
            move=None,
            reason="; ".join(reasons),
            snapshot_digest=snapshot.digest,
        )
    return DecisionAuthorization(
        policy=policy,
        authorized=True,
        move=proposal.move,
        reason="all bounded M14-C authorization gates passed",
        snapshot_digest=snapshot.digest,
    )


def select_final_decision(
    *,
    anchor_move: str,
    proposal: DecisionProposal | None,
    authorization: DecisionAuthorization,
    authorization_snapshot: DecisionAuthorizationSnapshot,
) -> FinalDecision:
    anchor = _canonical_move(anchor_move, "anchor move")
    proposal_move = None if proposal is None else proposal.move
    if authorization.authorized:
        return FinalDecision(
            authority=HYBRID_AUTHORITY,
            emitted_move=str(authorization.move),
            anchor_move=anchor,
            proposal_move=proposal_move,
            authorization=authorization,
            authorization_snapshot=authorization_snapshot,
        )
    return FinalDecision(
        authority=ANCHOR_FALLBACK,
        emitted_move=anchor,
        anchor_move=anchor,
        proposal_move=proposal_move,
        authorization=authorization,
        authorization_snapshot=authorization_snapshot,
    )


def denied_counterfactual_authorization() -> DecisionAuthorization:
    return DecisionAuthorization(
        policy=AUTHORIZATION_POLICY,
        authorized=False,
        move=None,
        reason="counterfactual-only path grants no outward decision authority",
        snapshot_digest="0" * 64,
    )


def attach_anchor(
    proposal: DecisionProposal,
    *,
    anchor_move: str,
) -> CounterfactualDecision:
    move = _canonical_move(anchor_move, "anchor move")
    if proposal.move is None:
        return CounterfactualDecision(
            proposal=proposal,
            anchor_move=move,
            proposal_matches_anchor=None,
            would_change_outward_move=None,
        )
    matches = proposal.move == move
    return CounterfactualDecision(
        proposal=proposal,
        anchor_move=move,
        proposal_matches_anchor=matches,
        would_change_outward_move=not matches,
    )
