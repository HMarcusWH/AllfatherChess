"""Bounded, immutable, explicitly discriminated STRENGTH-S2 records.

Do not deserialize these into the legacy three-candidate G3/J12 dictionaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
MOVE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")
BESTMOVE = re.compile(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000)(?: ponder ([a-h][1-8][a-h][1-8][qrbn]?))?\Z")
MODE = "observe_only"
POLICY = "strength_s2_incumbent_observe_v1"


class StrengthContractError(ValueError):
    """A claim fails closed instead of being guessed from partial evidence."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise StrengthContractError(reason)


def canonical(data: Any) -> bytes:
    """No floats outside finite values, no dynamic key coercion, no NaN."""
    def visit(item: Any, depth: int = 0) -> None:
        require(depth <= 12, "strength record nesting exceeds bound")
        if item is None or isinstance(item, (str, bool)):
            if isinstance(item, str):
                require(len(item) <= 8192, "strength string length cap")
            return
        if type(item) is int:
            require(abs(item) <= (2**63 - 1), "strength integer outside int64")
            return
        if type(item) is float:
            require(math.isfinite(item), "nonfinite strength value")
            return
        if isinstance(item, (list, tuple)):
            require(len(item) <= 8192, "strength list size cap")
            for v in item:
                visit(v, depth + 1)
            return
        if isinstance(item, dict):
            require(len(item) <= 256, "strength object key cap")
            for k, v in item.items():
                require(isinstance(k, str) and len(k) <= 100, "invalid key")
                visit(v, depth + 1)
            return
        raise StrengthContractError("unsupported strength value type")
    visit(data)
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _sha(value: str, name: str) -> None:
    require(isinstance(value, str) and SHA.fullmatch(value) is not None, f"{name} must be SHA256")


def _positive(value: int, name: str) -> None:
    require(type(value) is int and value > 0, f"{name} must be positive integer")


@dataclass(frozen=True)
class TurnKey:
    session_id: str
    generation: int
    history_sha256: str
    side_to_move: str
    variant: str
    restriction_sha256: str
    search_nonce: str

    def __post_init__(self) -> None:
        require(isinstance(self.session_id, str) and re.fullmatch(r"[0-9a-f]{32}", self.session_id) is not None,
                "turn session UUID must be 32 hex")
        _positive(self.generation, "generation")
        _sha(self.history_sha256, "full history")
        _sha(self.restriction_sha256, "external root restriction")
        require(self.side_to_move in ("w", "b") and self.variant == "standard", "S2 supports standard chess only")
        require(isinstance(self.search_nonce, str) and re.fullmatch(r"[0-9a-f]{32}", self.search_nonce) is not None,
                "search nonce must be 128-bit lowercase hex")

    def as_dict(self) -> dict:
        return {"schema_version": 1, "kind": "strength_turn_key_v1",
                "session_id": self.session_id, "generation": self.generation,
                "history_sha256": self.history_sha256, "side_to_move": self.side_to_move,
                "variant": self.variant, "restriction_sha256": self.restriction_sha256,
                "search_nonce": self.search_nonce}

    @property
    def turn_digest(self) -> str:
        return digest(self.as_dict())


@dataclass(frozen=True)
class SearchIdentity:
    turn: TurnKey
    source_commit: str
    profile_sha256: str
    binary_sha256: str
    options_sha256: str

    def __post_init__(self) -> None:
        require(isinstance(self.source_commit, str) and COMMIT.fullmatch(self.source_commit) is not None,
                "source commit must be full literal 40-character SHA")
        for name in ("profile_sha256", "binary_sha256", "options_sha256"):
            _sha(getattr(self, name), name)

    def as_dict(self) -> dict:
        return {"schema_version": 1, "kind": "strength_search_identity_v1",
                "turn": self.turn.as_dict(), "source_commit": self.source_commit,
                "profile_sha256": self.profile_sha256, "binary_sha256": self.binary_sha256,
                "options_sha256": self.options_sha256}

    @property
    def search_digest(self) -> str:
        return digest(self.as_dict())


@dataclass(frozen=True)
class IncumbentCheckpoint:
    search_digest: str
    root_epoch: int
    progress_seq: int
    move: str
    depth: int
    nodes: int
    exact_score_cp: int | None
    exact_score_mate: int | None
    observed_ns: int

    def __post_init__(self) -> None:
        _sha(self.search_digest, "search identity")
        require(type(self.root_epoch) is int and self.root_epoch >= 1, "invalid root epoch")
        _positive(self.progress_seq, "progress_seq")
        require(isinstance(self.move, str) and MOVE.fullmatch(self.move) is not None, "invalid incumbent move")
        _positive(self.depth, "incumbent depth")
        require(type(self.nodes) is int and self.nodes >= 0, "invalid node count")
        for field in ("exact_score_cp", "exact_score_mate"):
            x = getattr(self, field)
            require(x is None or type(x) is int and abs(x) < 1000000, "invalid score integer")
        require(self.exact_score_cp is None or self.exact_score_mate is None, "mixed mate/cp score")
        require(type(self.observed_ns) is int and self.observed_ns >= 0, "invalid monotonic timestamp")

    def as_dict(self) -> dict:
        return {"schema_version": 1, "kind": "strength_incumbent_checkpoint_v1",
                "search_digest": self.search_digest, "root_epoch": self.root_epoch,
                "progress_seq": self.progress_seq, "move": self.move,
                "depth": self.depth, "nodes": self.nodes,
                "exact_score_cp": self.exact_score_cp,
                "exact_score_mate": self.exact_score_mate, "observed_ns": self.observed_ns}


@dataclass(frozen=True)
class FinalSelection:
    """Explicit new type: never impersonates legacy HYBRID/Authorization."""
    search_digest: str
    anchor_line: str
    chosen_line: str
    incumbent_epoch: int
    replaces_anchor: bool = False
    would_authorize: bool = False
    mode: str = MODE
    kind: str = "strength_final_selection_v1"

    def __post_init__(self) -> None:
        _sha(self.search_digest, "search identity")
        require(self.kind == "strength_final_selection_v1" and self.mode == MODE,
                "S2 permits only observe_only selection kind")
        require(type(self.replaces_anchor) is bool and not self.replaces_anchor, "S2 may not replace anchor")
        require(type(self.would_authorize) is bool and not self.would_authorize, "S2 may not claim authorization")
        require(self.chosen_line == self.anchor_line and BESTMOVE.fullmatch(self.anchor_line) is not None
                and self.anchor_line != "bestmove 0000", "S2 must publish real anchor bestmove")
        require(type(self.incumbent_epoch) is int and self.incumbent_epoch >= 1, "missing incumbent epoch")

    def as_dict(self) -> dict:
        return {"schema_version": 1, "kind": self.kind, "mode": self.mode,
                "search_digest": self.search_digest, "anchor_line": self.anchor_line,
                "chosen_line": self.chosen_line, "incumbent_epoch": self.incumbent_epoch,
                "replaces_anchor": False, "would_authorize": False}
