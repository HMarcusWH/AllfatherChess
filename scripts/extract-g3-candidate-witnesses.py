#!/usr/bin/env python3
"""Extract deterministic candidate G3 witnesses from retained LOCAL-1 evidence."""
from __future__ import annotations
import argparse,hashlib,json
from collections import defaultdict
from pathlib import Path

def load(path:Path):
    return json.loads(path.read_text(encoding="utf-8"))

def require(ok,msg):
    if not ok: raise RuntimeError(msg)

def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campaign",type=Path,required=True)
    ap.add_argument("--source-commit",required=True)
    ap.add_argument("--workflow-run",type=int,required=True)
    ap.add_argument("--artifact-id",type=int,required=True)
    ap.add_argument("--artifact-name",required=True)
    ap.add_argument("--artifact-digest",required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    campaign=args.campaign.resolve()
    by=defaultdict(list)
    for final_path in sorted(campaign.glob("base-*/sessions/allfather-g3/*/replays/*/decision/final.json")):
        run=final_path.parents[1]
        job=run.relative_to(campaign).parts[0]
        decision=(load(final_path).get("decision") or {})
        if decision.get("authority")!="HYBRID" or not decision.get("proposal_move") or decision.get("proposal_move")==decision.get("anchor_move"):
            continue
        manifest=load(run/"manifest.json")
        snap=decision.get("authorization_snapshot") or {}
        route=load(run/"route.json")
        resource=load(run/"resource.json")
        time_plan=manifest.get("time_plan") or {}
        position=manifest.get("position") or {}
        qualified=(
            (decision.get("authorization") or {}).get("authorized") is True
            and snap.get("route_action")=="BUY_STAGED_VERIFY"
            and snap.get("route_buy_extension") is True
            and snap.get("staged_complete") is True
            and snap.get("terminal_source")=="staged_verification"
            and (manifest.get("clock_outcome") or {}).get("output_within_deadline") is True
            and resource.get("qualified") is True
            and (route.get("resource_measurement") or {}).get("qualified") is True
            and (route.get("envelope_claim") or {}).get("claimed") is True
            and str(position.get("command") or "").startswith("position startpos")
            and float(time_plan.get("available_clock_ms") or 0)>=10000
        )
        if not qualified: continue
        by[job].append({
            "id":f"discovery-{job}-g{int(manifest['generation']):06d}",
            "moves":" ".join(position.get("moves") or []),
            "command":(manifest.get("external_request") or {}).get("command"),
            "source_set":"discovery_local1_v1",
            "discovery":{
                "job_id":job,"generation":manifest["generation"],"run_id":manifest["run_id"],
                "position_id":position["position_id"],"anchor_move":decision["anchor_move"],
                "proposal_move":decision["proposal_move"],"available_clock_ms":time_plan["available_clock_ms"],
                "hard_budget_ms":time_plan["hard_budget_ms"],"output_within_deadline":True,
            },
        })
    require(len(by)==4,f"expected four override-bearing base jobs, found {sorted(by)}")
    selected=[]; seen=set()
    for job in sorted(by):
        picked=[]
        for row in sorted(by[job],key=lambda x:(x["discovery"]["generation"],x["discovery"]["run_id"])):
            pid=row["discovery"]["position_id"]
            if pid in seen: continue
            picked.append(row); seen.add(pid)
            if len(picked)==4: break
        require(len(picked)==4,f"{job}: expected four distinct eligible positions")
        selected.extend(picked)
    doc={
      "schema_version":1,"corpus_id":"g3-candidate-local1-discovery-v1",
      "source":{"repository":"HMarcusWH/AllfatherChess","source_commit":args.source_commit,
        "workflow_run":args.workflow_run,"artifact_id":args.artifact_id,"artifact_name":args.artifact_name,
        "artifact_digest":args.artifact_digest},
      "selection_algorithm":{"id":"qualified-natural-override-base-round-robin-v1",
        "description":"From candidate LOCAL-1 base-* allfather-g3 replays, retain only valid non-anchor HYBRID decisions with granted authorization, BUY_STAGED_VERIFY, completed staged verification, claimed envelope, qualified resource evidence, output within deadline, startpos history, and available_clock_ms >= 10000. Deduplicate position_id, sort each job by (generation, run_id), sort job ids lexicographically, and take the earliest four distinct positions from each override-bearing base job. No validation-run result may alter this frozen set.",
        "required_per_job":4,"minimum_available_clock_ms":10000,"case_count":16},
      "claim_boundary":{"discovery_only":True,"validation_authority":False,"promotion_authority":False,
        "strength":False,"elo":False},
      "cases":selected,
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(doc,separators=(",",":"),sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"cases":len(selected),"sha256":hashlib.sha256(args.output.read_bytes()).hexdigest()},sort_keys=True))
    return 0
if __name__=="__main__": raise SystemExit(main())
