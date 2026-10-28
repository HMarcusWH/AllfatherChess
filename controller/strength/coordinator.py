"""Observe-only native Stockfish coordinator, never a second UCI writer.

No specialist processes, comparison, grant, or move replacement exists in S2.
The existing frontend is the *only* stdout owner and stopwatch authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import secrets
import threading
import time

from common.search_request import PositionRequest

from .contracts import (FinalSelection, IncumbentCheckpoint, SearchIdentity,
                        StrengthContractError, TurnKey, BESTMOVE, digest, require)
from .epoch import IncumbentEpochTracker
from .evidence import FlightLog
from .native_clock import NativeClockPlan
from .root_frames import parse_info


@dataclass(frozen=True)
class S2Settings:
    source_commit: str
    profile_sha256: str
    evidence_dir: Path
    max_events_per_turn: int
    mode: str = "observe_only"
    policy: str = "strength_s2_incumbent_observe_v1"

    def __post_init__(self):
        require(self.mode == "observe_only" and self.policy in
                ("strength_s2_incumbent_observe_v1", "strength_e00_incumbent_observe_v1",
                 "strength_s3a_contracts_observe_v1"),
                "S2/E00 permits observe-only; apply/control are future protocols")
        require(re.fullmatch(r"[0-9a-f]{40}",self.source_commit) is not None,"missing exact source")
        require(re.fullmatch(r"[0-9a-f]{64}",self.profile_sha256) is not None,"missing profile SHA")
        require(type(self.max_events_per_turn) is int and 64<=self.max_events_per_turn<=65536,
                "S2 log capacity invalid")


class StrengthCoordinator:
    """Per-session native incumbent state with an immutable final anchor choice."""
    def __init__(self, *, settings:S2Settings, binary_sha256:str,
                 options_sha256:str, session_id:str|None=None):
        self.settings=settings
        self.binary_sha256=binary_sha256
        self.options_sha256=options_sha256
        self.session_id=secrets.token_hex(16) if session_id is None else session_id
        require(re.fullmatch(r"[0-9a-f]{32}",self.session_id) is not None,
                "invalid session")
        self._lock=threading.RLock()
        self._turns: list[tuple[TurnKey, FlightLog]]=[]
        self._tracker: IncumbentEpochTracker | None=None
        self._log: FlightLog | None=None
        self._selection: FinalSelection | None=None
        self._published=False
        self._closed=False

    def begin_turn(self, *,token:int,position:PositionRequest,
                   original_position_command:str,plan:NativeClockPlan) -> None:
        with self._lock:
            require(not self._closed, "coordinator closed")
            require(self._tracker is None or self._published or
                    self._tracker.closed, "previous turn unfinished")
            require(token==plan.generation and plan.position_id==position.position_id,
                    "native clock/position token mismatch")
            require(type(original_position_command) is str
                    and original_position_command.startswith("position ")
                    and len(original_position_command)<8192,
                    "missing full exact position history")
            key=TurnKey(
                session_id=self.session_id,generation=token,
                history_sha256=hashlib.sha256(original_position_command.encode()).hexdigest(),
                side_to_move=position.side_to_move,variant=position.variant,
                restriction_sha256=digest({"searchmoves":list(plan.restriction)}),
                search_nonce=secrets.token_hex(16))
            identity=SearchIdentity(key,self.settings.source_commit,
                                    self.settings.profile_sha256,
                                    self.binary_sha256,self.options_sha256)
            tracker=IncumbentEpochTracker(identity,plan.restriction)
            log=FlightLog(self.settings.max_events_per_turn)
            log.append("begin",{"turn":key.as_dict(),"search":identity.as_dict(),
                                "position_command":original_position_command,
                                "native_time":plan.as_dict()})
            self._tracker=tracker
            self._log=log
            self._turns.append((key,log))
            self._selection=None
            self._published=False

    def on_anchor_info(self, *,token:int,line:str,observed_ns:int|None=None) -> None:
        with self._lock:
            tracker=self._tracker
            if tracker is None or tracker.closed or token!=tracker.identity.turn.generation:
                return
            frame=parse_info(line)
            if frame is None or frame.multipv!=1:
                return
            try:
                c=tracker.note_frame(frame,token=token,
                                     observed_ns=time.monotonic_ns() if observed_ns is None else observed_ns)
            except StrengthContractError as exc:
                assert self._log is not None
                self._log.append("denied_info",{"reason":str(exc)})
                return
            if c is not None:
                assert self._log is not None
                self._log.append("checkpoint",c.as_dict())

    def on_anchor_bestmove(self, *,token:int,line:str,
                           observed_ns:int|None=None) -> FinalSelection:
        with self._lock:
            tracker=self._tracker
            require(tracker is not None and tracker.identity.turn.generation==token
                    and not tracker.closed, "stale/duplicate terminal bestmove")
            match=BESTMOVE.fullmatch(line)
            require(match is not None and match.group(1)!="0000",
                    "missing true terminal Stockfish bestmove")
            checkpoint=tracker.terminal(match.group(1),token=token,
                                        observed_ns=time.monotonic_ns() if observed_ns is None else observed_ns)
            selected=FinalSelection(
                search_digest=tracker.identity.search_digest,
                anchor_line=line,chosen_line=line,
                incumbent_epoch=checkpoint.root_epoch)
            self._selection=selected
            assert self._log is not None
            self._log.append("terminal",{"checkpoint":checkpoint.as_dict(),
                                         "selection":selected.as_dict()})
            return selected

    def note_published(self, *,token:int,selection:FinalSelection,
                       published_ns:int|None=None)->None:
        """Invoked AFTER the single stdout syscall: bounded memory only, no IO."""
        with self._lock:
            tracker=self._tracker
            if (tracker is None or tracker.identity.turn.generation!=token
                    or self._published or self._selection!=selection):
                if self._log is not None:
                    self._log.append("denied_publication",{"reason":"stale/duplicate/mismatched selection"})
                return
            self._published=True
            assert self._log is not None
            self._log.append("published",{"chosen_line":selection.chosen_line,
                                          "at_ns":time.monotonic_ns() if published_ns is None else published_ns,
                                          "epoch":selection.incumbent_epoch})
 
    def mark_observation_failure(self, *,token:int,reason:str) -> None:
        """Mark evidence NEGATIVE; cannot revoke a previously published anchor.

        Called on a bestmove path only after stdout has crossed the writer.
        No disk I/O and no new move authorization is possible here.
        """
        with self._lock:
            if self._tracker is None or self._tracker.identity.turn.generation!=token:
                return
            if self._log is not None:
                # The independent verifier refuses every flight with failure.
                self._log.failure=str(reason)[:240]

    def abort_generation(self, *,token:int,reason:str) -> None:
        with self._lock:
            if self._tracker is None or self._tracker.identity.turn.generation!=token:
                return
            if self._published:
                return  # Bytes already crossed stdout: immutable successful history.
            if self._log is not None:
                self._log.append("abort",{"reason":str(reason)[:240]})
            self._tracker.closed=True

    def close(self) -> None:
        """Flush records after the protocol shutdown, never inside bestmove."""
        with self._lock:
            if self._closed:
                return
            self._closed=True
            snapshots=list(self._turns)
        for key,log in snapshots:
            target=self.settings.evidence_dir / (
                f"s2-{key.session_id}-{key.generation:08d}-{key.search_nonce}.json")
            log.write(target)
