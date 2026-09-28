#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.runner import run_case
from tools.engine_opt.compare import summarize
from tools.engine_opt.report import host_identity,source_identity,sha256,write_report

PROFILES=(
 ("v1-current",{"NNCacheSize":0,"MinibatchSize":32,"MaxPrefetch":32,"AdaptivePrefetch":False}),
 ("auto-p0-c256k",{"NNCacheSize":262144,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False}),
 ("b7-p0-c256k",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":0,"AdaptivePrefetch":False}),
 ("b7-p8-c256k",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":8,"AdaptivePrefetch":False}),
 ("b7-p12-c256k",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":12,"AdaptivePrefetch":False}),
 ("b7-p32-c256k",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":32,"AdaptivePrefetch":False}),
 ("auto-p0-c2m",{"NNCacheSize":2000000,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False}),
 ("b7-p12-c2m",{"NNCacheSize":2000000,"MinibatchSize":7,"MaxPrefetch":12,"AdaptivePrefetch":False}),
 ("auto-adaptive-c2m",{"NNCacheSize":2000000,"MinibatchSize":0,"MaxPrefetch":32,"AdaptivePrefetch":True}),
)

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary",type=Path,required=True)
    ap.add_argument("--weights",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--nodes",type=int,default=16)
    ap.add_argument("--deadline-ms",type=float,default=3500.0)
    ap.add_argument("--quick",action="store_true")
    args=ap.parse_args()
    cases=load_epd(ROOT/"tests/fixtures/engine_opt/positions.epd")
    cases=cases[:3] if args.quick else cases
    rows=[]; summaries={}; errors=[]
    env={"OPENBLAS_NUM_THREADS":"1","GOTO_NUM_THREADS":"1","OMP_NUM_THREADS":"1"}
    common={
      "WeightsFile":str(args.weights.resolve()),"Backend":"blas","BackendOptions":"",
      "MaxConcurrentSearchers":1,"TaskWorkers":0,"Threads":1,"MultiPV":1,
      "ScoreType":"WDL_mu","DefectTelemetry":False,"UCI_Chess960":False,
    }
    for profile,delta in PROFILES:
        profile_rows=[]
        for case in cases:
            try:
                row=run_case(
                    binary=args.binary.resolve(),cwd=ROOT,family="lc0",case=case,
                    options={**common,**delta},nodes=args.nodes,deadline_ms=args.deadline_ms,
                    environment=env,
                )
                row["profile"]=profile
                rows.append(row)
                profile_rows.append(row)
            except Exception as exc:
                errors.append({"profile":profile,"case_id":case.case_id,"error":f"{type(exc).__name__}: {exc}"})
        if profile_rows:
            summaries[profile]=summarize(profile_rows)
    payload={
      "schema_version":1,"kind":"lc0-cpu-runtime-matrix","source":source_identity(ROOT),
      "host":host_identity(),"binary":{"path":str(args.binary),"sha256":sha256(args.binary)},
      "weights":{"path":str(args.weights),"sha256":sha256(args.weights)},
      "nodes":args.nodes,"deadline_ms":args.deadline_ms,
      "profiles":[name for name,_ in PROFILES],"rows":rows,"summaries":summaries,"errors":errors,
      "claim_boundary":{"strength":False,"elo":False,"selection_is_automatic":False},
    }
    write_report(args.output,payload)
    print(json.dumps({"rows":len(rows),"errors":len(errors),"output":str(args.output)},sort_keys=True))
    return 0 if rows else 1

if __name__=="__main__":
    raise SystemExit(main())
