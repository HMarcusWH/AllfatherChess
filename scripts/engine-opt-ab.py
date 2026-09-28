#!/usr/bin/env python3
"""Frozen-corpus constituent A/B benchmark for ENGINE-OPT-V2."""
from __future__ import annotations
import argparse,json,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.runner import run_case
from tools.engine_opt.compare import bestmove_agreement,summarize
from tools.engine_opt.report import host_identity,source_identity,sha256,write_report

def options_for(family: str, weights: Path | None) -> tuple[dict,list[str],dict[str,str]]:
    if family=="stockfish":
        return (
            {"Threads":1,"Hash":16,"MultiPV":1,"UCI_Chess960":False},
            [],
            {},
        )
    if family=="reckless":
        return (
            {"Threads":1,"Hash":16,"MultiPV":1,"Minimal":False,"UCI_Chess960":False},
            [],
            {},
        )
    if family=="lc0":
        if weights is None:
            raise ValueError("LC0 A/B requires --weights")
        return (
            {
                "WeightsFile":str(weights.resolve()),
                "Backend":"blas",
                "BackendOptions":"",
                "NNCacheSize":262144,
                "MinibatchSize":0,
                "MaxConcurrentSearchers":1,
                "TaskWorkers":0,
                "Threads":1,
                "MultiPV":1,
                "ScoreType":"WDL_mu",
                "MaxPrefetch":0,
                "UCI_Chess960":False,
            },
            ["--show-hidden"],
            {
                "OPENBLAS_NUM_THREADS":"1",
                "GOTO_NUM_THREADS":"1",
                "OMP_NUM_THREADS":"1",
            },
        )
    raise ValueError(f"unsupported family: {family}")

def median_optional(rows: list[dict], key: str) -> float | None:
    values=[
        row["metrics"].get(key)
        for row in rows
        if isinstance(row["metrics"].get(key),(int,float))
        and not isinstance(row["metrics"].get(key),bool)
    ]
    return None if not values else float(statistics.median(values))

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family",choices=("stockfish","reckless","lc0"),required=True)
    ap.add_argument("--left",type=Path,required=True)
    ap.add_argument("--right",type=Path,required=True)
    ap.add_argument("--left-label",required=True)
    ap.add_argument("--right-label",required=True)
    ap.add_argument("--weights",type=Path)
    ap.add_argument("--nodes",type=int,required=True)
    ap.add_argument("--deadline-ms",type=float,default=10000.0)
    ap.add_argument("--warmup-nodes",type=int)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()

    cases=load_epd(ROOT/"tests/fixtures/engine_opt/positions.epd")
    options,engine_args,environment=options_for(args.family,args.weights)
    sides=(
        (args.left_label,args.left.resolve()),
        (args.right_label,args.right.resolve()),
    )
    by_side: dict[str,list[dict]]={}
    errors=[]
    for label,binary in sides:
        rows=[]
        for case in cases:
            try:
                row=run_case(
                    binary=binary,cwd=ROOT,family=args.family,case=case,
                    options=dict(options),nodes=args.nodes,
                    deadline_ms=args.deadline_ms,environment=dict(environment),
                    args=list(engine_args),
                    warmup_nodes=(args.warmup_nodes if args.family=="lc0" else None),
                )
                row["side"]=label
                rows.append(row)
            except Exception as exc:
                errors.append({
                    "side":label,"case_id":case.case_id,
                    "error":f"{type(exc).__name__}: {exc}",
                })
        by_side[label]=rows

    left_rows=by_side[args.left_label]
    right_rows=by_side[args.right_label]
    comparison=None
    if left_rows and right_rows:
        left_wall=median_optional(left_rows,"wall_ms")
        right_wall=median_optional(right_rows,"wall_ms")
        left_cpu=median_optional(left_rows,"cpu_ms")
        right_cpu=median_optional(right_rows,"cpu_ms")
        comparison={
            "bestmove_agreement":bestmove_agreement(left_rows,right_rows),
            "left_median_wall_ms":left_wall,
            "right_median_wall_ms":right_wall,
            "wall_ratio_right_over_left":(
                None if left_wall in (None,0) or right_wall is None
                else right_wall/left_wall
            ),
            "left_median_cpu_ms":left_cpu,
            "right_median_cpu_ms":right_cpu,
            "cpu_ratio_right_over_left":(
                None if left_cpu in (None,0) or right_cpu is None
                else right_cpu/left_cpu
            ),
        }
    payload={
        "schema_version":1,
        "kind":"engine-opt-constituent-ab",
        "family":args.family,
        "source":source_identity(ROOT),
        "host":host_identity(),
        "nodes":args.nodes,
        "deadline_ms":args.deadline_ms,
        "warmup_nodes":args.warmup_nodes if args.family=="lc0" else None,
        "options":options,
        "args":engine_args,
        "environment":environment,
        "binaries":{
            args.left_label:{
                "path":str(args.left),
                "sha256":sha256(args.left),
            },
            args.right_label:{
                "path":str(args.right),
                "sha256":sha256(args.right),
            },
        },
        "summaries":{
            label:(summarize(rows) if rows else None)
            for label,rows in by_side.items()
        },
        "comparison":comparison,
        "rows":[row for rows in by_side.values() for row in rows],
        "errors":errors,
        "claim_boundary":{
            "strength":False,
            "elo":False,
            "benchmark_only":True,
        },
    }
    write_report(args.output,payload)
    print(json.dumps({
        "family":args.family,
        "left_rows":len(left_rows),
        "right_rows":len(right_rows),
        "errors":len(errors),
        "comparison":comparison,
    },sort_keys=True))
    return 0 if len(left_rows)==len(cases) and len(right_rows)==len(cases) and not errors else 1

if __name__=="__main__":
    raise SystemExit(main())
