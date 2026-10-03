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
from tools.engine_opt.domain import load_execution_domain

PROFILES=(
 ("v1-current-cold",{"NNCacheSize":0,"MinibatchSize":32,"MaxPrefetch":32,"AdaptivePrefetch":False},None),
 ("v1-current-warm64",{"NNCacheSize":0,"MinibatchSize":32,"MaxPrefetch":32,"AdaptivePrefetch":False},64),
 ("auto-p0-c256k-cold",{"NNCacheSize":262144,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False},None),
 ("auto-p0-c256k-warm64",{"NNCacheSize":262144,"MinibatchSize":0,"MaxPrefetch":0,"AdaptivePrefetch":False},64),
 ("b1-p0-c256k-cold",{"NNCacheSize":262144,"MinibatchSize":1,"MaxPrefetch":0,"AdaptivePrefetch":False},None),
 ("b4-p0-c256k-cold",{"NNCacheSize":262144,"MinibatchSize":4,"MaxPrefetch":0,"AdaptivePrefetch":False},None),
 ("b7-p8-c256k-cold",{"NNCacheSize":262144,"MinibatchSize":7,"MaxPrefetch":8,"AdaptivePrefetch":False},None),
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
PROFILE_NAMES={name for name,_,_ in PROFILES}

def load_selection(path: Path) -> tuple[str,str,int]:
    doc=json.loads(path.read_text(encoding="utf-8"))
    lc0=((doc.get("selected") or {}).get("lc0") or {})
    qualification=doc.get("qualification") or {}
    selected=str(lc0.get("matrix_profile") or "")
    baseline=str(qualification.get("baseline_profile") or "v1-current-cold")
    repeats=qualification.get("confirmation_repeats",3)
    if selected not in PROFILE_NAMES:
        raise ValueError(f"selected matrix profile is not in the executable matrix: {selected!r}")
    if baseline not in PROFILE_NAMES:
        raise ValueError(f"baseline matrix profile is not in the executable matrix: {baseline!r}")
    if isinstance(repeats,bool) or not isinstance(repeats,int) or repeats < 2:
        raise ValueError("selection confirmation_repeats must be an integer >= 2")
    return selected,baseline,repeats

def profiles_for_run(selected_profile: str, baseline_profile: str, qualification_only: bool):
    if not qualification_only:
        return PROFILES
    required={selected_profile,baseline_profile}
    selected=tuple(row for row in PROFILES if row[0] in required)
    names={row[0] for row in selected}
    if names != required:
        raise ValueError(f"qualification profile set incomplete: expected {sorted(required)}, got {sorted(names)}")
    return selected

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary",type=Path,required=True)
    ap.add_argument("--weights",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--execution-domain",type=Path,required=True)
    ap.add_argument("--nodes",type=int,default=16)
    ap.add_argument("--deadline-ms",type=float,default=3500.0)
    ap.add_argument("--selection",type=Path,default=ROOT/"qualification/engine-opt-v2-selection.json")
    ap.add_argument("--quick",action="store_true")
    ap.add_argument(
        "--qualification-only",
        action="store_true",
        help="Run only the selected and baseline profiles required for qualification.",
    )
    args=ap.parse_args()
    selected_profile,baseline_profile,confirmation_repeats=load_selection(args.selection)
    source=source_identity(ROOT)
    execution_domain=load_execution_domain(
        args.execution_domain,
        expected_source_commit=source["commit"],
    )
    cases=load_epd(ROOT/"tests/fixtures/engine_opt/positions.epd")
    cases=cases[:3] if args.quick else cases
    if args.quick:
        confirmation_repeats=1
    rows=[]; summaries={}; repeat_summaries={}; errors=[]
    env={"OPENBLAS_NUM_THREADS":"1","GOTO_NUM_THREADS":"1","OMP_NUM_THREADS":"1"}
    common={
      "WeightsFile":str(args.weights.resolve()),"Backend":"blas","BackendOptions":"",
      "MaxConcurrentSearchers":1,"TaskWorkers":0,"Threads":1,"MultiPV":1,
      "ScoreType":"WDL_mu","DefectTelemetry":False,"UCI_Chess960":False,
    }
    confirmation_profiles={selected_profile,baseline_profile}
    active_profiles=profiles_for_run(selected_profile,baseline_profile,args.qualification_only)
    for profile,delta,warmup_nodes in active_profiles:
        per_repeat=[]
        repeat_count=confirmation_repeats if profile in confirmation_profiles else 1
        for repeat_index in range(repeat_count):
            profile_rows=[]
            for case in cases:
                try:
                    row=run_case(
                        binary=args.binary.resolve(),cwd=ROOT,family="lc0",case=case,
                        options={**common,**delta},nodes=args.nodes,deadline_ms=args.deadline_ms,
                        environment=env,args=["--show-hidden"],warmup_nodes=warmup_nodes,
                    )
                    row["profile"]=profile
                    row["repeat_index"]=repeat_index
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
                        "profile":profile,"repeat_index":repeat_index,"case_id":case.case_id,
                        "error":f"{type(exc).__name__}: {exc}"
                    })
            if profile_rows:
                per_repeat.append(summarize(profile_rows))
        repeat_summaries[profile]=per_repeat
        if per_repeat:
            summaries[profile]=per_repeat[0]
    payload={
      "schema_version":1,"kind":"lc0-cpu-runtime-matrix","source":source,
      "host":host_identity(),"execution_domain":execution_domain,
      "binary":{"path":str(args.binary),"sha256":sha256(args.binary)},
      "weights":{"path":str(args.weights),"sha256":sha256(args.weights)},
      "uci_args":["--show-hidden"],"nodes":args.nodes,"deadline_ms":args.deadline_ms,
      "profiles":[name for name,_,_ in active_profiles],"rows":rows,"summaries":summaries,
      "repeat_summaries":repeat_summaries,
      "confirmation":{"selected_profile":selected_profile,"baseline_profile":baseline_profile,
                      "repeats":confirmation_repeats},
      "execution_mode":"qualification_only" if args.qualification_only else "full_matrix",
      "errors":errors,
      "claim_boundary":{"strength":False,"elo":False,"selection_is_automatic":False},
    }
    write_report(args.output,payload)
    print(json.dumps({"rows":len(rows),"errors":len(errors),"output":str(args.output),
                      "selected_profile":selected_profile,"confirmation_repeats":confirmation_repeats},sort_keys=True))
    return 0 if rows and not errors else 1

if __name__=="__main__":
    raise SystemExit(main())
