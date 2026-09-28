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
 ("v1-current-cold",{"NNCacheSize":0,"MinibatchSize":32,"MaxPrefetch":32,"AdaptivePrefetch":False},None),
 ("v1-current-warm64",{"NNCacheSize":0,"MinibatchSize":32,"MaxPrefetch":32,"AdaptivePrefetch":False},64),
 ("auto-p0-c256k-cold",{"NNCacheSize":262144,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False},None),
 ("auto-p0-c256k-warm64",{"NNCacheSize":262144,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False},64),
 ("b7-p0-c256k-warm64",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":0,"AdaptivePrefetch":False},64),
 ("b7-p8-c256k-warm64",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":8,"AdaptivePrefetch":False},64),
 ("b7-p12-c256k-warm64",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":12,"AdaptivePrefetch":False},64),
 ("b7-p32-c256k-warm64",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":32,"AdaptivePrefetch":False},64),
 ("auto-p0-c2m-warm64",{"NNCacheSize":2000000,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False},64),
 ("b7-p12-c2m-warm64",{"NNCacheSize":2000000,"MinibatchSize":7,"MaxPrefetch":12,"AdaptivePrefetch":False},64),
 ("auto-adaptive-c2m-warm64",{"NNCacheSize":2000000,"MinibatchSize":0,"MaxPrefetch":32,"AdaptivePrefetch":True},64),
 ("b7-p32-c2m-telemetry",{"NNCacheSize":2000000,"MinibatchSize":7,"MaxPrefetch":32,"AdaptivePrefetch":False,"DefectTelemetry":True,"DefectTelemetryIterations":0},64),
 ("auto-adaptive-c2m-telemetry",{"NNCacheSize":2000000,"MinibatchSize":0,"MaxPrefetch":32,"AdaptivePrefetch":True,"DefectTelemetry":True,"DefectTelemetryIterations":0},64),
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
    for profile,delta,warmup_nodes in PROFILES:
        profile_rows=[]
        for case in cases:
            try:
                row=run_case(
                    binary=args.binary.resolve(),cwd=ROOT,family="lc0",case=case,
                    options={**common,**delta},nodes=args.nodes,deadline_ms=args.deadline_ms,
                    environment=env,args=["--show-hidden"],warmup_nodes=warmup_nodes,
                )
                row["profile"]=profile
                for transcript_line in row.get("transcript", []):
                    marker="<< info string DEFECT_TELEMETRY_SUMMARY "
                    if transcript_line.startswith(marker):
                        try:
                            row["defect_telemetry"]=json.loads(transcript_line[len(marker):])
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(
                                f"{profile}/{case.case_id}: malformed defect telemetry summary: {exc}"
                            ) from exc
                if delta.get("DefectTelemetry") is True and "defect_telemetry" not in row:
                    raise RuntimeError(
                        f"{profile}/{case.case_id}: telemetry-enabled search emitted no summary"
                    )
                rows.append(row)
                profile_rows.append(row)
            except Exception as exc:
                errors.append({
                    "profile":profile,"case_id":case.case_id,
                    "error":f"{type(exc).__name__}: {exc}"
                })
        if profile_rows:
            summaries[profile]=summarize(profile_rows)
    payload={
      "schema_version":1,"kind":"lc0-cpu-runtime-matrix","source":source_identity(ROOT),
      "host":host_identity(),"binary":{"path":str(args.binary),"sha256":sha256(args.binary)},
      "weights":{"path":str(args.weights),"sha256":sha256(args.weights)},
      "uci_args":["--show-hidden"],"nodes":args.nodes,"deadline_ms":args.deadline_ms,
      "profiles":[name for name,_,_ in PROFILES],"rows":rows,"summaries":summaries,
      "errors":errors,
      "claim_boundary":{"strength":False,"elo":False,"selection_is_automatic":False},
    }
    write_report(args.output,payload)
    print(json.dumps({"rows":len(rows),"errors":len(errors),"output":str(args.output)},sort_keys=True))
    return 0 if rows and not errors else 1

if __name__=="__main__":
    raise SystemExit(main())
