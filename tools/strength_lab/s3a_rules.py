"""Independent S3-A legal-root replay, deliberately outside controller runtime.

Legality is checked with the source-pinned python-chess rules engine, never
inferred from regex root shape, a proposer-written list, or Stockfish CP.
"""
from __future__ import annotations
from typing import Any
from common.search_request import parse_position_command, SearchRequestError
from controller.strength.candidates import reconstruct_proposal
from controller.strength.contracts import require, StrengthContractError
from controller.strength.integrity import strict_load

def legal_roots(position_command: str) -> tuple[str, ...]:
    try:
        import chess
    except ImportError as exc:
        raise StrengthContractError("S3 rules replay needs pinned chess==1.11.2") from exc
    require(chess.__version__ == "1.11.2", "S3 independent rules version changed")
    require(type(position_command) is str and len(position_command)<=8192,
            "S3 full-history command size invalid")
    try:
        position=parse_position_command(position_command)
        require(position.command()==position_command and position.variant=="standard",
                "S3 full command not canonical")
        board=chess.Board(position.base_fen)
        require(board.is_valid(), "S3 illegal initial board")
        for move in position.moves:
            board.push_uci(move)
        require(board.is_valid(), "S3 illegal final board")
    except (SearchRequestError,ValueError,AssertionError,chess.IllegalMoveError) as exc:
        raise StrengthContractError("S3 independent full chess replay failed") from exc
    return tuple(sorted(move.uci() for move in board.legal_moves))

def verify_proposal(raw: object) -> dict[str, Any]:
    proposal=reconstruct_proposal(raw)
    observed=legal_roots(proposal.position_command)
    require(set(proposal.allowed_legal_roots)==set(observed) and
            len(proposal.allowed_legal_roots)==len(observed),
            "S3 producer legal-root witness not complete/correct")
    return {"schema_version":1,"kind":"strength_s3a_independent_rules_verdict_v1",
            "source_only":True,"legal_history_verified":True,
            "proposal_digest":proposal.proposal_digest,
            "legal_root_count":len(observed),
            "proposer_family":proposal.family,
            "strength_qualified":False,"resource_qualified":False,
            "quality_qualified":False,"work_dispatch_authorized":False,
            "move_authorized":False}

def verify_proposal_file(path) -> dict[str, Any]:
    return verify_proposal(strict_load(path))
