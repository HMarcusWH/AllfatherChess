#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import (
    EngineOptProfileError, load_json, validate_reference,
)
from tools.engine_opt.domain import candidate_bundle_identity
POLICY=ROOT/"qualification/online-engine-opt-v2.json"
SELECTION=ROOT/"qualification/engine-opt-v2-selection.json"
CONFIG=ROOT/"config/allfather.online-engine-opt-v2.json"
DERIVED=ROOT/"qualification/engine-derived-lock.json"
EVIDENCE=ROOT/"qualification/engine-opt-v2-evidence.json"
RESULT=ROOT/"build/test-results/engine-opt-v2/report.json"

def sha(path: Path | str) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):
            h.update(block)
    return h.hexdigest()

def git(*args: str) -> str:
    return subprocess.check_output(
        ["git","-C",str(ROOT),*args],text=True
    ).strip()

def require(condition: bool, message: str) -> None:
    if not condition:
        raise EngineOptProfileError(message)

def main() -> int:
    try:
        policy=load_json(POLICY)
        selection=load_json(SELECTION)
        config=load_json(CONFIG)
        derived=load_json(DERIVED)
        evidence=load_json(EVIDENCE)
        validate_reference(policy,selection,config,require_selected=True)
        require((selection.get("source_report") or {}).get("path")=="qualification/engine-opt-v2-evidence.json",
                "selected profile does not bind the frozen evidence summary")
        require(selection.get("selected")==evidence.get("selected"),
                "selected profile differs from the frozen measurement decision")
        source_report=selection.get("source_report") or {}
        measurement_source=evidence.get("measurement_source") or {}
        selection_runs=source_report.get("workflow_runs")
        evidence_runs=measurement_source.get("workflow_runs")
        require(isinstance(selection_runs,list) and selection_runs,
                "selected profile must bind one or more measurement workflows")
        require(selection_runs==evidence_runs,
                "selection and evidence disagree on measurement workflow lineage")
        bundle=ROOT/policy["bundle_root"]
        manifest=load_json(bundle/"build-manifest.json")
        source=git("rev-parse","HEAD")
        source_tree=git("rev-parse","HEAD^{tree}")
        candidate_identity=candidate_bundle_identity(
            bundle,
            expected_source_commit=source,
        )
        require(manifest.get("profile_id")==policy["profile_id"],
                "v2 bundle profile mismatch")
        require(manifest.get("source_commit")==source,
                "v2 bundle source commit mismatch")
        require(manifest.get("source_tree")==source_tree,
                "v2 bundle source tree mismatch")

        expected_contracts={
            "vendor_lock_sha256":sha(ROOT/"vendor.lock.json"),
            "policy_sha256":sha(POLICY),
            "runtime_config_sha256":sha(CONFIG),
            "selection_sha256":sha(SELECTION),
            "evidence_sha256":sha(EVIDENCE),
            "derived_lock_sha256":sha(DERIVED),
            "lc0_strength_lock_sha256":sha(ROOT/"qualification/lc0-strength.lock.json"),
            "lc0_strength_profile_sha256":sha(ROOT/"qualification/lc0-strength-profile.json"),
        }
        require(manifest.get("contracts")==expected_contracts,
                "v2 bundle contract hashes differ from checkout")

        expected_trees={
            family:git("rev-parse",f"HEAD:engines/{family}")
            for family in ("stockfish","reckless","lc0")
        }
        require(manifest.get("derived_engine_trees")==expected_trees,
                "v2 bundle derived engine trees differ from checkout")
        for family,tree in expected_trees.items():
            require(
                tree==(derived.get("engines") or {}).get(family,{}).get("derived_tree"),
                f"{family}: current derived tree differs from engine-derived lock",
            )

        builds=manifest.get("builds")
        require(builds==policy.get("builds"),
                "v2 bundle build declarations differ from policy")
        artifacts=manifest.get("artifacts") or {}
        for category in ("engines","networks"):
            rows=artifacts.get(category) or {}
            require(set(rows)=={"stockfish","reckless","lc0"},
                    f"v2 bundle {category} identities incomplete")
            for family,row in rows.items():
                path=bundle/row["path"]
                require(path.is_file(),f"{family} {category} artifact missing")
                require(path.stat().st_size==row.get("size"),
                        f"{family} {category} artifact size mismatch")
                require(sha(path)==row.get("sha256"),
                        f"{family} {category} artifact hash mismatch")

        report={
            "schema_version":1,
            "profile_id":"engine-opt-v2",
            "status":selection.get("status"),
            "source_commit":source,
            "source_tree":source_tree,
            "bundle_manifest_sha256":sha(bundle/"build-manifest.json"),
            "selection_sha256":sha(SELECTION),
            "contracts":expected_contracts,
            "derived_engine_trees":expected_trees,
            "passed":True,
            "candidate_identity_valid":True,
            "promotion_ready":False,
            "promotion_requires":"execution-domain-bound aggregate qualification",
            "candidate_bundle":candidate_identity,
            "evidence_sha256":sha(EVIDENCE),
            "claim_boundary":{
                "strength":False,"elo":False,"deployment":False,
            },
        }
    except Exception as exc:
        report={
            "schema_version":1,"profile_id":"engine-opt-v2",
            "passed":False,"candidate_identity_valid":False,"promotion_ready":False,
            "error":f"{type(exc).__name__}: {exc}",
        }
    RESULT.parent.mkdir(parents=True,exist_ok=True)
    RESULT.write_text(
        json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8"
    )
    print(json.dumps(report,sort_keys=True))
    return 0 if report["passed"] else 1

if __name__=="__main__":
    raise SystemExit(main())
