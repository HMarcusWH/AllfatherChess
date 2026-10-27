"""Incumbent epoch/progress tracking with ABA and stale-generation rejection."""
from __future__ import annotations

from .contracts import IncumbentCheckpoint, SearchIdentity, StrengthContractError, require, MOVE
from .root_frames import UciFrame


class IncumbentEpochTracker:
    """A single search identity. A -> B -> A increments epoch twice."""

    def __init__(self, identity: SearchIdentity,
                 permitted_roots: tuple[str, ...] = ()) -> None:
        self.identity = identity
        self.permitted_roots = tuple(permitted_roots)
        require(len(set(self.permitted_roots)) == len(self.permitted_roots), "duplicate root restriction")
        self.root_epoch = 0
        self.progress_seq = 0
        self.incumbent: str | None = None
        self.current: IncumbentCheckpoint | None = None
        self.closed = False
        self.info_seen = 0

    def eligible(self, move: str) -> bool:
        return bool(MOVE.fullmatch(move) and
                    (not self.permitted_roots or move in self.permitted_roots))

    def note_frame(self, frame: UciFrame, *, token: int, observed_ns: int) -> IncumbentCheckpoint | None:
        require(not self.closed, "closed incumbent tracker")
        require(token == self.identity.turn.generation, "stale incumbent search token")
        self.info_seen += 1
        if frame.multipv != 1 or not self.eligible(frame.move):
            return None
        require(frame.depth > 0, "missing root report depth")
        self.root_epoch += int(frame.move != self.incumbent)
        self.progress_seq += 1
        self.incumbent = frame.move
        self.current = IncumbentCheckpoint(
            search_digest=self.identity.search_digest,
            root_epoch=self.root_epoch,
            progress_seq=self.progress_seq,
            move=frame.move, depth=frame.depth, nodes=frame.nodes,
            exact_score_cp=frame.cp if frame.exact else None,
            exact_score_mate=frame.mate if frame.exact else None,
            observed_ns=observed_ns,
        )
        return self.current

    def terminal(self, move: str, *, token: int, observed_ns: int) -> IncumbentCheckpoint:
        require(not self.closed, "second terminal incumbent event")
        require(token == self.identity.turn.generation, "stale terminal search token")
        require(self.eligible(move), "terminal bestmove is not in allowed legal-root shape/restriction")
        if self.incumbent != move:
            self.root_epoch += 1
        self.progress_seq += 1
        previous = self.current if self.incumbent == move else None
        self.incumbent = move
        self.current = IncumbentCheckpoint(
            search_digest=self.identity.search_digest,
            root_epoch=self.root_epoch,
            progress_seq=self.progress_seq,
            move=move, depth=1 if previous is None else previous.depth,
            nodes=0 if previous is None else previous.nodes,
            exact_score_cp=None if previous is None else previous.exact_score_cp,
            exact_score_mate=None if previous is None else previous.exact_score_mate,
            observed_ns=observed_ns,
        )
        self.closed = True
        return self.current

    def is_current(self, *, epoch: int, move: str, progress_seq: int | None = None) -> bool:
        return bool(self.current is not None and self.root_epoch == epoch
                    and self.incumbent == move
                    and (progress_seq is None or self.progress_seq == progress_seq))
