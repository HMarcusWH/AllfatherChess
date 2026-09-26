#!/usr/bin/env python3
"""Qualify LOCAL-1 complete-game lifecycle and descriptive five-arm baselines."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from common.search_request import parse_go_request
from controller.replay import load_manifest,verify_bundle_integrity
from controller.final_decision import (
    load_final_decision_artifact,
    verify_final_decision_integrity,
)
from controller.counterfactual import verify_counterfactual_integrity
from tests.harness.local_full_game import (
    LocalFullGameError,
    arm_outcome,
    expected_arm_moves,
    parse_proxy_transcript,
    parse_uci_pgn,
    validate_request_position,
)

POLICY=ROOT/"qualification/local-full-game.json"


class QualificationError(RuntimeError):
    pass


def load_json(path:Path)->dict[str,Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""):
            h.update(chunk)
    return h.hexdigest()


def source_sha()->str:
    return subprocess.check_output(
        ["git","-C",str(ROOT),"rev-parse","HEAD"],text=True
    ).strip()


def bad_termination(value:str)->str|None:
    lowered=(value or "").strip().lower()
    for marker in ("illegal move","disconnect","stall","unterminated"):
        if marker in lowered:
            return marker
    return None


def time_forfeit(value:str)->bool:
    return "time forfeit" in (value or "").lower()


def replay_records(case:dict[str,Any], arm:str, policy:dict[str,Any])->list[tuple[Path,dict[str,Any]]]:
    names=list((case.get("new_replays") or {}).get(arm) or [])
    root=ROOT/policy["arms"][arm]["replay_root"]
    records=[]
    for name in names:
        path=root/name
        if not (path/"manifest.json").is_file():
            raise QualificationError(f"{case['case']}:{arm}: replay {name} lacks manifest.json")
        records.append((path,load_manifest(path)))
    records.sort(key=lambda item:(int(item[1].get("generation",-1)),item[0].name))
    return records


def validate_allfather_replays(
    *,
    case:dict[str,Any],
    arm:str,
    transcript:dict[str,Any],
    policy:dict[str,Any],
    problems:list[str],
    metrics:dict[str,Any],
) -> None:
    requests=list(transcript["requests"])
    try:
        records=replay_records(case,arm,policy)
    except Exception as exc:
        problems.append(f"{case['case']}:{arm}: cannot load replay set: {exc}")
        return
    if len(records)!=len(requests):
        problems.append(
            f"{case['case']}:{arm}: replay/request count mismatch "
            f"{len(records)} != {len(requests)}"
        )
        metrics["missing_replays"]+=abs(len(records)-len(requests))
        return

    used=set()
    for ordinal,((run,manifest),request) in enumerate(zip(records,requests),1):
        if run in used:
            metrics["reused_replays"]+=1
            problems.append(f"{case['case']}:{arm}: replay reused: {run.name}")
            continue
        used.add(run)
        integrity=verify_bundle_integrity(run)
        if integrity:
            metrics["replay_integrity_errors"]+=len(integrity)
            problems.append(
                f"{case['case']}:{arm}:{run.name}: replay integrity: {integrity}"
            )
        if manifest.get("position",{}).get("command")!=request["position"]:
            problems.append(
                f"{case['case']}:{arm}:{run.name}: replay position differs from transcript"
            )
        if manifest.get("external_request",{}).get("command")!=request["go"]:
            problems.append(
                f"{case['case']}:{arm}:{run.name}: replay go differs from transcript"
            )
        try:
            parsed_go=parse_go_request(request["go"])
        except Exception as exc:
            problems.append(f"{case['case']}:{arm}: invalid external go: {exc}")
            parsed_go={}
        names={item.get("kind") for item in parsed_go.get("limits",[]) if isinstance(item,dict)}
        if not {"wtime","btime"} <= names:
            problems.append(
                f"{case['case']}:{arm}:{run.name}: Fastchess request did not carry both clocks"
            )
        outcome=manifest.get("clock_outcome") or {}
        if outcome.get("output_within_deadline") is not True:
            metrics["controller_time_forfeits"]+=1
            problems.append(
                f"{case['case']}:{arm}:{run.name}: controller missed its hard deadline"
            )
        resource_path=run/"resource.json"
        route_path=run/"route.json"
        if not resource_path.is_file() or not route_path.is_file():
            metrics["unqualified_required_resources"]+=1
            problems.append(f"{case['case']}:{arm}:{run.name}: missing resource/route artifact")
            continue
        resource=load_json(resource_path)
        route=load_json(route_path)
        if resource.get("qualified") is not True:
            metrics["unqualified_required_resources"]+=1
            problems.append(f"{case['case']}:{arm}:{run.name}: resource certificate unqualified")
        if (route.get("envelope_claim") or {}).get("claimed") is not True:
            metrics["unqualified_required_resources"]+=1
            problems.append(f"{case['case']}:{arm}:{run.name}: route envelope claim denied")
        physical=resource.get("physical_cpu_ms")
        if isinstance(physical,(int,float)) and not isinstance(physical,bool):
            metrics["physical_cpu_ms_by_arm"][arm]+=float(physical)

        bestmoves=request["bestmoves"]
        if len(bestmoves)!=1:
            continue
        emitted=bestmoves[0].split()[1].lower()

        if arm=="allfather-hybrid":
            final_path=run/"decision"/"final.json"
            if not final_path.is_file():
                problems.append(f"{case['case']}:{arm}:{run.name}: missing decision/final.json")
                continue
            final_problems=verify_final_decision_integrity(run)
            if final_problems:
                metrics["replay_integrity_errors"]+=len(final_problems)
                problems.append(
                    f"{case['case']}:{arm}:{run.name}: final decision integrity: {final_problems}"
                )
            final=load_final_decision_artifact(run)["decision"]
            if final.get("emitted_move")!=emitted:
                problems.append(
                    f"{case['case']}:{arm}:{run.name}: final emitted move "
                    f"{final.get('emitted_move')!r} != transcript {emitted!r}"
                )
            authority=str(final.get("authority"))
            metrics["authority"][authority]+=1
            cf=run/"decision"/"counterfactual.json"
            if cf.is_file():
                cf_problems=verify_counterfactual_integrity(run)
                if cf_problems:
                    metrics["replay_integrity_errors"]+=len(cf_problems)
                    problems.append(
                        f"{case['case']}:{arm}:{run.name}: counterfactual integrity: {cf_problems}"
                    )
        else:
            if (run/"decision"/"final.json").exists():
                problems.append(
                    f"{case['case']}:{arm}:{run.name}: control arm unexpectedly sealed move authority"
                )
            anchors=[
                stage for stage in manifest.get("stages",[])
                if isinstance(stage,dict) and stage.get("role")=="anchor"
            ]
            if len(anchors)!=1 or anchors[0].get("bestmove")!=emitted:
                problems.append(
                    f"{case['case']}:{arm}:{run.name}: control anchor bestmove does not match output"
                )


def validate_case(
    case:dict[str,Any],
    *,
    policy:dict[str,Any],
    mode:str,
    problems:list[str],
    metrics:dict[str,Any],
    baseline_rows:list[dict[str,Any]],
) -> None:
    case_id=str(case.get("case"))
    if case.get("harness_error"):
        problems.append(f"{case_id}: harness error: {case['harness_error']}")
        return
    if case.get("returncode")!=0:
        problems.append(f"{case_id}: Fastchess return code {case.get('returncode')}")
    if case.get("timed_out"):
        problems.append(f"{case_id}: Fastchess invocation timed out")
    leaks=case.get("process_leaks") or {}
    if leaks:
        metrics["process_leaks"]+=len(leaks)
        problems.append(f"{case_id}: leaked processes {leaks}")

    pgn_rel=case.get("pgn")
    if not isinstance(pgn_rel,str) or not (ROOT/pgn_rel).is_file():
        problems.append(f"{case_id}: missing PGN")
        return
    try:
        games=parse_uci_pgn(ROOT/pgn_rel)
    except LocalFullGameError as exc:
        problems.append(f"{case_id}: PGN parse failed: {exc}")
        return

    policy_case=case.get("policy_case") or {}
    expected_games=int(policy_case.get("rounds",1))*int(policy_case.get("games",2))
    if len(games)!=expected_games:
        problems.append(f"{case_id}: game count {len(games)} != expected {expected_games}")

    opening_prefix=list(policy_case.get("expected_history_prefix") or [])
    terminal=bool(policy_case.get("terminal_without_search"))
    arms=policy["arms"]
    transcripts={}
    for arm,path_rel in (case.get("transcripts") or {}).items():
        try:
            transcripts[arm]=parse_proxy_transcript(ROOT/path_rel)
        except Exception as exc:
            problems.append(f"{case_id}:{arm}: transcript parse failed: {exc}")
            continue
        if transcripts[arm]["protocol_errors"]:
            metrics["duplicate_bestmoves"]+=sum(
                1 for row in transcripts[arm]["protocol_errors"]
                if "duplicate bestmove" in str(row.get("reason",""))
            )
            problems.append(
                f"{case_id}:{arm}: transcript protocol errors: "
                f"{transcripts[arm]['protocol_errors']}"
            )
        for request in transcripts[arm]["requests"]:
            if len(request["bestmoves"])!=1:
                if len(request["bestmoves"])>1:
                    metrics["duplicate_bestmoves"]+=len(request["bestmoves"])-1
                problems.append(
                    f"{case_id}:{arm}: request {request['request_index']} has "
                    f"{len(request['bestmoves'])} terminal bestmoves"
                )
            elif request["bestmoves"][0].split()[1].lower() in {"0000","(none)","none","a1a1"}:
                metrics["null_bestmoves"]+=1
                problems.append(
                    f"{case_id}:{arm}: unexpected null bestmove on active request"
                )

    for game_index,game in enumerate(games):
        termination=game["headers"].get("Termination","")
        marker=bad_termination(termination)
        if marker:
            metric_key={
                "illegal move":"illegal_moves",
                "abandoned":"abandoned_games",
                "unterminated":"unterminated_games",
            }[marker]
            metrics[metric_key]+=1
            problems.append(f"{case_id}: game {game_index} bad termination: {termination}")
        if time_forfeit(termination):
            metrics["time_forfeits"]+=1
            allfather_lost=False
            for candidate in case.get("engines",[]):
                spec=policy["arms"][candidate]
                if spec["kind"]!="allfather":
                    continue
                outcome=arm_outcome(
                    {
                        "White":game["headers"].get("White"),
                        "Black":game["headers"].get("Black"),
                        "Result":game["headers"].get("Result"),
                    },
                    spec["display_name"],
                )
                if outcome=="loss":
                    allfather_lost=True
                    break
            if mode in {"required","soak"} and allfather_lost:
                metrics["controller_time_forfeits"]+=1
                problems.append(
                    f"{case_id}: game {game_index} Allfather lost on time"
                )
        baseline_rows.append({
            "case":case_id,
            "game_index":game_index,
            "white":game["headers"].get("White"),
            "black":game["headers"].get("Black"),
            "result":game["headers"].get("Result"),
            "termination":termination,
        })

        for arm in case.get("engines",[]):
            transcript=transcripts.get(arm)
            if transcript is None:
                continue
            expected=expected_arm_moves(
                game,arms[arm]["display_name"],len(opening_prefix)
            )
            actual=[
                row for row in transcript["requests"]
                if int(row.get("game_index",-999))==game_index
            ]
            if terminal and expected:
                problems.append(
                    f"{case_id}:{arm}: terminal-without-search fixture produced expected moves"
                )
            if len(actual)!=len(expected):
                problems.append(
                    f"{case_id}:{arm}: game {game_index} request count "
                    f"{len(actual)} != expected played moves {len(expected)}"
                )
                continue
            for req,exp in zip(actual,expected):
                for detail in validate_request_position(
                    req,base_fen=game["base_fen"],history=exp["history"]
                ):
                    problems.append(f"{case_id}:{arm}: game {game_index}: {detail}")
                if len(req["bestmoves"])==1:
                    move=req["bestmoves"][0].split()[1].lower()
                    if move!=exp["move"]:
                        problems.append(
                            f"{case_id}:{arm}: game {game_index}: transcript bestmove "
                            f"{move} != PGN {exp['move']}"
                        )

    if terminal:
        for arm,transcript in transcripts.items():
            if transcript["requests"]:
                problems.append(f"{case_id}:{arm}: terminal fixture unexpectedly searched")

    for arm in case.get("engines",[]):
        if arms[arm]["kind"]=="allfather" and arm in transcripts:
            validate_allfather_replays(
                case=case,arm=arm,transcript=transcripts[arm],policy=policy,
                problems=problems,metrics=metrics,
            )
            if opening_prefix and not terminal and transcripts[arm]["requests"]:
                first=transcripts[arm]["requests"][0]
                try:
                    from common.search_request import parse_position_command
                    pos=parse_position_command(first["position"])
                    if list(pos.moves[:len(opening_prefix)])!=opening_prefix:
                        problems.append(
                            f"{case_id}:{arm}: opening transition/history prefix not preserved"
                        )
                except Exception as exc:
                    problems.append(f"{case_id}:{arm}: cannot check history prefix: {exc}")


def baseline_summary(
    rows:list[dict[str,Any]],policy:dict[str,Any],metrics:dict[str,Any]
)->dict[str,Any]:
    arms=policy["baseline"]["arms"]
    displays={arm:policy["arms"][arm]["display_name"] for arm in arms}
    reverse={name:arm for arm,name in displays.items()}
    totals={arm:{"wins":0,"draws":0,"losses":0,"games":0} for arm in arms}
    pairwise=defaultdict(lambda:{
        "games":0,"results":Counter(),"terminations":Counter()
    })
    for row in rows:
        white=reverse.get(row["white"])
        black=reverse.get(row["black"])
        if white is None or black is None:
            continue
        pair="__".join(sorted((white,black)))
        pairwise[pair]["games"]+=1
        pairwise[pair]["results"][row["result"]]+=1
        pairwise[pair]["terminations"][row["termination"]]+=1
        for arm in (white,black):
            outcome=arm_outcome(
                {"White":row["white"],"Black":row["black"],"Result":row["result"]},
                displays[arm],
            )
            if outcome is None:
                continue
            totals[arm]["games"]+=1
            totals[arm][outcome+"s"]+=1
    return {
        "schema_version":1,
        "profile_id":policy["profile_id"],
        "source_commit":source_sha(),
        "time_control":policy["time_control"],
        "arms":totals,
        "pairwise":{
            key:{
                "games":value["games"],
                "results":dict(value["results"]),
                "terminations":dict(value["terminations"]),
            }
            for key,value in sorted(pairwise.items())
        },
        "allfather_measured_physical_cpu_ms":{
            arm:round(total,3)
            for arm,total in sorted(metrics["physical_cpu_ms_by_arm"].items())
        },
        "claim_boundary":{
            "same_clock_baseline":True,
            "equal_resource_comparison":False,
            "elo":False,
            "superiority":False,
        },
    }


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=["required","baseline","soak"],default="required")
    args=parser.parse_args()

    policy=load_json(POLICY)
    root=ROOT/policy["output_root"]/args.mode
    campaign_path=root/"campaign-manifest.json"
    if not campaign_path.is_file():
        raise QualificationError(f"missing campaign manifest: {campaign_path}")
    campaign=load_json(campaign_path)
    problems=[]
    if campaign.get("source_commit")!=source_sha():
        problems.append(
            f"campaign source {campaign.get('source_commit')} != checkout {source_sha()}"
        )
    if campaign.get("policy_sha256")!=sha256(POLICY):
        problems.append("campaign policy digest does not match qualification policy")

    runner_manifest=ROOT/campaign.get("runner_manifest","")
    lock=load_json(ROOT/policy["runner_lock"])
    if not runner_manifest.is_file():
        problems.append("missing Fastchess build manifest")
    else:
        runner=load_json(runner_manifest)
        if runner.get("source_commit")!=lock["commit"] or runner.get("source_tree")!=lock["tree"]:
            problems.append("Fastchess build manifest does not match frozen lock")

    metrics=defaultdict(int)
    metrics["authority"]=Counter()
    metrics["physical_cpu_ms_by_arm"]=defaultdict(float)
    baseline_rows=[]
    for case in campaign.get("cases",[]):
        validate_case(
            case,policy=policy,mode=args.mode,problems=problems,
            metrics=metrics,baseline_rows=baseline_rows,
        )

    if args.mode=="baseline":
        expected=int(policy["acceptance"]["baseline"]["required_completed_games"])
        if len(baseline_rows)!=expected:
            problems.append(
                f"baseline completed games {len(baseline_rows)} != frozen expectation {expected}"
            )
        for key in ("illegal_moves","abandoned_games","unterminated_games"):
            if int(metrics[key])!=int(policy["acceptance"]["baseline"][key]):
                problems.append(
                    f"baseline acceptance {key}={metrics[key]} "
                    f"!= {policy['acceptance']['baseline'][key]}"
                )
        summary=baseline_summary(baseline_rows,policy,metrics)
        (root/"baseline-comparison.json").write_text(
            json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+"\n",
            encoding="utf-8",
        )
    elif args.mode=="required":
        frozen=policy["acceptance"]["required"]
        observed={
            "illegal_moves":int(metrics["illegal_moves"]),
            "duplicate_bestmoves":int(metrics["duplicate_bestmoves"]),
            "null_bestmoves":int(metrics["null_bestmoves"]),
            "controller_time_forfeits":int(metrics["controller_time_forfeits"]),
            "missing_replays":int(metrics["missing_replays"]),
            "reused_replays":int(metrics["reused_replays"]),
            "replay_integrity_errors":int(metrics["replay_integrity_errors"]),
            "unqualified_required_resources":int(metrics["unqualified_required_resources"]),
            "process_leaks":int(metrics["process_leaks"]),
        }
        for key,expected in frozen.items():
            if observed.get(key)!=int(expected):
                problems.append(
                    f"required acceptance {key}={observed.get(key)} != {expected}"
                )

    report={
        "schema_version":1,
        "profile_id":policy["profile_id"],
        "mode":args.mode,
        "source_commit":source_sha(),
        "policy_sha256":sha256(POLICY),
        "cases":len(campaign.get("cases",[])),
        "games":len(baseline_rows),
        "metrics":{
            key:(dict(value) if isinstance(value,Counter) else
                 {k:round(v,3) for k,v in value.items()} if isinstance(value,defaultdict) else
                 value)
            for key,value in metrics.items()
            if key not in {"physical_cpu_ms_by_arm"}
        },
        "allfather_measured_physical_cpu_ms":{
            arm:round(total,3)
            for arm,total in sorted(metrics["physical_cpu_ms_by_arm"].items())
        },
        "problems":problems,
        "qualified":not problems,
        "claim_boundary":policy["claim_boundary"],
    }
    (root/"report.json").write_text(
        json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    if problems:
        print("LOCAL-1 qualification FAILED")
        for item in problems:
            print(" -",item)
        return 1
    print(
        f"LOCAL-1 {args.mode} qualification PASS: "
        f"{len(baseline_rows)} games, authority={dict(metrics['authority'])}"
    )
    return 0


if __name__=="__main__":
    raise SystemExit(main())
