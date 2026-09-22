"""Pure counterfactual hybrid-decision semantics.

This module owns no engine process, UCI output, routing, budget reservation, or
filesystem mutation. It maps already-collected typed evidence into a frozen
counterfactual proposal.

Authority firewall
------------------
A DecisionProposal is not a DecisionAuthorization. PR #22 deliberately creates
only counterfactual proposals while Stockfish anchor remains the sole outward
authority.
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
class DecisionAuthorization:
    """Separate authority type. PR #22 never grants one."""

    authorized: bool
    move: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.authorized:
            raise DecisionError(
                "counterfactual milestone does not permit DecisionAuthorization"
            )
        if self.move is not None:
            raise DecisionError("denied DecisionAuthorization cannot carry a move")
        if not isinstance(self.reason, str) or not self.reason:
            raise DecisionError("DecisionAuthorization reason must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "move": self.move,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CounterfactualDecision:
    """Proposal plus the later anchor comparison; never a correctness label."""

    proposal: DecisionProposal
    anchor_move: str
    proposal_matches_anchor: bool | None
    would_change_outward_move: bool | None
    outward_authority: str = "stockfish-anchor"

    def __post_init__(self) -> None:
        _canonical_move(self.anchor_move, "anchor move")
        if self.outward_authority != "stockfish-anchor":
            raise DecisionError(
                "counterfactual milestone must preserve stockfish-anchor authority"
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


def denied_counterfactual_authorization() -> DecisionAuthorization:
    return DecisionAuthorization(
        authorized=False,
        move=None,
        reason="PR22 counterfactual-only milestone grants no outward decision authority",
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
