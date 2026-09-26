#!/usr/bin/env python3
"""Run LOCAL-1 full-game and descriptive baseline campaigns through Fastchess."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/"qualification/local-full-game.json"


class CampaignError(RuntimeError):
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


def replay_dirs(root:Path)->set[str]:
    if not root.exists():
        return set()
    return {item.name for item in root.iterdir() if item.is_dir()}


def wait_new_replays(root:Path, known:set[str], timeout:float=20.0)->list[str]:
    deadline=time.monotonic()+timeout
    last:set[str]|None=None
    stable_since=None
    while time.monotonic()<deadline:
        current=replay_dirs(root)-known
        finalized={
            name for name in current
            if (root/name/"manifest.json").is_file()
        }
        if finalized==current:
            if last==current:
                if stable_since is not None and time.monotonic()-stable_since>=0.5:
                    return sorted(current)
            else:
                last=set(current)
                stable_since=time.monotonic()
        else:
            last=None
            stable_since=None
        time.sleep(0.05)
    return sorted(replay_dirs(root)-known)


def snapshot_processes()->dict[int,str]:
    try:
        output=subprocess.check_output(
            ["ps","-eo","pid=,args="],text=True,stderr=subprocess.DEVNULL
        )
    except (OSError,subprocess.CalledProcessError):
        return {}
    result={}
    markers=(
        "allfather.local-game.validation.json",
        "allfather.local-control.validation.json",
        "build/online-cpu-reference/bin/stockfish",
        "build/online-cpu-reference/bin/reckless",
        "build/online-cpu-reference/bin/lc0",
        "uci-transcript-proxy.py",
    )
    for raw in output.splitlines():
        raw=raw.strip()
        if not raw:
            continue
        head,_,tail=raw.partition(" ")
        try:
            pid=int(head)
        except ValueError:
            continue
        if any(marker in tail for marker in markers):
            result[pid]=tail
    return result


def wait_for_no_new_processes(before:dict[int,str], timeout:float=5.0)->dict[int,str]:
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        now=snapshot_processes()
        leaks={pid:cmd for pid,cmd in now.items() if pid not in before}
        if not leaks:
            return {}
        time.sleep(0.1)
    now=snapshot_processes()
    return {pid:cmd for pid,cmd in now.items() if pid not in before}


def child_command(arm:str, spec:dict[str,Any])->list[str]:
    if spec["kind"]=="allfather":
        return [
            sys.executable,
            "-m","controller",
            "--config",str(ROOT/spec["config"]),
        ]
    return [str(ROOT/spec["binary"])]


def write_wrapper(case_dir:Path, arm:str, spec:dict[str,Any], session_id:str)->tuple[Path,Path]:
    transcript=case_dir/f"transcript-{arm}.jsonl"
    wrapper=case_dir/f"engine-{arm}.sh"
    child=child_command(arm,spec)
    command=[
        sys.executable,
        str(ROOT/"scripts/uci-transcript-proxy.py"),
        "--log",str(transcript),
        "--session-id",session_id,
        "--",
        *child,
    ]
    wrapper.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nexec "
        +" ".join(shlex.quote(part) for part in command)
        +"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper,transcript


def fastchess_engine_args(arm:str, spec:dict[str,Any], wrapper:Path)->list[str]:
    args=[
        "-engine",
        f"cmd={wrapper}",
        f"name={spec['display_name']}",
        "restart=off",
        "option.UCI_Chess960=false",
    ]
    if arm=="stockfish":
        args += ["option.Threads=1","option.Hash=16","option.MultiPV=1"]
    elif arm=="reckless":
        args += [
            "option.Threads=1","option.Hash=16","option.MultiPV=1","option.Minimal=false"
        ]
    elif arm=="lc0":
        args += [
            "option.WeightsFile=build/online-cpu-reference/networks/791556.pb.gz",
            "option.Backend=blas",
            "option.BackendOptions=",
            "option.NNCacheSize=0",
            "option.MinibatchSize=32",
            "option.MaxConcurrentSearchers=1",
            "option.TaskWorkers=0",
            "option.MultiPV=1",
            "option.Threads=1",
            "option.ScoreType=WDL_mu",
        ]
    return args


def opening_args(opening:dict[str,Any])->list[str]:
    kind=opening["type"]
    if kind=="startpos":
        return []
    if kind not in {"pgn","epd"}:
        raise CampaignError(f"unsupported opening type {kind!r}")
    return [
        "-openings",
        f"file={opening['file']}",
        f"format={kind}",
        "order=sequential",
        f"start={int(opening['start'])}",
    ]


def run_case(
    policy:dict[str,Any],
    *,
    mode:str,
    case:dict[str,Any],
    output_root:Path,
    ordinal:int,
)->dict[str,Any]:
    case_id=str(case["id"])
    case_dir=output_root/case_id
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)

    arms=policy["arms"]
    first,second=case["engines"]
    before_processes=snapshot_processes()
    known_replays={}
    wrappers={}
    transcripts={}
    for arm in (first,second):
        spec=arms[arm]
        if spec["kind"]=="allfather":
            root=ROOT/spec["replay_root"]
            root.mkdir(parents=True,exist_ok=True)
            known_replays[arm]=(root,replay_dirs(root))
        session=f"{mode}-{ordinal:02d}-{case_id}-{arm}"
        wrapper,transcript=write_wrapper(case_dir,arm,spec,session)
        wrappers[arm]=wrapper
        transcripts[arm]=transcript

    pgn=case_dir/"games.pgn"
    log=case_dir/"fastchess.log"
    driver=case_dir/"driver.txt"
    binary=ROOT/policy["runner_binary"]
    if not binary.is_file():
        raise CampaignError(f"Fastchess binary missing: {binary}")
    for arm in (first,second):
        spec=arms[arm]
        if spec["kind"]=="direct" and not (ROOT/spec["binary"]).is_file():
            raise CampaignError(f"{arm} binary missing: {ROOT/spec['binary']}")

    command=[str(binary)]
    command += fastchess_engine_args(first,arms[first],wrappers[first])
    command += fastchess_engine_args(second,arms[second],wrappers[second])
    command += [
        "-each",
        f"tc={policy['time_control']['tc']}",
        f"timemargin={int(policy['time_control']['timemargin_ms'])}",
        "proto=uci",
        "-rounds",str(int(case.get("rounds",1))),
        "-games",str(int(case.get("games",2))),
        "-concurrency",str(int(policy["time_control"]["concurrency"])),
        "-variant","standard",
        "-maxmoves",str(int(case["maxmoves"])),
        "-srand","20260926",
        "-pgnout",f"file={pgn}","notation=uci","append=false",
        "-log",f"file={log}","level=trace","append=false","engine=true",
        "-event",f"LOCAL1-{case_id}",
        "-site","local-ci",
        "-ratinginterval","0",
        "-strict",
    ]
    command += opening_args(case.get("opening",{"type":"startpos"}))

    env=dict(os.environ)
    env.update({
        "OPENBLAS_NUM_THREADS":"1",
        "GOTO_NUM_THREADS":"1",
        "OMP_NUM_THREADS":"1",
        "PYTHONUNBUFFERED":"1",
    })
    started=time.monotonic()
    timed_out=False
    with driver.open("w",encoding="utf-8") as handle:
        try:
            completed=subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=float(case.get("timeout_s",900)),
                check=False,
                text=True,
            )
            returncode=completed.returncode
        except subprocess.TimeoutExpired:
            timed_out=True
            returncode=124
    duration=time.monotonic()-started

    new_replays={}
    for arm,(root,known) in known_replays.items():
        new_replays[arm]=wait_new_replays(root,known)

    leaks=wait_for_no_new_processes(before_processes)
    audit={
        "case":case_id,
        "mode":mode,
        "engines":[first,second],
        "display_names":[arms[first]["display_name"],arms[second]["display_name"]],
        "command":command,
        "returncode":returncode,
        "timed_out":timed_out,
        "duration_s":round(duration,3),
        "pgn":str(pgn.relative_to(ROOT)),
        "fastchess_log":str(log.relative_to(ROOT)),
        "driver_log":str(driver.relative_to(ROOT)),
        "transcripts":{
            arm:str(path.relative_to(ROOT)) for arm,path in transcripts.items()
        },
        "new_replays":new_replays,
        "process_leaks":{str(pid):cmd for pid,cmd in sorted(leaks.items())},
        "policy_case":case,
    }
    (case_dir/"case.json").write_text(
        json.dumps(audit,indent=2,sort_keys=True,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    return audit


def cases_for_mode(policy:dict[str,Any], mode:str, games:int|None)->list[dict[str,Any]]:
    if mode=="required":
        return [dict(item) for item in policy["required_cases"]]
    if mode=="baseline":
        baseline=policy["baseline"]
        return [
            {
                "id":f"baseline-{a}-vs-{b}",
                "engines":[a,b],
                "games":int(baseline["games_per_pairing"]),
                "rounds":int(baseline["rounds"]),
                "maxmoves":int(baseline["maxmoves"]),
                "opening":dict(baseline["opening"]),
                "expected_history_prefix":[],
                "terminal_without_search":False,
            }
            for a,b in baseline["pairings"]
        ]
    if mode=="soak":
        soak=policy["soak"]
        count=int(games if games is not None else soak["default_games"])
        if count<2 or count%2:
            raise CampaignError("soak game count must be an even integer >= 2")
        return [{
            "id":f"soak-{count}-games",
            "engines":list(soak["engines"]),
            "games":2,
            "rounds":count//2,
            "maxmoves":int(soak["maxmoves"]),
            "opening":{"type":"startpos"},
            "expected_history_prefix":[],
            "terminal_without_search":False,
            "timeout_s":max(1800,count*120),
        }]
    raise CampaignError(f"unsupported mode {mode!r}")


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=["required","baseline","soak"],default="required")
    parser.add_argument("--games",type=int)
    args=parser.parse_args()

    policy=load_json(POLICY)
    output_root=ROOT/policy["output_root"]/args.mode
    output_root.mkdir(parents=True,exist_ok=True)
    cases=cases_for_mode(policy,args.mode,args.games)
    records=[]
    for ordinal,case in enumerate(cases,1):
        print(f"==> LOCAL-1 {args.mode}: {case['id']}",flush=True)
        try:
            records.append(run_case(
                policy,mode=args.mode,case=case,output_root=output_root,ordinal=ordinal
            ))
        except Exception as exc:
            records.append({
                "case":case["id"],
                "mode":args.mode,
                "engines":case["engines"],
                "returncode":125,
                "harness_error":f"{type(exc).__name__}: {exc}",
                "process_leaks":{},
                "policy_case":case,
            })

    manifest={
        "schema_version":1,
        "profile_id":policy["profile_id"],
        "mode":args.mode,
        "source_commit":source_sha(),
        "policy_sha256":sha256(POLICY),
        "runner_manifest":str(Path("build/tools/fastchess/build-manifest.json")),
        "time_control":policy["time_control"],
        "cases":records,
    }
    path=output_root/"campaign-manifest.json"
    path.write_text(
        json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    bad=[
        row for row in records
        if row.get("returncode")!=0 or row.get("timed_out") or row.get("process_leaks")
    ]
    if bad:
        print(f"LOCAL-1 runner recorded {len(bad)} failing case(s); qualifier evidence retained")
        return 1
    print(f"LOCAL-1 {args.mode} runner completed {len(records)} case(s)")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
