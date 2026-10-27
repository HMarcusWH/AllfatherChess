"""Independent E00 campaign reconstruction. Never infer qualification from green CI."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from controller.strength.integrity import strict_load,verify_flight
from tools.strength_lab.e00_corpus import ROOT,POLICY,build_corpus,bytes_for
from tools.strength_lab.e00_runner import (ARMS,BUNDLE,ROOT_OUT,E00_PROFILE,NATIVE_PROFILE,
                                           preflight,sha,head)

class E00Failure(ValueError):
    pass

def need(ok:bool,reason:str)->None:
    if not ok:raise E00Failure(reason)

def _file(path:Path, root:Path) -> Path:
    """No symlinked or escaped evidence inputs."""
    root=root.resolve(strict=True)
    need(not path.is_symlink() and path.is_file(), "E00 missing/symlinked evidence file")
    resolved=path.resolve(strict=True)
    need(resolved.is_relative_to(root), "E00 evidence path escapes owned root")
    return resolved

def _bestmove(line:str,board) -> str:
    import chess
    hit=re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?)(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?",line)
    need(hit is not None,"E00 terminal move missing/invalid")
    move=chess.Move.from_uci(hit.group(1))
    need(move in board.legal_moves,"E00 terminal chess move illegal")
    return hit.group(1)

def _board(command:str):
    import chess
    words=command.split()
    need(len(words)>=2 and words[0]=="position","invalid E00 full history")
    if words[1]=="startpos":
        board=chess.Board()
        rest=words[2:]
    else:
        need(words[1]=="fen" and len(words)>=8,"invalid E00 FEN")
        board=chess.Board(" ".join(words[2:8]))
        rest=words[8:]
    need(not rest or rest[0]=="moves","invalid E00 move suffix")
    for text in rest[1:] if rest else []:
        move=chess.Move.from_uci(text)
        need(move in board.legal_moves,"E00 history move illegal")
        board.push(move)
    need(board.is_valid() and bool(list(board.legal_moves)),"invalid/terminal E00 board")
    return board

def p95(rows:list[float])->float:
    need(bool(rows) and all(type(x) in (float,int) and math.isfinite(x) for x in rows),
         "E00 p95 missing/nonfinite")
    return float(sorted(rows)[math.ceil(.95*len(rows))-1])

def verify_campaign(run:Path) -> dict:
    run=run.resolve(strict=True)
    need(run.is_dir() and run.is_relative_to(ROOT_OUT.resolve()),"E00 run outside build campaign root")
    manifest=strict_load(_file(run/"manifest.json",run))
    policy=strict_load(POLICY)
    keys={"schema_version","kind","phase","status","source_commit","policy_sha256",
          "profile_sha256","binary_sha256","bundle_manifest_sha256","corpus_sha256",
          "corpus_count","expected_samples","go_command","arms","samples","flights",
          "participants","errors","resource_qualified","equal_compute",
          "strength_qualified","live_game_authorized","release_authorized"}
    need(set(manifest)==keys and manifest["schema_version"]==1
         and manifest["kind"]=="strength_e00_campaign_v1"
         and manifest["phase"] in ("smoke","full","native-smoke")
         and manifest["status"]=="COMPLETE" and manifest["errors"]==[],
         "E00 incomplete/failed or unknown manifest")
    native=manifest["phase"]=="native-smoke"
    profile=NATIVE_PROFILE if native else E00_PROFILE
    source=head()
    binary,opts=preflight(profile,source)
    need(manifest["source_commit"]==source and manifest["policy_sha256"]==sha(POLICY)
         and manifest["profile_sha256"]==sha(profile)
         and manifest["binary_sha256"]==sha(binary)
         and manifest["bundle_manifest_sha256"]==sha(BUNDLE/"build-manifest.json")
         and manifest["corpus_count"]==policy["sample_count"]
         and manifest["arms"]==list(ARMS)
         and manifest["equal_compute"] is False
         and manifest["strength_qualified"] is False
         and manifest["live_game_authorized"] is False
         and manifest["release_authorized"] is False,
         "E00 source/bundle/authority drift")
    go=manifest["go_command"]
    need(go==("go wtime 30000 btime 30000 winc 1000 binc 1000" if native
              else f"go nodes {policy['search_nodes']}"),"E00 go request drift")
    rows=build_corpus()
    content=bytes_for(rows)
    need(_file(run/"corpus.jsonl",run).read_bytes()==content and
         hashlib.sha256(content).hexdigest()==manifest["corpus_sha256"],
         "E00 frozen corpus mismatch")
    count=policy["sample_count"] if manifest["phase"]=="full" else policy["smoke_count"]
    need(manifest["expected_samples"]==count
         and len(manifest["samples"])==count
         and len(manifest["flights"])==count
         and len(manifest["participants"])==len(ARMS),
         "E00 missing/extra sample, flight or participant")
    receipt_arms=set()
    have_cgroup=True
    all_receipts={}
    for rec in manifest["participants"]:
        arm=rec["arm"]
        need(arm in ARMS and arm not in receipt_arms, "E00 duplicated participant")
        receipt_arms.add(arm)
        need(rec["path"]==arm+"-session.json","E00 unexpected participant path")
        file=_file(run/rec["path"],run)
        need(sha(file)==rec["sha256"],"E00 participant receipt altered")
        receipt=strict_load(file)
        need(receipt["arm"]==arm and receipt["session_id"] and
             receipt["exit_code"]==0 and receipt["errors"]==[]
             and receipt["leaked_before_cleanup"] is False,
             "E00 participant crashed or leaked a child")
        needs={"started_ns","ended_ns","events","resource_status","cpu_ms",
               "memory_peak_bytes","arm","session_id","exit_code","errors",
               "leaked_before_cleanup"}
        need(set(receipt)==needs and receipt["started_ns"]<receipt["ended_ns"],
             "E00 participant receipt contract mismatch")
        is_measured=rec["resource_status"]==receipt["resource_status"]=="MEASURED"
        have_cgroup=have_cgroup and is_measured
        if is_measured:
            need(type(receipt["cpu_ms"]) in (float,int) and receipt["cpu_ms"]>0
                 and type(receipt["memory_peak_bytes"]) is int
                 and receipt["memory_peak_bytes"]>0,"invalid cgroup receipt")
        else:
            need(receipt["cpu_ms"] is None and receipt["memory_peak_bytes"] is None,
                 "unavailable E00 cgroup presented as zero")
        all_receipts[arm]=receipt
    need(receipt_arms==set(ARMS)
         and manifest["resource_qualified"] is have_cgroup,
         "E00 cgroup availability contradiction")
    actual_terminals={}
    for arm in ARMS:
        events=all_receipts[arm]["events"]
        output_moves=[e["line"] for e in events
                      if e.get("direction")=="stdout" and e.get("line","").startswith("bestmove ")]
        need(len(output_moves)==count,
             "E00 missing/extra actual stdout terminal moves")
        actual_terminals[arm]=output_moves
        stdout=[e["line"] for e in events if e.get("direction")=="stdout"]
        need(stdout.count("uciok")==1 and stdout.count("readyok")==count*2+1,
             "E00 handshake/readiness coverage incomplete")
        actual_input=[e["line"] for e in events if e.get("direction")=="stdin"]
        expected_input=["uci"]
        if arm==ARMS[0]:
            for name,value in opts.items():
                rendered=str(value).lower() if type(value) is bool else str(value)
                expected_input.append(f"setoption name {name} value {rendered}")
        expected_input.append("isready")
        for row in rows[:count]:
            expected_input.extend(["ucinewgame","isready",row["position_command"],
                                   go,"isready"])
        expected_input.append("quit")
        need(actual_input==expected_input,
             "E00 exact source UCI commands, history, options or reset order differ")
    witness_files={}
    flight_dir=(ROOT/("build/strength-s2-evidence" if native else
                      "build/strength-e00-evidence")).resolve()
    for rec in manifest["flights"]:
        rel=rec["path"]
        need(type(rel) is str and rel.startswith(str(flight_dir.relative_to(ROOT))+"/"),
             "E00 flight path mismatch")
        file=_file(ROOT/rel,flight_dir)
        need(sha(file)==rec["sha256"],"E00 flight source bytes changed")
        raw=strict_load(file)
        checked=verify_flight(raw)
        need(checked["status"]=="PUBLISHED_ANCHOR" and checked["qualified"] is False
             and checked["source_commit"]==source
             and checked["replacement_count"]==0,"invalid S2/E00 anchor flight")
        begin=raw["events"][0]["data"]
        plan=begin["native_time"]
        need(plan["kind"]==("strength_native_clock_plan_v1" if native else
                           "strength_e00_node_plan_v1") and
             plan["external_go_command"]==go,
             "E00 evidence not of the requested UCI policy")
        pos=begin["position_command"]
        key=hashlib.sha256(pos.encode("utf-8")).hexdigest()
        need(key not in witness_files,"duplicate E00 flight for full history")
        witness_files[key]=begin
    overhead_wall=[]
    overhead_cpu=[]
    seen_ids=set()
    discrepancies=[]
    for idx,entry in enumerate(manifest["samples"]):
        row=rows[idx]
        need(entry["id"]==row["id"] and
             entry["path"]==f"samples/{row['id']}.json" and row["id"] not in seen_ids,
             "E00 missing/reordered/duplicated sample")
        seen_ids.add(row["id"])
        file=_file(run/entry["path"],run)
        need(sha(file)==entry["sha256"],"E00 sample hash mismatch")
        record=strict_load(file)
        need(record["id"]==row["id"] and record["history_sha256"]==row["history_sha256"]
             and record["occurrence"]==idx and record["go_command"]==go
             and set(record["arms"])==set(ARMS),
             "E00 occurrence or arm join mismatch")
        board=_board(row["position_command"])
        need(row["final_fen"]==board.fen(en_passant="fen"),
             "E00 full chess history differs from sealed corpus")
        terminal={}
        for arm in ARMS:
            value=record["arms"][arm]
            need(value["arm"]==arm and value["session_id"]==all_receipts[arm]["session_id"]
                 and value["position_command"]==row["position_command"]
                 and value["go_command"]==go
                 and value["resource_status"]==all_receipts[arm]["resource_status"],
                 "E00 source/session/command mismatch")
            need(type(value["wall_ms"]) in (int,float) and 0<=value["wall_ms"]<120000
                 and type(value["search_sent_ns"]) is int
                 and type(value["search_ended_ns"]) is int
                 and abs((value["search_ended_ns"]-value["search_sent_ns"])/1e6 -
                         value["wall_ms"])<1e-6,
                 "E00 timing receipt invalid")
            stdout=value["stdout"]
            need(type(stdout) is list and len(stdout)>0
                 and all(type(line) is str for line in stdout),
                 "E00 stdout missing")
            best=[s for s in stdout if s.startswith("bestmove ")]
            need(len(best)==1 and best[0]==value["bestmove"]
                 and stdout[-1]==best[0]
                 and best[0]==actual_terminals[arm][idx],
                 "E00 raw session vs sample terminal occurrence differs")
            terminal[arm]=_bestmove(best[0],board)
            arm_measured=all_receipts[arm]["resource_status"]=="MEASURED"
            need((value["cpu_ms"] is None) if not arm_measured else
                 (type(value["cpu_ms"]) in (int,float) and
                  math.isfinite(value["cpu_ms"]) and value["cpu_ms"]>=0),
                 "E00 CPU observation invalid")
        need(row["history_sha256"] in witness_files,
             "E00 missing matched S2/E00 flight")
        if not native and terminal[ARMS[0]]!=terminal[ARMS[1]]:
            discrepancies.append(row["id"])
        overhead_wall.append(record["arms"][ARMS[1]]["wall_ms"]-
                             record["arms"][ARMS[0]]["wall_ms"])
        if have_cgroup:
            overhead_cpu.append(record["arms"][ARMS[1]]["cpu_ms"]-
                                record["arms"][ARMS[0]]["cpu_ms"])
    need(set(witness_files)=={x["history_sha256"] for x in rows[:count]},
         "unmatched/missing/extra E00 flight histories")
    need(not discrepancies,"E00 real fixed-node terminal move disagreements: "+",".join(discrepancies[:12]))
    wall=p95(overhead_wall)
    cpu=p95(overhead_cpu) if overhead_cpu else None
    wall_budget=policy["max_p95_wall_overhead_ms_seed"]
    cpu_budget=policy["max_p95_extra_cpu_ms_seed"]
    within=wall<=wall_budget and (cpu is not None and cpu<=cpu_budget)
    status=("PASS" if native else
            "PASS" if have_cgroup and within else
            "INCONCLUSIVE" if not have_cgroup else "FAIL")
    return {"schema_version":1,"status":status,
            "phase":manifest["phase"],"source_commit":source,
            "actual_history_count":count,"legal_move_count":2*count,
            "fixed_node_bestmove_agreement":None if native else count,
            "flight_reconstructed_count":count,
            "p95_wrapper_minus_direct_wall_ms":wall,
            "p95_wrapper_minus_direct_cpu_ms":cpu,
            "resource_coverage":"MEASURED" if have_cgroup else "UNAVAILABLE",
            "overhead_budget_pass":within if have_cgroup else None,
            "functional_equivalence":None if native else True,
            "native_protocol_smoke":True if native else None,
            "e00_scientifically_qualified":False,
            "strength_qualified":False,"equal_compute":False,
            "live_game_authorized":False,"public_release":False}
