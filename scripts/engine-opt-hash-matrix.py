#!/usr/bin/env python3
"""Measure transposition-hash sensitivity without making a strength claim."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.runner import run_case
from tools.engine_opt.compare import summarize
from tools.engine_opt.report import host_identity,source_identity,sha256,write_report

HASHES=(16,32,64,128,256)

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family",choices=("stockfish","reckless"),required=True)
    ap.add_argument("--binary",type=Path,required=True)
    ap.add_argument("--nodes",type=int,default=200000)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    cases=load_epd(ROOT/"tests/fixtures/engine_opt/positions.epd")
    rows=[]; summaries={}; errors=[]
    for hash_mb in HASHES:
        group=[]
        options={"Threads":1,"Hash":hash_mb,"MultiPV":1,"UCI_Chess960":False}
        if args.family=="reckless":
            options["Minimal"]=False
        for case in cases:
            try:
                row=run_case(
                    binary=args.binary.resolve(),cwd=ROOT,family=args.family,
                    case=case,options=dict(options),nodes=args.nodes,
                    deadline_ms=10000.0,
                )
                row["hash_mb"]=hash_mb
                rows.append(row)
                group.append(row)
            except Exception as exc:
                errors.append({
                    "hash_mb":hash_mb,"case_id":case.case_id,
                    "error":f"{type(exc).__name__}: {exc}",
                })
        if group:
            summaries[str(hash_mb)]=summarize(group)
    payload={
        "schema_version":1,
        "kind":"engine-opt-hash-matrix",
        "family":args.family,
        "source":source_identity(ROOT),
        "host":host_identity(),
        "binary":{"path":str(args.binary),"sha256":sha256(args.binary)},
        "nodes":args.nodes,
        "hash_mb":list(HASHES),
        "rows":rows,
        "summaries":summaries,
        "errors":errors,
        "claim_boundary":{
            "strength":False,"elo":False,"benchmark_only":True,
        },
    }
    write_report(args.output,payload)
    print(json.dumps({
        "family":args.family,"rows":len(rows),"errors":len(errors),
        "summaries":summaries,
    },sort_keys=True))
    return 0 if len(rows)==len(cases)*len(HASHES) and not errors else 1

if __name__=="__main__":
    raise SystemExit(main())
