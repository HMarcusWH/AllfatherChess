"""S3-B: independent, same-Stockfish two-root comparison evidence.

A complete comparison is *research evidence*, NEVER a quality witness or
permission to replace the native anchor. No three-candidate G3/J12 adaptation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import threading
import time
from typing import Any

from common.search_request import parse_position_command, SearchRequestError
from .contracts import (SearchIdentity, IncumbentCheckpoint, StrengthContractError,
                        digest, require, MOVE, SHA, BESTMOVE)
from .candidates import CandidatePairV1
from .root_frames import parse_info

MAX_INFO_EVENTS=8192
NONCE=re.compile(r"[0-9a-f]{32}\Z")


def _sha(value:object,label:str)->None:
    require(type(value) is str and SHA.fullmatch(value) is not None,
            "S3 comparator "+label+" must be SHA256")


def legal_pv(position_command:str, raw:str, expected_root:str)->bool:
    """Replay the ENTIRE actual UCI PV under pinned independent chess rules."""
    try:
        import chess
        require(chess.__version__=="1.11.2",
                "S3 comparison requires pinned chess==1.11.2")
        request=parse_position_command(position_command,variant="standard")
        require(request.command()==position_command,"noncanonical S3 position")
        board=chess.Board(request.base_fen)
        require(board.is_valid(),"invalid S3 base FEN")
        for text in request.moves:
            board.push_uci(text)
        require(board.is_valid(),"invalid S3 current position")
        tokens=raw.split()
        require(tokens.count("pv")==1,"ambiguous S3 PV")
        moves=tokens[tokens.index("pv")+1:]
        require(bool(moves) and moves[0]==expected_root and len(moves)<=256,
                "S3 PV does not start at requested root")
        for item in moves:
            require(MOVE.fullmatch(item) is not None,"malformed S3 PV move")
            board.push_uci(item)
        return True
    except (SearchRequestError, ValueError, AssertionError, StrengthContractError,
            chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
        return False


@dataclass(frozen=True)
class ComparisonRequestV1:
    pair: CandidatePairV1
    comparator_search: SearchIdentity
    comparator_engine_sha256: str
    comparator_nnue_sha256: str
    source_bundle_manifest_sha256: str
    round_number: int
    native_limit_nodes: int
    round_nonce: str
    tt_policy: str="within_comparison_warm_v1"

    def __post_init__(self)->None:
        require(type(self.pair) is CandidatePairV1 and
                type(self.comparator_search) is SearchIdentity,
                "S3 comparator needs typed pair and search identity")
        p=self.pair.proposal
        require(self.comparator_search.turn==p.turn and
                self.comparator_search.source_commit==p.anchor_search.source_commit and
                self.comparator_search.profile_sha256==p.anchor_search.profile_sha256,
                "S3 comparison foreign source/turn")
        require(self.comparator_search.binary_sha256==
                p.anchor_search.binary_sha256==self.comparator_engine_sha256,
                "S3 comparator must run exact original anchor Stockfish executable")
        _sha(self.comparator_nnue_sha256,"NNUE")
        _sha(self.source_bundle_manifest_sha256,"bundle")
        require(type(self.round_number) is int and 1<=self.round_number<=3,
                "S3 comparator round outside frozen policy")
        require(type(self.native_limit_nodes) is int and
                1024<=self.native_limit_nodes<=2**28,
                "S3 node limit outside bounded comparator domain")
        require(type(self.round_nonce) is str and NONCE.fullmatch(self.round_nonce) is not None,
                "missing S3 comparison nonce")
        require(self.tt_policy=="within_comparison_warm_v1",
                "S3 comparator TT provenance changed")
        require(set(self.pair.ordered_roots)=={p.incumbent.move,p.move},
                "S3 comparator omitted original incumbent")

    @property
    def go_command(self)->str:
        first,second=self.pair.ordered_roots
        return f"go nodes {self.native_limit_nodes} searchmoves {first} {second}"

    @property
    def request_digest(self)->str:
        return digest(self.as_dict())

    def as_dict(self)->dict[str,Any]:
        return {"schema_version":1,"kind":"strength_two_root_request_v1",
                "pair_digest":self.pair.pair_digest,
                "anchor_search_digest":self.pair.proposal.anchor_search.search_digest,
                "comparator_search":self.comparator_search.as_dict(),
                "position_command":self.pair.proposal.position_command,
                "ordered_roots":list(self.pair.ordered_roots),
                "incumbent_root_epoch":self.pair.proposal.incumbent.root_epoch,
                "comparator_engine_sha256":self.comparator_engine_sha256,
                "comparator_nnue_sha256":self.comparator_nnue_sha256,
                "source_bundle_manifest_sha256":self.source_bundle_manifest_sha256,
                "round_number":self.round_number,"round_nonce":self.round_nonce,
                "native_limit_nodes":self.native_limit_nodes,
                "go_command":self.go_command,"tt_policy":self.tt_policy,
                "authority":{"outward_move":False,"quality":False,
                             "work_dispatch":False}}


def _scored_frame(req:ComparisonRequestV1,line:str)->dict|None:
    frame=parse_info(line)
    if frame is None or frame.multipv not in (1,2) or not frame.exact:
        return None
    if frame.cp is None and frame.mate is None:
        return None
    if frame.move not in req.pair.ordered_roots:
        return None
    if "multipv" not in line.split() or line.split().count("multipv")!=1:
        return None
    tokens=line.split()
    try:
        ix=tokens.index("time")
        native_time=int(tokens[ix+1])
    except (ValueError,IndexError):
        return None
    if native_time<0 or frame.nodes<=0:
        return None
    if not legal_pv(req.pair.proposal.position_command,line,frame.move):
        return None
    return {"root":frame.move,"rank":frame.multipv,"depth":frame.depth,
            "nodes":frame.nodes,"time_ms":native_time,
            "score_kind":"cp" if frame.cp is not None else "mate",
            "score":frame.cp if frame.cp is not None else frame.mate,
            "raw_sha256":hashlib.sha256(line.encode()).hexdigest()}


def reconstruct_comparison(req:ComparisonRequestV1,
                           observations:list[dict],terminal:str,
                           *, completed_ns:int|None=None)->dict[str,Any]:
    """Reparse untrusted raw transcript independently, never trust producer PASS.

    Candidate frame = rank1 followed by rank2 at same depth and monotone
    elapsed native time/nodes. An unrelated rank or second rank1 invalidates
    pending. Only a completed terminal may finalize the latest complete frame.
    """
    require(type(req) is ComparisonRequestV1,"typed S3 comparator request required")
    require(type(observations) is list and len(observations)<=MAX_INFO_EVENTS,
            "S3 comparison transcript overflow")
    m=BESTMOVE.fullmatch(terminal) if type(terminal) is str else None
    require(m is not None and m.group(1) in req.pair.ordered_roots,
            "S3 comparison missing legal terminal from its two roots")
    require(type(completed_ns) is int and completed_ns>=0,
            "S3 comparator missing real terminal event timestamp")
    pending=None
    complete=None
    errors=[]
    previous_ns=-1
    for index,entry in enumerate(observations):
        require(type(entry) is dict and set(entry)=={"line","ns"} and
                type(entry["line"]) is str and len(entry["line"])<=4096 and
                type(entry["ns"]) is int and entry["ns"]>=previous_ns and
                entry["ns"]<=completed_ns,
                "S3 comparison observation malformed/out of clock order")
        previous_ns=entry["ns"]
        line=entry["line"]
        if not line.startswith("info "):
            raise StrengthContractError("non-info line injected into comparison transcript")
        frame=_scored_frame(req,line)
        tokens=line.split()
        if frame is None:
            if "multipv" in tokens or "pv" in tokens:
                pending=None
                errors.append({"event_index":index,"reason":"NONQUALIFYING_FRAME"})
            continue
        frame["observed_ns"]=entry["ns"]
        frame["event_index"]=index
        if frame["rank"]==1:
            pending=frame
        else:
            if pending is None:
                errors.append({"event_index":index,"reason":"ORPHAN_RANK2"})
                continue
            if (pending["depth"]==frame["depth"] and
                pending["nodes"]<=frame["nodes"] and
                pending["time_ms"]<=frame["time_ms"] and
                pending["root"]!=frame["root"] and
                pending["score_kind"]==frame["score_kind"] and
                {pending["root"],frame["root"]}==set(req.pair.ordered_roots)):
                complete={"rank1":pending,"rank2":frame,
                          "depth":frame["depth"],
                          "frame_completed_ns":entry["ns"],
                          "native_nodes":frame["nodes"],
                          "native_time_ms":frame["time_ms"],
                          "score_kind":frame["score_kind"]}
            else:
                errors.append({"event_index":index,"reason":"INCOMPLETE_PAIR"})
            pending=None
    disposition=("COMPLETE_CP" if complete is not None and
                 complete["score_kind"]=="cp" else
                 "MATE_FALLBACK" if complete is not None else "NO_COMPARISON")
    return {"schema_version":1,"kind":"strength_two_root_round_v1",
            "request":req.as_dict(),"request_sha256":req.request_digest,
            "terminal_line":terminal,"terminal_ns":completed_ns,
            "observations":observations,"observations_sha256":digest(observations),
            "frame":complete,"rejections":errors[:128],
            "rejection_overflow":max(0,len(errors)-128),
            "disposition":disposition,
            "native_nodes_overshot":bool(complete is not None and
                                        complete["native_nodes"]>req.native_limit_nodes),
            "execution_integrity":"RECORDED",
            "resource_qualified":False,
            "quality_qualified":False,"strength_qualified":False,
            "equal_compute":False,"replacement_authorized":False}


class ComparatorRoundCollector:
    """Single-reader callback sink; terminal callback never waits or performs IO."""
    def __init__(self,req:ComparisonRequestV1):
        self.req=req
        self._lock=threading.Lock()
        self._observations=[]
        self._terminal=None
        self._terminal_ns=None
        self._error=None
        self._event=threading.Event()

    def on_info(self,token:int,line:str)->None:
        if token!=self.req.round_number:return
        with self._lock:
            if self._terminal is not None:return
            if len(self._observations)>=MAX_INFO_EVENTS or len(line)>4096:
                self._error="S3 comparison info stream overflow"
            else:
                self._observations.append({"line":line,"ns":time.monotonic_ns()})

    def on_complete(self,token:int,line:str)->None:
        if token!=self.req.round_number:return
        with self._lock:
            if self._terminal is not None:
                self._error="duplicate S3 comparator bestmove"
            else:
                self._terminal=line
                self._terminal_ns=time.monotonic_ns()
            self._event.set()

    def wait(self,timeout:float)->dict:
        require(type(timeout) in (int,float) and
                0<float(timeout)<=300,"unbounded S3 comparator timeout")
        if not self._event.wait(float(timeout)):
            raise TimeoutError("S3 comparator never reached terminal")
        with self._lock:
            if self._error is not None:
                raise StrengthContractError(self._error)
            return reconstruct_comparison(self.req,list(self._observations),
                                          self._terminal,completed_ns=self._terminal_ns)
