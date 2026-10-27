"""Independent verification of native-anchor S2 event journals.

A valid journal is only *mechanism evidence*: there are no strength, quality,
resource, game, or public-release qualification fields that can be promoted.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
from typing import Any

from common.search_request import parse_position_command
from .contracts import StrengthContractError, TurnKey, SearchIdentity, FinalSelection, digest, require, MOVE
from .native_clock import verify_native_plan
from .e00_nodes import verify_e00_plan


def strict_load(path: Path) -> dict:
    def pairs(rows):
        result={}
        for k,v in rows:
            require(k not in result, "duplicate JSON key in S2 evidence")
            result[k]=v
        return result
    data=Path(path).read_bytes()
    require(len(data)<=128*1024*1024, "oversized S2 evidence")
    doc=json.loads(data,object_pairs_hook=pairs,
                   parse_constant=lambda x: (_ for _ in ()).throw(StrengthContractError("nonfinite JSON")))
    require(type(doc) is dict, "invalid S2 evidence root")
    return doc


def verify_flight(record: dict) -> dict:
    """Reconstruct event, search, ABA and publication identities independently."""
    reasons=[]
    try:
        require(type(record) is dict and
                set(record)=={"schema_version","kind","diagnostic_only","qualified",
                              "strength_qualified","replacement_authorized","overflow",
                              "failure","events","end_sha256"}, "unexpected S2 journal fields")
        require(record["schema_version"]==1 and record["kind"]=="strength_s2_flight_v1",
                "unsupported S2 flight discriminator")
        require(record["diagnostic_only"] is True
                and record["qualified"] is False
                and record["strength_qualified"] is False
                and record["replacement_authorized"] is False,
                "journal claims scientific/live authority")
        require(record["overflow"] is False and record["failure"] is None,
                "incomplete/failed S2 flight")
        events=record["events"]
        require(type(events) is list and 1<=len(events)<=65536, "missing/oversized event sequence")
        previous="0"*64
        begin=None
        observed_epoch=0
        last_root=None
        last_progress=0
        last_checkpoint=None
        terminal=None
        published=None
        aborted=False
        for i,item in enumerate(events):
            require(type(item) is dict and set(item)=={"seq","event","data","previous_sha256","event_sha256"},
                    "event unknown/missing keys")
            require(type(item["seq"]) is int and item["seq"]==i+1, "event sequence mismatch")
            require(item["previous_sha256"]==previous, "event chain broken")
            raw={k:v for k,v in item.items() if k!="event_sha256"}
            require(digest(raw)==item["event_sha256"], "event digest mismatch")
            previous=item["event_sha256"]
            event=item["event"]
            data=item["data"]
            require(type(data) is dict, "event payload not object")
            if event=="begin":
                require(i==0 and begin is None and
                        set(data)=={"turn","search","position_command","native_time"},
                        "ambiguous begin")
                begin=data
                turn=TurnKey(**{k:v for k,v in data["turn"].items()
                                if k not in ("kind","schema_version")})
                require(data["turn"]==turn.as_dict(), "turn record does not roundtrip")
                search=SearchIdentity(
                    turn=turn,source_commit=data["search"]["source_commit"],
                    profile_sha256=data["search"]["profile_sha256"],
                    binary_sha256=data["search"]["binary_sha256"],
                    options_sha256=data["search"]["options_sha256"])
                require(search.as_dict()==data["search"], "search identity mismatch")
                pos=parse_position_command(data["position_command"])
                import hashlib
                require(hashlib.sha256(data["position_command"].encode()).hexdigest()==turn.history_sha256
                        and pos.side_to_move==turn.side_to_move and pos.variant==turn.variant,
                        "full history identity mismatch")
                plan=data["native_time"]
                if plan.get("kind")=="strength_e00_node_plan_v1":
                    require(not verify_e00_plan(plan,position=pos),
                            "offline E00 node source/timing mismatch")
                else:
                    require(not verify_native_plan(plan,position=pos),
                            "native clock source/timing mismatch")
                require(plan["generation"]==turn.generation
                        and plan["position_id"]==pos.position_id
                        and turn.restriction_sha256==digest({"searchmoves":plan["restriction"]}),
                        "clock/root restriction turn mismatch")
            elif event=="checkpoint":
                require(begin is not None and terminal is None and not aborted,
                        "out of order incumbent event")
                ck=data
                require(ck.get("kind")=="strength_incumbent_checkpoint_v1",
                        "unsupported checkpoint kind")
                root=ck["move"]
                require(isinstance(root,str) and MOVE.fullmatch(root) is not None,
                        "invalid root")
                require(not plan["restriction"] or root in plan["restriction"],
                        "out-of-restriction incumbent checkpoint")
                require(type(ck.get("depth")) is int and ck["depth"]>0
                        and type(ck.get("nodes")) is int and ck["nodes"]>=0
                        and type(ck.get("observed_ns")) is int and ck["observed_ns"]>=0,
                        "invalid incumbent native-depth/node/time metadata")
                observed_epoch += int(root!=last_root)
                last_progress+=1
                require(ck["root_epoch"]==observed_epoch
                        and ck["progress_seq"]==last_progress
                        and ck["search_digest"]==search.search_digest,
                        "checkpoint epoch/progress/search mismatch")
                last_root=root
                last_checkpoint=ck
            elif event=="terminal":
                require(begin is not None and terminal is None and not aborted,
                        "duplicate/out of order anchor terminal")
                ck=data["checkpoint"]
                sel=data["selection"]
                root=ck["move"]
                require(not plan["restriction"] or root in plan["restriction"],
                        "terminal bestmove outside external-root restriction")
                observed_epoch+=int(root!=last_root)
                last_progress+=1
                require(ck["root_epoch"]==observed_epoch
                        and ck["progress_seq"]==last_progress
                        and ck["search_digest"]==search.search_digest,
                        "terminal epoch identity mismatch")
                selection=FinalSelection(**{k:v for k,v in sel.items()
                                           if k not in ("schema_version",)})
                require(sel==selection.as_dict(),"forged S2 final selection")
                require(selection.anchor_line.split()[1]==root
                        and selection.incumbent_epoch==observed_epoch,
                        "terminal bestmove/checkpoint mismatch")
                terminal=selection
                last_root=root
            elif event=="published":
                require(terminal is not None and published is None and not aborted,
                        "publication missing/duplicate or after abort")
                require(set(data)=={"chosen_line","at_ns","epoch"}
                        and data["chosen_line"]==terminal.chosen_line
                        and data["epoch"]==terminal.incumbent_epoch
                        and type(data["at_ns"]) is int and data["at_ns"]>=0,
                        "published stdout differs from selected anchor")
                published=data
            elif event=="abort":
                require(published is None and not aborted, "abort after publication")
                require(set(data)=={"reason"} and isinstance(data["reason"],str),
                        "invalid abort record")
                aborted=True
            elif event=="denied_info":
                require(begin is not None and terminal is None, "stale denied frame")
            elif event=="denied_publication":
                raise StrengthContractError("duplicate/stale publication attempted")
            else:
                raise StrengthContractError(f"unknown S2 flight event {event!r}")
        require(previous==record["end_sha256"], "flight terminal hash mismatch")
        require(begin is not None, "missing S2 begin")
        # Interrupted searches remain a negative/mechanism-only result.
        status="PUBLISHED_ANCHOR" if published is not None else (
            "ABORTED" if aborted else "INCOMPLETE")
        return {"schema_version":1,"diagnostic_only":True,"qualified":False,
                "status":status,"events_verified":len(events),
                "replacement_count":0,"incumbent_epochs":observed_epoch,
                "source_commit":search.source_commit}
    except (StrengthContractError,KeyError,TypeError,ValueError,IndexError) as exc:
        reasons.append(str(exc))
    return {"schema_version":1,"diagnostic_only":True,"qualified":False,
            "status":"INVALID","errors":reasons}
