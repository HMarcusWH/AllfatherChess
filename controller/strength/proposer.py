"""S3-B: bounded real-UCI specialist *nomination* under independent rules.

The raw engine's CP, Q, mate and native nodes are never Stockfish score
certificates. A proposer can nominate one move but has no quality or move
authority. The actual UCI child is owned by the offline lab's UciProcess.
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
                        require, SHA, BESTMOVE, MOVE, digest)
from .candidates import CandidateProposalV1
from .root_frames import parse_info

MAX_OBSERVATIONS=8192
NATIVE_SEEDS={"lc0":(32,128,512),"reckless":(4096,16384,65536)}


@dataclass(frozen=True)
class ProposerRequestV1:
    anchor_search: SearchIdentity
    proposer_search: SearchIdentity
    incumbent: IncumbentCheckpoint
    position_command: str
    legal_roots: tuple[str,...]
    external_roots: tuple[str,...]
    family: str
    engine_sha256: str
    network_sha256: str|None
    native_limit_nodes: int
    proposal_nonce: str

    def __post_init__(self)->None:
        require(type(self.anchor_search) is SearchIdentity and
                type(self.proposer_search) is SearchIdentity and
                type(self.incumbent) is IncumbentCheckpoint,
                "typed S3 proposer and incumbent required")
        require(self.proposer_search.turn==self.anchor_search.turn and
                self.proposer_search.source_commit==self.anchor_search.source_commit and
                self.proposer_search.profile_sha256==self.anchor_search.profile_sha256,
                "foreign S3 proposer source/turn")
        require(self.incumbent.search_digest==self.anchor_search.search_digest and
                self.incumbent.root_epoch>0,
                "S3 proposer missing matching incumbent")
        require(type(self.family) is str and self.family in NATIVE_SEEDS and
                type(self.native_limit_nodes) is int and
                self.native_limit_nodes in NATIVE_SEEDS[self.family],
                "S3 proposer native budget not frozen")
        require(self.proposer_search.binary_sha256==self.engine_sha256 and
                type(self.engine_sha256) is str and SHA.fullmatch(self.engine_sha256) is not None,
                "S3 actual proposer executable digest mismatch")
        if self.family=="lc0":
            require(type(self.network_sha256) is str and
                    SHA.fullmatch(self.network_sha256) is not None,
                    "LC0 real network identity missing")
        else:
            require(self.network_sha256 is None,"Reckless may not claim LC0 network")
        require(type(self.proposal_nonce) is str and
                re.fullmatch("[0-9a-f]{32}",self.proposal_nonce) is not None,
                "S3 proposal nonce missing")
        require(type(self.position_command) is str and
                len(self.position_command)<=8192 and
                self.position_command==self.position_command.strip() and
                hashlib.sha256(self.position_command.encode()).hexdigest()==
                self.anchor_search.turn.history_sha256,
                "S3 proposal full history not bound")
        try:
            parsed=parse_position_command(self.position_command)
            require(parsed.command()==self.position_command and
                    parsed.side_to_move==self.anchor_search.turn.side_to_move,
                    "S3 candidate side/position mismatch")
        except SearchRequestError as exc:
            raise StrengthContractError("S3 invalid position command") from exc
        require(type(self.legal_roots) is tuple and
                1<len(self.legal_roots)<=256 and
                len(set(self.legal_roots))==len(self.legal_roots) and
                all(type(x) is str and MOVE.fullmatch(x) is not None
                    for x in self.legal_roots),
                "S3 proposer needs whole legal-root universe witness")
        require(type(self.external_roots) is tuple and
                len(set(self.external_roots))==len(self.external_roots) and
                all(x in self.legal_roots for x in self.external_roots),
                "S3 foreign/duplicate external searchmoves")
        require(self.incumbent.move in self.legal_roots and
                (not self.external_roots or self.incumbent.move in self.external_roots),
                "S3 incumbent outside permitted legal roots")
        require(self.anchor_search.turn.restriction_sha256==
                digest({"searchmoves":list(self.external_roots)}),
                "S3 external root restriction digest mismatch")

    @property
    def search_command(self)->str:
        cmd=f"go nodes {self.native_limit_nodes}"
        # An external constraint is not a modulo root partition. Without
        # searchmoves, the specialist sees the entire position's root universe.
        if self.external_roots:
            cmd+=" searchmoves "+" ".join(self.external_roots)
        return cmd

    def as_dict(self)->dict[str,Any]:
        return {"schema_version":1,"kind":"strength_specialist_request_v1",
                "anchor_search":self.anchor_search.as_dict(),
                "proposer_search":self.proposer_search.as_dict(),
                "incumbent":self.incumbent.as_dict(),
                "position_command":self.position_command,
                "legal_roots":list(self.legal_roots),
                "external_roots":list(self.external_roots),
                "family":self.family,"engine_sha256":self.engine_sha256,
                "network_sha256":self.network_sha256,
                "native_limit_nodes":self.native_limit_nodes,
                "proposal_nonce":self.proposal_nonce,
                "search_command":self.search_command,
                "authority":{"outward_move":False,"work_dispatch":False,
                             "quality":False}}

    @property
    def request_digest(self)->str:
        return digest(self.as_dict())


def reconstruct_proposer(req:ProposerRequestV1, observations:list[dict],
                         terminal:str, *, terminal_ns:int,
                         grant_digest:str, settlement_sha256:str)->dict[str,Any]:
    """Choose a nonincumbent *legal* root from the actual stdout transcript."""
    require(type(req) is ProposerRequestV1 and type(observations) is list and
            len(observations)<=MAX_OBSERVATIONS and
            type(terminal_ns) is int and terminal_ns>=0,"S3 proposer malformed transcript")
    m=BESTMOVE.fullmatch(terminal) if type(terminal) is str else None
    require(m is not None and m.group(1) in req.legal_roots and
            (not req.external_roots or m.group(1) in req.external_roots),
            "S3 proposer did not terminate with a permitted legal root")
    allowed=set(req.legal_roots if not req.external_roots else req.external_roots)
    previous=-1
    pv_frames=[]
    for i,e in enumerate(observations):
        require(type(e) is dict and set(e)=={"line","ns"} and
                type(e["ns"]) is int and previous<=e["ns"]<=terminal_ns and
                type(e["line"]) is str and len(e["line"])<=4096,
                "S3 proposer foreign/unbounded/stale info event")
        previous=e["ns"]
        require(e["line"].startswith("info "),"S3 proposer injected terminal/command")
        f=parse_info(e["line"])
        if f is None or not f.exact or f.move not in allowed or f.nodes<=0:
            continue
        pv_frames.append({"event":i,"observed_ns":e["ns"],"root":f.move,
                          "multipv":f.multipv,"depth":f.depth,"nodes":f.nodes,
                          "score_type":"mate" if f.mate is not None else
                                       "cp" if f.cp is not None else "missing",
                          "raw_sha256":hashlib.sha256(e["line"].encode()).hexdigest()})
    # LC0: highest-ranked legal non-incumbent in the LAST completed report
    # sequence (no CP mixing). If only MPV1 was emitted, bestmove fallback is
    # allowed only when different from incumbent. Reckless: terminal only.
    challenger=None
    if req.family=="lc0":
        candidates=sorted((x for x in pv_frames
                           if x["root"]!=req.incumbent.move),
                          key=lambda x:(-x["event"],x["multipv"]))
        if candidates:
            challenger=candidates[0]["root"]
    if challenger is None and m.group(1)!=req.incumbent.move:
        challenger=m.group(1)
    max_nodes=max((f["nodes"] for f in pv_frames),default=0)
    need_ok=challenger is not None and max_nodes>0
    proposal=None
    if need_ok:
        proposal=CandidateProposalV1(
            anchor_search=req.anchor_search,
            proposer_search=req.proposer_search,
            incumbent=req.incumbent,
            position_command=req.position_command,
            allowed_legal_roots=req.legal_roots,
            external_searchmoves=req.external_roots,
            family=req.family,move=challenger,
            native_work_semantics=req.family+".uci_nodes",
            native_work_count=max_nodes,
            grant_digest=grant_digest,settlement_sha256=settlement_sha256,
            observed_ns=max(terminal_ns,req.incumbent.observed_ns),
            proposer_network_sha256=req.network_sha256)
    return {"schema_version":1,"kind":"strength_proposer_result_v1",
            "request":req.as_dict(),"request_sha256":req.request_digest,
            "terminal":terminal,"terminal_ns":terminal_ns,
            "observations":observations,"observations_sha256":digest(observations),
            "native_reported_nodes":max_nodes,
            "native_limit_overshot":max_nodes>req.native_limit_nodes,
            "proposal":None if proposal is None else proposal.as_dict(),
            "status":"PROPOSED" if proposal is not None else "NO_ALTERNATIVE",
            "resource_qualified":False,"quality_qualified":False,
            "strength_qualified":False,"move_authorized":False}


class ProposerCollector:
    def __init__(self,req:ProposerRequestV1,token:int):
        require(type(token) is int and token>0,"S3 proposer token missing")
        self.req=req
        self.token=token
        self.lock=threading.Lock()
        self.rows=[]
        self.terminal=None
        self.terminal_ns=None
        self.failure=None
        self.event=threading.Event()

    def on_info(self,token:int,line:str)->None:
        if token!=self.token:return
        with self.lock:
            if self.terminal is not None:return
            if len(self.rows)>=MAX_OBSERVATIONS or len(line)>4096:
                self.failure="S3 proposer info overflow"
            else:
                self.rows.append({"line":line,"ns":time.monotonic_ns()})

    def on_complete(self,token:int,line:str)->None:
        if token!=self.token:return
        with self.lock:
            if self.terminal is not None:
                self.failure="duplicate S3 proposer terminal"
            else:
                self.terminal=line
                self.terminal_ns=time.monotonic_ns()
            self.event.set()

    def wait(self,timeout:float,*,grant_digest:str,settlement_sha256:str)->dict:
        require(type(timeout) in (float,int) and
                0<float(timeout)<=300,"S3 proposer timeout invalid")
        if not self.event.wait(timeout):
            raise TimeoutError("S3 proposer no bounded real bestmove")
        with self.lock:
            if self.failure:
                raise StrengthContractError(self.failure)
            return reconstruct_proposer(self.req,list(self.rows),self.terminal,
                                        terminal_ns=self.terminal_ns,
                                        grant_digest=grant_digest,
                                        settlement_sha256=settlement_sha256)
