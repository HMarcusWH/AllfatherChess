"""Aggregate all retained LOCAL-1 soak shards into one full-campaign report."""
from __future__ import annotations

import argparse
from pathlib import Path
import json

from .common import ROOT, load, policy, require, save, source_identity
from .runner import schedule
from .validate import qualify


def _campaign_manifests(root: Path) -> list[Path]:
    found=[]
    for path in root.rglob("manifest.json"):
        try:
            doc=load(path)
        except Exception:
            continue
        if doc.get("mode")=="soak" and isinstance(doc.get("shard"),dict):
            found.append(path)
    return sorted(found)


def aggregate(root: Path) -> dict:
    p=policy()
    expected_schedule=schedule(p,"soak")
    expected_by_id={row["id"]:row for row in expected_schedule}
    source=source_identity()
    manifests=_campaign_manifests(root)
    errors=[]
    rows=[]
    seen_shards={}
    seen_jobs={}

    for manifest_path in manifests:
        try:
            manifest=load(manifest_path)
            report_path=manifest_path.with_name("report.json")
            require(report_path.is_file(), f"missing shard report beside {manifest_path}")
            retained_report=load(report_path)
            recomputed=qualify(manifest_path.parent)
            require(recomputed.get("passed") is True,
                    f"shard raw evidence failed independent requalification: {recomputed.get('errors')}")
            require(retained_report == recomputed,
                    "retained shard report differs from independently recomputed evidence")
            report=recomputed
            shard=manifest.get("shard") or {}
            require(shard.get("count")==10 and type(shard.get("index")) is int,
                    "soak aggregate expects ten deterministic shards")
            index=shard["index"]
            require(0 <= index < 10 and index not in seen_shards,
                    "duplicate/out-of-range soak shard")
            require(manifest.get("source")==source,
                    f"shard {index}: source identity mismatch")
            require(manifest.get("status")=="completed" and not manifest.get("failures"),
                    f"shard {index}: campaign did not complete")
            require(report.get("execution_scope")=="partial_soak_shard",
                    f"shard {index}: report is not scoped as partial")
            require(report.get("shard_passed") is True and report.get("passed") is True,
                    f"shard {index}: execution failed")
            require(report.get("baseline_is_complete") is False and
                    report.get("aggregate_soak_complete") is False and
                    (report.get("claim_boundary") or {}).get("full_game_lifecycle") is False,
                    f"shard {index}: partial report overclaims full-campaign qualification")
            jobs=manifest.get("planned_jobs")
            require(isinstance(jobs,list), f"shard {index}: planned_jobs missing")
            require(report.get("observed_games")==2*len(jobs) and
                    report.get("validated_games")==2*len(jobs),
                    f"shard {index}: game count does not match planned jobs")
            for job in jobs:
                job_id=job.get("id")
                require(job_id in expected_by_id and expected_by_id[job_id]==job,
                        f"shard {index}: job differs from frozen soak schedule")
                require(job_id not in seen_jobs, f"duplicate soak job across shards: {job_id}")
                seen_jobs[job_id]=index
            seen_shards[index]=(manifest_path,report_path)
            rows.append({
                "shard":index,
                "campaign_id":manifest.get("campaign_id"),
                "jobs":len(jobs),
                "games":report.get("validated_games"),
                "manifest":str(manifest_path),
                "report":str(report_path),
            })
        except Exception as exc:
            errors.append(f"{manifest_path}: {type(exc).__name__}: {exc}")

    missing_shards=sorted(set(range(10))-set(seen_shards))
    if missing_shards:
        errors.append(f"missing soak shards: {missing_shards}")
    missing_jobs=sorted(set(expected_by_id)-set(seen_jobs))
    extra_jobs=sorted(set(seen_jobs)-set(expected_by_id))
    if missing_jobs:
        errors.append(f"missing soak jobs: {missing_jobs}")
    if extra_jobs:
        errors.append(f"unexpected soak jobs: {extra_jobs}")

    expected_games=2*len(expected_schedule)
    observed_games=sum(int(row["games"] or 0) for row in rows)
    passed=not errors and len(seen_shards)==10 and len(seen_jobs)==len(expected_schedule)
    return {
        "schema_version":1,
        "execution_scope":"aggregate_soak",
        "source":source,
        "shard_count":10,
        "shards":sorted(rows,key=lambda row:row["shard"]),
        "planned_jobs":len(expected_schedule),
        "expected_games":expected_games,
        "validated_games":observed_games,
        "errors":errors,
        "passed":passed,
        "baseline_is_complete":passed,
        "aggregate_soak_complete":passed,
        "claim_boundary":{
            "full_game_lifecycle":passed,
            "same_clock_descriptive_baseline":passed,
            "equal_compute":False,
            "elo":False,
            "superiority":False,
            "deployment":False,
        },
    }


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    report=aggregate(args.root.resolve())
    save(args.output.resolve(),report)
    print(json.dumps({
        "passed":report["passed"],
        "shards":len(report["shards"]),
        "planned_jobs":report["planned_jobs"],
        "validated_games":report["validated_games"],
        "errors":report["errors"],
    },sort_keys=True))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
