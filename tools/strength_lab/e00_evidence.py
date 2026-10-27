"""Independent source/flight/actual-stdout reconciliation for E00.

Hash-linked producer journals are only one piece of evidence. This module
cross-checks the real UCI observer output, exact search identity and
occurrence, rather than treating self-consistency as physical truth.
"""
from __future__ import annotations
import hashlib
from typing import Any

def need(ok:bool,message:str)->None:
    if not ok:
        raise ValueError("E00 evidence: "+message)

def reconcile_flight(flight:dict, *, row:dict, occurrence:int,
                     published_stdout:str, expected_go:str,
                     source_commit:str, binary_sha256:str,
                     profile_sha256:str, options_sha256:str,
                     prior_session:str|None) -> tuple[str,str]:
    need(type(flight) is dict and type(flight.get("events")) is list,
         "missing canonical flight")
    events=flight["events"]
    begins=[e["data"] for e in events if e.get("event")=="begin"]
    terminals=[e["data"] for e in events if e.get("event")=="terminal"]
    publications=[e["data"] for e in events if e.get("event")=="published"]
    need(len(begins)==len(terminals)==len(publications)==1,
         "missing/duplicate flight begin/terminal/publication")
    begin,terminal,published=begins[0],terminals[0],publications[0]
    turn=begin["turn"]
    search=begin["search"]
    plan=begin["native_time"]
    need(begin["position_command"]==row["position_command"]
         and turn["history_sha256"]==row["history_sha256"]
         and hashlib.sha256(begin["position_command"].encode()).hexdigest()
             ==row["history_sha256"], "source history differs from flight")
    need(turn["generation"]==occurrence+1 and
         search["turn"]==turn and
         search["source_commit"]==source_commit and
         search["profile_sha256"]==profile_sha256 and
         search["binary_sha256"]==binary_sha256 and
         search["options_sha256"]==options_sha256,
         "search generation/profile/network/executable identity mismatch")
    need(prior_session is None or turn["session_id"]==prior_session,
         "multiple S2 sessions injected into one experiment")
    need(plan["external_go_command"]==expected_go and
         plan["anchor_go_command"]==expected_go and
         plan["generation"]==turn["generation"],
         "flight go command differs from UCI stream")
    selection=terminal["selection"]
    need(selection["anchor_line"]==published_stdout
         and selection["chosen_line"]==published_stdout
         and terminal["checkpoint"]["move"]==published_stdout.split()[1]
         and published["chosen_line"]==published_stdout
         and published["epoch"]==selection["incumbent_epoch"]
         and selection["replaces_anchor"] is False
         and selection["would_authorize"] is False,
         "flight selection/published bytes disagree with external bestmove")
    observed=terminal["checkpoint"]["observed_ns"]
    publication=published["at_ns"]
    start_ns=int(plan["received_monotonic"]*1e9)
    need(type(observed) is int and type(publication) is int and
         start_ns<=observed<=publication,
         "within-flight receipt/terminal/publication clock order invalid")
    nonce=turn["search_nonce"]
    need(isinstance(nonce,str) and len(nonce)==32,"missing search nonce")
    return turn["session_id"],nonce

def reconstruct_stdout_times(events:list[dict],go:str,count:int) -> list[dict]:
    """Use real recorded UCI command/terminal events, not sample summary times."""
    need(type(events) is list and count>0,"empty UCI session evidence")
    rows=[]
    pending=None
    for event in events:
        need(type(event) is dict and
             set(event)=={"direction","line","ns"} and
             type(event["ns"]) is int and event["ns"]>0
             and type(event["line"]) is str,
             "invalid UCI source event schema")
        channel=event["direction"]
        line=event["line"]
        if channel=="stdin" and line.startswith("go "):
            need(line==go and pending is None,"overlapping or changed real UCI go")
            pending=event
        elif channel=="stdout" and line.startswith("bestmove "):
            need(pending is not None,"unsolicited/duplicate real terminal")
            need(event["ns"]>=pending["ns"],"terminal timestamp precedes go")
            rows.append({"go_ns":pending["ns"],"terminal_ns":event["ns"],
                         "terminal_line":line,
                         "wall_ms":(event["ns"]-pending["ns"])/1e6})
            pending=None
    need(pending is None and len(rows)==count,
         "missing/extra actual UCI command to terminal pair")
    return rows
