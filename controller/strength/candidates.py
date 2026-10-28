"""S3-A source contracts: one specialist nomination, never a chess vote.

This source-only layer does not launch helpers, evaluate move quality or
authorize external move replacement. Full-history chess legality is verified
separately by the independent rules adapter in S3-B.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from common.search_request import parse_position_command, SearchRequestError
from .contracts import (TurnKey, SearchIdentity, IncumbentCheckpoint,
                        StrengthContractError, require, digest, MOVE, SHA)

FAMILIES = {"lc0": "lc0.uci_nodes", "reckless": "reckless.uci_nodes"}
MAX_LEGAL_ROOTS = 256


def _sha(value: object, label: str) -> None:
    require(type(value) is str and SHA.fullmatch(value) is not None, label + " requires SHA256")


def _roots(value: object, label: str, *, allow_empty: bool) -> tuple[str, ...]:
    require(type(value) is tuple and (allow_empty or bool(value)) and
            len(value) <= MAX_LEGAL_ROOTS, label + " length invalid")
    require(all(type(move) is str and MOVE.fullmatch(move) is not None for move in value),
            label + " must contain UCI roots")
    require(len(set(value)) == len(value), label + " contains duplicate roots")
    return value


@dataclass(frozen=True)
class CandidateProposalV1:
    """Typed nomination with explicit source and legal-root witness domain.

    legal_roots is a *claimed* oracle snapshot, not proof of legality.
    Independent legal replay and measured WorkGrant authentication are S3-B.
    """
    anchor_search: SearchIdentity
    proposer_search: SearchIdentity
    incumbent: IncumbentCheckpoint
    position_command: str
    allowed_legal_roots: tuple[str, ...]
    external_searchmoves: tuple[str, ...]
    family: str
    move: str
    native_work_semantics: str
    native_work_count: int
    grant_digest: str
    settlement_sha256: str
    observed_ns: int
    proposer_network_sha256: str | None = None

    def __post_init__(self) -> None:
        require(type(self.anchor_search) is SearchIdentity and
                type(self.proposer_search) is SearchIdentity and
                type(self.incumbent) is IncumbentCheckpoint,
                "S3 proposal needs typed source searches and incumbent")
        turn = self.anchor_search.turn
        require(self.proposer_search.turn == turn and
                self.proposer_search.source_commit == self.anchor_search.source_commit and
                self.proposer_search.profile_sha256 == self.anchor_search.profile_sha256,
                "S3 proposer source/turn/profile mismatch")
        require(self.incumbent.search_digest == self.anchor_search.search_digest and
                self.incumbent.root_epoch > 0,
                "S3 proposal not bound to real incumbent search")
        require(type(self.position_command) is str and
                0 < len(self.position_command) <= 8192 and
                self.position_command == self.position_command.strip() and
                hashlib.sha256(self.position_command.encode("utf-8")).hexdigest()
                == turn.history_sha256,
                "S3 proposal full-history identity mismatch")
        try:
            position = parse_position_command(self.position_command, variant=turn.variant)
            require(position.command() == self.position_command and
                    position.side_to_move == turn.side_to_move,
                    "S3 proposal position syntax/side mismatch")
        except SearchRequestError as exc:
            raise StrengthContractError("invalid S3 full-history command") from exc
        legal = _roots(self.allowed_legal_roots, "legal roots", allow_empty=False)
        restricted = _roots(self.external_searchmoves, "external searchmoves", allow_empty=True)
        require(turn.restriction_sha256 ==
                digest({"searchmoves": list(restricted)}),
                "S3 proposer external root restriction hash mismatch")
        allowed = set(legal)
        require(self.incumbent.move in allowed and
                type(self.move) is str and self.move in allowed and
                self.move != self.incumbent.move and
                (not restricted or
                 self.move in restricted and self.incumbent.move in restricted),
                "S3 must nominate a different legal, permitted challenger")
        require(self.family in FAMILIES and
                self.native_work_semantics == FAMILIES[self.family] and
                type(self.native_work_count) is int and
                0 < self.native_work_count < 2**63,
                "S3 foreign/inconsistent native work semantics")
        _sha(self.grant_digest, "proposal work grant")
        _sha(self.settlement_sha256, "proposal resource settlement")
        require(type(self.observed_ns) is int and
                self.observed_ns >= self.incumbent.observed_ns and
                self.observed_ns >= 0,
                "S3 proposal observed before incumbent")
        if self.family == "lc0":
            _sha(self.proposer_network_sha256, "LC0 network")
        else:
            require(self.proposer_network_sha256 is None,
                    "Reckless cannot carry a foreign neural-network identity")

    @property
    def turn(self) -> TurnKey:
        return self.anchor_search.turn

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "kind": "strength_candidate_proposal_v1",
            "anchor_search": self.anchor_search.as_dict(),
            "proposer_search": self.proposer_search.as_dict(),
            "incumbent": self.incumbent.as_dict(),
            "position_command": self.position_command,
            "allowed_legal_roots": list(self.allowed_legal_roots),
            "external_searchmoves": list(self.external_searchmoves),
            "family": self.family, "move": self.move,
            "native_work_semantics": self.native_work_semantics,
            "native_work_count": self.native_work_count,
            "grant_digest": self.grant_digest,
            "settlement_sha256": self.settlement_sha256,
            "observed_ns": self.observed_ns,
            "proposer_network_sha256": self.proposer_network_sha256,
            "authority": {"outward_move": False, "quality": False,
                          "work_dispatch": False},
        }

    @property
    def proposal_digest(self) -> str:
        return digest(self.as_dict())


@dataclass(frozen=True)
class CandidatePairV1:
    """Two-root *input candidate* only. No comparison score or authority."""
    proposal: CandidateProposalV1
    current_incumbent: IncumbentCheckpoint
    ordered_roots: tuple[str, str]

    def __post_init__(self) -> None:
        require(type(self.proposal) is CandidateProposalV1 and
                type(self.current_incumbent) is IncumbentCheckpoint,
                "invalid S3 pair references")
        p = self.proposal
        require(self.current_incumbent.search_digest == p.anchor_search.search_digest and
                self.current_incumbent.root_epoch == p.incumbent.root_epoch and
                self.current_incumbent.move == p.incumbent.move and
                self.current_incumbent.progress_seq >= p.incumbent.progress_seq,
                "S3 pair uses an obsolete incumbent epoch/root")
        require(type(self.ordered_roots) is tuple and len(self.ordered_roots) == 2
                and set(self.ordered_roots) == {p.incumbent.move, p.move},
                "S3 comparison requires exactly the incumbent and challenger")
        ordered = _ordered_pair(p)
        require(self.ordered_roots == ordered,
                "S3 pair order differs from source-deterministic turn key")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "kind": "strength_comparison_input_pair_v1",
            "proposal_digest": self.proposal.proposal_digest,
            "anchor_search_digest": self.proposal.anchor_search.search_digest,
            "history_sha256": self.proposal.turn.history_sha256,
            "current_incumbent": self.current_incumbent.as_dict(),
            "ordered_roots": list(self.ordered_roots),
            "tt_policy": "not_dispatched_source_only",
            "authority": {"outward_move": False, "comparison": False,
                          "work_dispatch": False},
        }

    @property
    def pair_digest(self) -> str:
        return digest(self.as_dict())


def _ordered_pair(proposal: CandidateProposalV1) -> tuple[str, str]:
    order = (proposal.incumbent.move, proposal.move)
    return order if int(proposal.turn.turn_digest[-2:], 16) % 2 == 0 else order[::-1]


def make_pair(proposal: CandidateProposalV1,
              current_incumbent: IncumbentCheckpoint) -> CandidatePairV1:
    require(type(proposal) is CandidateProposalV1,
            "S3 requires a typed specialist proposal")
    return CandidatePairV1(proposal, current_incumbent, _ordered_pair(proposal))


def reconstruct_proposal(raw: object) -> CandidateProposalV1:
    """Strict source-only replay; never trust a producer-written passed bit."""
    require(type(raw) is dict and set(raw) ==
            set(CandidateProposalV1.__dataclass_fields__) |
            {"schema_version", "kind", "authority"},
            "unknown/missing S3 proposal fields")
    require(raw["schema_version"] == 1 and
            raw["kind"] == "strength_candidate_proposal_v1" and
            raw["authority"] == {"outward_move": False, "quality": False,
                                 "work_dispatch": False},
            "S3 proposal claims unsupported authority")
    def turn(data: object) -> TurnKey:
        require(type(data) is dict and
                set(data) == set(TurnKey.__dataclass_fields__) |
                {"kind", "schema_version"},
                "S3 embedded turn malformed")
        obj = TurnKey(**{k: data[k] for k in TurnKey.__dataclass_fields__})
        require(obj.as_dict() == data, "S3 turn disagrees with canonical serializer")
        return obj
    def identity(data: object) -> SearchIdentity:
        require(type(data) is dict and set(data) ==
                set(SearchIdentity.__dataclass_fields__) | {"schema_version", "kind"},
                "S3 embedded search malformed")
        obj = SearchIdentity(turn(data["turn"]), data["source_commit"],
                             data["profile_sha256"], data["binary_sha256"],
                             data["options_sha256"])
        require(obj.as_dict() == data, "S3 search identity malformed")
        return obj
    d = raw["incumbent"]
    require(type(d) is dict and set(d) ==
            set(IncumbentCheckpoint.__dataclass_fields__) | {"schema_version", "kind"},
            "S3 embedded incumbent malformed")
    inc = IncumbentCheckpoint(**{k: d[k] for k in IncumbentCheckpoint.__dataclass_fields__})
    require(inc.as_dict() == d, "S3 incumbent does not reconstruct")
    proposal = CandidateProposalV1(
        anchor_search=identity(raw["anchor_search"]),
        proposer_search=identity(raw["proposer_search"]),
        incumbent=inc, position_command=raw["position_command"],
        allowed_legal_roots=tuple(raw["allowed_legal_roots"]),
        external_searchmoves=tuple(raw["external_searchmoves"]),
        family=raw["family"], move=raw["move"],
        native_work_semantics=raw["native_work_semantics"],
        native_work_count=raw["native_work_count"],
        grant_digest=raw["grant_digest"],
        settlement_sha256=raw["settlement_sha256"],
        observed_ns=raw["observed_ns"],
        proposer_network_sha256=raw["proposer_network_sha256"],
    )
    require(proposal.as_dict() == raw, "S3 proposal canonical roundtrip failed")
    return proposal
