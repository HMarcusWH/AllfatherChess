#!/usr/bin/env python3
"""Aggregate exact-head ENGINE-OPT-V2 measurements and lifecycle evidence."""
from __future__ import annotations
import argparse,json,math,subprocess
from pathlib import Path

class QualificationError(RuntimeError):
    pass

def require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationError(message)

def load(path: Path) -> dict:
    value=json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value,dict),f"{path}: JSON root must be an object")
    return value

def find_one(root: Path, pattern: str) -> Path:
    found=sorted(root.glob(pattern))
    require(len(found)==1,f"{pattern}: expected one file, found {len(found)}")
    return found[0]

def current_source(root: Path) -> str:
    return subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()

def stable_repeat_summaries(matrix: dict, profile: str, repeats: int, cases: int):
    rows=(matrix.get("repeat_summaries") or {}).get(profile) or []
    require(len(rows)==repeats,f"{profile}: expected {repeats} confirmation repeats, found {len(rows)}")
    vectors=[]
    for index,row in enumerate(rows):
        require(row.get("cases")==cases,f"{profile} repeat {index}: incomplete frozen corpus")
        require(row.get("completion_rate")==1.0,f"{profile} repeat {index}: incomplete search completion")
        vector=tuple(row.get("bestmoves") or [])
        require(len(vector)==cases,f"{profile} repeat {index}: malformed bestmove vector")
        vectors.append(vector)
    require(len(set(vectors))==1,f"{profile}: bestmove vector is not repeatable across confirmation runs")
    return rows,vectors[0]

def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    artifact_root=args.root.resolve()
    repo=Path(__file__).resolve().parents[1]
    source=current_source(repo)
    errors=[]; details={}
    selection=load(repo/"qualification/engine-opt-v2-selection.json")

    def gate(name,work):
        try:
            details[name]=work()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    def check_lc0():
        matrix=load(find_one(artifact_root,"engine-opt-v2-lc0-matrix/**/lc0.json"))
        require((matrix.get("source") or {}).get("commit")==source,"LC0 matrix source is not exact head")
        require(not matrix.get("errors"),"LC0 matrix contains failed cases")
        lc0=((selection.get("selected") or {}).get("lc0") or {})
        q=selection.get("qualification") or {}
        selected_profile=str(lc0.get("matrix_profile") or "")
        baseline_profile=str(q.get("baseline_profile") or "v1-current-cold")
        repeats=q.get("confirmation_repeats"); cases=q.get("corpus_cases",8)
        require(isinstance(repeats,int) and not isinstance(repeats,bool) and repeats>=2,
                "selection confirmation_repeats is invalid")
        require(isinstance(cases,int) and not isinstance(cases,bool) and cases>=1,
                "selection corpus_cases is invalid")
        c=matrix.get("confirmation") or {}
        require(c.get("selected_profile")==selected_profile,"matrix confirmed a different selected profile")
        require(c.get("baseline_profile")==baseline_profile,"matrix confirmed a different baseline profile")
        require(c.get("repeats")==repeats,"matrix confirmation repeat count differs from selection")
        baseline_rows,baseline_vector=stable_repeat_summaries(matrix,baseline_profile,repeats,cases)
        selected_rows,selected_vector=stable_repeat_summaries(matrix,selected_profile,repeats,cases)
        require(selected_vector==baseline_vector,"selected LC0 profile changed frozen-corpus bestmoves")
        worst_selected=max(float(r["median_wall_ms"]) for r in selected_rows)
        fastest_baseline=min(float(r["median_wall_ms"]) for r in baseline_rows)
        ratio=worst_selected/fastest_baseline
        require(ratio<=float(q.get("max_median_wall_ratio",0.40)),
                "selected LC0 profile lost the measured efficiency advantage")
        observed_max=max(float(r["max_wall_ms"]) for r in selected_rows)
        require(observed_max<=float(q.get("max_wall_ms",600.0)),
                "selected LC0 profile exceeds the frozen n16 wall bound")
        raw=[r for r in matrix.get("rows",[]) if r.get("profile")==selected_profile]
        require(raw,"selected LC0 profile has no raw matrix rows")
        for row in raw:
            opts=row.get("options") or {}
            require(opts.get("NNCacheSize")==lc0.get("nn_cache_size"),"selected matrix NNCacheSize differs from runtime selection")
            require(opts.get("MinibatchSize")==lc0.get("minibatch_size"),"selected matrix MinibatchSize differs from runtime selection")
            require(opts.get("MaxPrefetch")==lc0.get("max_prefetch"),"selected matrix MaxPrefetch differs from runtime selection")
            require(opts.get("AdaptivePrefetch") is lc0.get("adaptive_prefetch"),"selected matrix AdaptivePrefetch differs from runtime selection")
            warm=row.get("warmup"); warm_nodes=lc0.get("warmup_nodes")
            if warm_nodes is None:
                require(warm is None,"selected matrix used warmup despite no-warmup runtime selection")
            else:
                require(isinstance(warm,dict) and warm.get("nodes")==warm_nodes,
                        "selected matrix warmup differs from runtime selection")
        return {
            "baseline_profile":baseline_profile,"selected_profile":selected_profile,
            "confirmation_repeats":repeats,"bestmoves":list(selected_vector),
            "baseline_median_wall_ms_range":[min(float(r["median_wall_ms"]) for r in baseline_rows),max(float(r["median_wall_ms"]) for r in baseline_rows)],
            "selected_median_wall_ms_range":[min(float(r["median_wall_ms"]) for r in selected_rows),worst_selected],
            "wall_ratio_worst_selected_over_fastest_baseline":ratio,
            "selected_max_wall_ms":observed_max,
            "native_work_vectors":[r.get("native_work_values") for r in selected_rows],
            "selected_max_cpu_ms":max(float(r.get("max_cpu_ms") or 0.0) for r in selected_rows),
            "execution_domain":matrix.get("execution_domain"),
        }

    def check_ab():
        root=artifact_root/"engine-opt-v2-constituent-ab"
        lc0=load(find_one(root,"**/lc0-derived-vs-pristine.json"))
        rr=load(find_one(root,"**/reckless-derived-vs-pristine.json"))
        sf=load(find_one(root,"**/stockfish-pgo-vs-no-pgo.json"))
        sfh=load(find_one(root,"**/stockfish-hash-matrix.json"))
        rrh=load(find_one(root,"**/reckless-hash-matrix.json"))
        for label,doc in (("LC0 A/B",lc0),("Reckless A/B",rr),("Stockfish PGO A/B",sf),("Stockfish hash",sfh),("Reckless hash",rrh)):
            require((doc.get("source") or {}).get("commit")==source,f"{label} source is not exact head")
            require(not doc.get("errors"),f"{label} contains failed cases")
        require((lc0.get("comparison") or {}).get("bestmove_agreement")==1.0,"derived LC0 disagrees with pristine control")
        ratio=float(lc0["comparison"]["wall_ratio_right_over_left"])
        require(0.85<=ratio<=1.15,"derived LC0 shows a material disabled-feature runtime regression")
        require((rr.get("comparison") or {}).get("bestmove_agreement")==1.0,"derived Reckless disagrees with pristine control")
        require(float(rr["comparison"]["left_median_wall_ms"])<=1.10*float(rr["comparison"]["right_median_wall_ms"]),
                "derived Reckless materially regressed against pristine control")
        require((sf.get("comparison") or {}).get("bestmove_agreement")==1.0,"PGO Stockfish changed frozen-corpus bestmoves")
        require(float(sf["comparison"]["left_median_wall_ms"])<=1.05*float(sf["comparison"]["right_median_wall_ms"]),
                "PGO Stockfish lost its acceptable fixed-node runtime envelope")
        sf16=float(sfh["summaries"]["16"]["median_wall_ms"]); sfbest=min(float(x["median_wall_ms"]) for x in sfh["summaries"].values())
        rr16=float(rrh["summaries"]["16"]["median_wall_ms"]); rrbest=min(float(x["median_wall_ms"]) for x in rrh["summaries"].values())
        require(sf16<=1.03*sfbest,"selected Stockfish Hash=16 fell outside the 3% efficiency band")
        require(rr16<=1.03*rrbest,"selected Reckless Hash=16 fell outside the 3% efficiency band")
        return {"lc0_bestmove_agreement":1.0,"lc0_pristine_over_derived_wall_ratio":ratio,
                "reckless_bestmove_agreement":1.0,"stockfish_pgo_bestmove_agreement":1.0,
                "stockfish_hash16_over_best":sf16/sfbest,"reckless_hash16_over_best":rr16/rrbest}

    def check_candidate():
        d=load(find_one(artifact_root,"engine-opt-v2-candidate/**/test-results/engine-opt-v2/report.json"))
        require(d.get("source_commit")==source,"candidate qualification source is not exact head")
        require(d.get("passed") is True and d.get("promotion_ready") is True,"selected candidate identity is not promotion-ready")
        return {"promotion_ready":True,"bundle_manifest_sha256":d.get("bundle_manifest_sha256"),"evidence_sha256":d.get("evidence_sha256")}

    def check_hybrid():
        root=artifact_root/"engine-opt-v2-hybrid-evidence"
        d=load(find_one(root,"**/test-results/online-hybrid-v2/report.json"))
        require(d.get("passed") is True,"real G3-v2 qualification did not pass")
        p=d.get("positive_case") or {}
        require(p.get("authority")=="HYBRID" and p.get("emitted_move") and p.get("emitted_move")!=p.get("anchor_move"),
                "G3-v2 lacks a genuine non-anchor authority witness")
        require(p.get("resource_qualified") is True and p.get("route_resource_qualified") is True,
                "G3-v2 positive case lacks qualified resource evidence")
        docs=[load(x) for x in sorted(root.glob("**/resource.json"))]
        require(docs,"G3-v2 artifact contains no resource reports")
        explore=[]; verify=[]
        for doc in docs:
            require(doc.get("qualified") is True,"G3-v2 resource report is not qualified")
            for stage in doc.get("stages") or []:
                if stage.get("instance")!="lc0-shadow" or not stage.get("complete"): continue
                cpu=stage.get("cpu_ms")
                require(isinstance(cpu,(int,float)) and not isinstance(cpu,bool) and math.isfinite(float(cpu)) and float(cpu)>=0,
                        "LC0 stage CPU measurement is missing or invalid")
                if stage.get("phase")=="EXPLORE": explore.append(float(cpu))
                elif stage.get("phase") in {"VERIFY","VERIFY_EXTENSION","STAGED_VERIFY"}: verify.append(float(cpu))
        require(explore and verify,"G3-v2 resource evidence lacks LC0 EXPLORE/VERIFY stages")
        estimates=((selection.get("selected") or {}).get("resource_estimates_ms") or {})
        er=float((estimates.get("explore") or {}).get("lc0")); vr=float((estimates.get("verify") or {}).get("lc0"))
        require(max(explore)<=er,"selected LC0 EXPLORE reservation is below measured CPU")
        require(max(verify)<=vr,"selected LC0 VERIFY reservation is below measured CPU")
        return {"case":p.get("case"),"anchor_move":p.get("anchor_move"),"emitted_move":p.get("emitted_move"),
                "lc0_explore_cpu_ms_max":max(explore),"lc0_verify_cpu_ms_max":max(verify),
                "lc0_explore_reserved_ms":er,"lc0_verify_reserved_ms":vr,
                "execution_domain":d.get("execution_domain")}

    def check_local1():
        found=[]
        for path in sorted((artifact_root/"engine-opt-v2-local1").glob("**/report.json")):
            try: row=load(path)
            except Exception: continue
            if row.get("execution_scope")=="required_local1" and "campaign_id" in row: found.append((path,row))
        require(len(found)==1,f"expected one retained LOCAL-1-v2 report, found {len(found)}")
        path,d=found[0]
        require(d.get("passed") is True,f"LOCAL-1-v2 did not pass: {d.get('errors')}")
        require((d.get("claim_boundary") or {}).get("full_game_lifecycle") is True,"LOCAL-1-v2 did not qualify full-game lifecycle")
        require(d.get("validated_games")==28,"LOCAL-1-v2 did not validate all 28 required games")
        manifests=sorted(path.parent.glob("manifest.json")); require(len(manifests)==1,"LOCAL-1-v2 campaign manifest missing")
        require((load(manifests[0]).get("source") or {}).get("commit")==source,"LOCAL-1-v2 source is not exact head")
        return {"campaign_id":d.get("campaign_id"),"validated_games":d.get("validated_games"),
                "authority_counts":d.get("authority_counts"),"actual_anchor_overrides":d.get("actual_anchor_overrides"),
                "execution_domain":d.get("execution_domain")}

    def check_execution_domain():
        names=("lc0","hybrid","local1_v2")
        rows=[]
        for name in names:
            require(name in details,f"{name} evidence missing before execution-domain gate")
            domain=(details[name].get("execution_domain") or {})
            require(domain.get("complete") is True,f"{name} execution-domain evidence is incomplete")
            host_id=domain.get("host_qualification_domain_id")
            runtime_id=domain.get("runtime_substrate_id")
            require(isinstance(host_id,str) and host_id,f"{name} host qualification domain missing")
            require(isinstance(runtime_id,str) and runtime_id,f"{name} runtime substrate id missing")
            rows.append((name,host_id,runtime_id))
        require(len({row[1] for row in rows})==1,
                "claim-bearing runtime evidence spans multiple host qualification domains")
        require(len({row[2] for row in rows})==1,
                "claim-bearing runtime evidence spans multiple runtime substrates")
        return {
            "host_qualification_domain_id":rows[0][1],
            "runtime_substrate_id":rows[0][2],
            "members":[{"name":name,"host_qualification_domain_id":host_id,
                        "runtime_substrate_id":runtime_id}
                       for name,host_id,runtime_id in rows],
        }

    gate("lc0",check_lc0); gate("constituent_ab",check_ab); gate("candidate",check_candidate)
    gate("hybrid",check_hybrid); gate("local1_v2",check_local1)
    gate("execution_domain",check_execution_domain)
    passed=not errors
    report={"schema_version":1,"profile_id":"engine-opt-v2-aggregate","source_commit":source,"passed":passed,
            "errors":errors,"details":details,
            "claim_boundary":{"engine_profile_qualified":passed,"full_game_lifecycle":bool(passed and details.get("local1_v2")),
                              "strength":False,"elo":False,"equal_compute":False,"deployment":False}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True))
    return 0 if passed else 1

if __name__=="__main__":
    raise SystemExit(main())
