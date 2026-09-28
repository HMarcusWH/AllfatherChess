#!/usr/bin/env python3
"""Aggregate exact-head ENGINE-OPT-V2 measurements and lifecycle evidence."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
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
    return subprocess.check_output(
        ["git","-C",str(root),"rev-parse","HEAD"],text=True
    ).strip()


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    artifact_root=args.root.resolve()
    repo=Path(__file__).resolve().parents[1]
    source=current_source(repo)
    errors=[]
    details={}

    try:
        matrix=load(find_one(
            artifact_root,
            "engine-opt-v2-lc0-matrix/**/lc0.json",
        ))
        require((matrix.get("source") or {}).get("commit")==source,
                "LC0 matrix source is not exact head")
        require(not matrix.get("errors"),"LC0 matrix contains failed cases")
        summaries=matrix.get("summaries") or {}
        v1=summaries.get("v1-current-cold") or {}
        selected=summaries.get("auto-p0-c256k-cold") or {}
        require(v1.get("cases")==selected.get("cases")==8,
                "LC0 matrix lacks complete frozen corpus")
        require(v1.get("bestmoves")==selected.get("bestmoves"),
                "selected LC0 profile changed frozen-corpus bestmoves")
        v1_wall=float(v1["median_wall_ms"])
        selected_wall=float(selected["median_wall_ms"])
        require(selected_wall <= 0.40*v1_wall,
                "selected LC0 profile lost the measured efficiency advantage")
        require(float(selected["max_wall_ms"]) <= 500.0,
                "selected LC0 profile exceeds the frozen n16 wall bound")
        details["lc0"]={
            "v1_median_wall_ms":v1_wall,
            "selected_median_wall_ms":selected_wall,
            "wall_ratio":selected_wall/v1_wall,
            "selected_max_wall_ms":float(selected["max_wall_ms"]),
        }

        ab_root=artifact_root/"engine-opt-v2-constituent-ab"
        lc0_ab=load(find_one(ab_root,"**/lc0-derived-vs-pristine.json"))
        reckless_ab=load(find_one(ab_root,"**/reckless-derived-vs-pristine.json"))
        stockfish_ab=load(find_one(ab_root,"**/stockfish-pgo-vs-no-pgo.json"))
        sf_hash=load(find_one(ab_root,"**/stockfish-hash-matrix.json"))
        rr_hash=load(find_one(ab_root,"**/reckless-hash-matrix.json"))
        for label,doc in (
            ("LC0 A/B",lc0_ab),("Reckless A/B",reckless_ab),
            ("Stockfish PGO A/B",stockfish_ab),
            ("Stockfish hash",sf_hash),("Reckless hash",rr_hash),
        ):
            require((doc.get("source") or {}).get("commit")==source,
                    f"{label} source is not exact head")
            require(not doc.get("errors"),f"{label} contains failed cases")
        require((lc0_ab.get("comparison") or {}).get("bestmove_agreement")==1.0,
                "derived LC0 disagrees with pristine control on frozen corpus")
        lc0_ratio=float(lc0_ab["comparison"]["wall_ratio_right_over_left"])
        require(0.85 <= lc0_ratio <= 1.15,
                "derived LC0 shows a material disabled-feature runtime regression")
        require((reckless_ab.get("comparison") or {}).get("bestmove_agreement")==1.0,
                "derived Reckless disagrees with pristine control")
        require(float(reckless_ab["comparison"]["left_median_wall_ms"])
                <= 1.10*float(reckless_ab["comparison"]["right_median_wall_ms"]),
                "derived Reckless materially regressed against pristine control")
        require((stockfish_ab.get("comparison") or {}).get("bestmove_agreement")==1.0,
                "PGO Stockfish changed frozen-corpus bestmoves")
        require(float(stockfish_ab["comparison"]["left_median_wall_ms"])
                <= 1.05*float(stockfish_ab["comparison"]["right_median_wall_ms"]),
                "PGO Stockfish lost its acceptable fixed-node runtime envelope")
        sf_summaries=sf_hash.get("summaries") or {}
        rr_summaries=rr_hash.get("summaries") or {}
        sf16=float(sf_summaries["16"]["median_wall_ms"])
        sf_best=min(float(row["median_wall_ms"]) for row in sf_summaries.values())
        require(sf16 <= 1.03*sf_best,
                "selected Stockfish Hash=16 fell outside the 3% efficiency band")
        rr16=float(rr_summaries["16"]["median_wall_ms"])
        rr_best=min(float(row["median_wall_ms"]) for row in rr_summaries.values())
        require(rr16 <= 1.03*rr_best,
                "selected Reckless Hash=16 fell outside the 3% efficiency band")
        details["constituent_ab"]={
            "lc0_bestmove_agreement":1.0,
            "lc0_pristine_over_derived_wall_ratio":lc0_ratio,
            "reckless_bestmove_agreement":1.0,
            "stockfish_pgo_bestmove_agreement":1.0,
            "stockfish_hash16_over_best":sf16/sf_best,
            "reckless_hash16_over_best":rr16/rr_best,
        }

        candidate=load(find_one(
            artifact_root,
            "engine-opt-v2-candidate/**/test-results/engine-opt-v2/report.json",
        ))
        require(candidate.get("source_commit")==source,
                "candidate qualification source is not exact head")
        require(candidate.get("passed") is True
                and candidate.get("promotion_ready") is True,
                "selected candidate identity is not promotion-ready")
        details["candidate"]={
            "promotion_ready":True,
            "bundle_manifest_sha256":candidate.get("bundle_manifest_sha256"),
            "evidence_sha256":candidate.get("evidence_sha256"),
        }

        hybrid=load(find_one(
            artifact_root,
            "engine-opt-v2-hybrid-evidence/**/test-results/online-hybrid-v2/report.json",
        ))
        require(hybrid.get("passed") is True,
                "real G3-v2 qualification did not pass")
        positive=hybrid.get("positive_case") or {}
        require(positive.get("authority")=="HYBRID"
                and positive.get("emitted_move")
                and positive.get("emitted_move")!=positive.get("anchor_move"),
                "G3-v2 lacks a genuine non-anchor authority witness")
        require(positive.get("resource_qualified") is True
                and positive.get("route_resource_qualified") is True,
                "G3-v2 positive case lacks qualified resource evidence")
        details["hybrid"]={
            "case":positive.get("case"),
            "anchor_move":positive.get("anchor_move"),
            "emitted_move":positive.get("emitted_move"),
        }

        local_reports=[]
        for path in sorted((artifact_root/"engine-opt-v2-local1").glob("**/report.json")):
            try:
                row=load(path)
            except Exception:
                continue
            if row.get("execution_scope")=="required_local1" and "campaign_id" in row:
                local_reports.append((path,row))
        require(len(local_reports)==1,
                f"expected one retained LOCAL-1-v2 report, found {len(local_reports)}")
        local_path,local=local_reports[0]
        require(local.get("passed") is True,
                f"LOCAL-1-v2 did not pass: {local.get('errors')}")
        require((local.get("claim_boundary") or {}).get("full_game_lifecycle") is True,
                "LOCAL-1-v2 did not qualify full-game lifecycle")
        require(local.get("validated_games")==28,
                "LOCAL-1-v2 did not validate all 28 required games")
        manifests=sorted(local_path.parent.glob("manifest.json"))
        require(len(manifests)==1,"LOCAL-1-v2 campaign manifest missing")
        manifest=load(manifests[0])
        require((manifest.get("source") or {}).get("commit")==source,
                "LOCAL-1-v2 source is not exact head")
        details["local1_v2"]={
            "campaign_id":local.get("campaign_id"),
            "validated_games":local.get("validated_games"),
            "authority_counts":local.get("authority_counts"),
            "actual_anchor_overrides":local.get("actual_anchor_overrides"),
        }

    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    report={
        "schema_version":1,
        "profile_id":"engine-opt-v2-aggregate",
        "source_commit":source,
        "passed":not errors,
        "errors":errors,
        "details":details,
        "claim_boundary":{
            "engine_profile_qualified":not errors,
            "full_game_lifecycle":bool(
                not errors and details.get("local1_v2")
            ),
            "strength":False,
            "elo":False,
            "equal_compute":False,
            "deployment":False,
        },
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(
        json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    print(json.dumps(report,sort_keys=True))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
