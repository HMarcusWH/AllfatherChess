#!/usr/bin/env python3
"""Validate the isolated b4 overlay against the exact-head canonical ENGINE-OPT bundle."""
from __future__ import annotations
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import EngineOptProfileError,load_json,validate_reference
from tools.engine_opt.domain import candidate_bundle_identity
POLICY=ROOT/"qualification/online-engine-opt-v2.json"
CANONICAL_SELECTION=ROOT/"qualification/engine-opt-v2-selection.json"
CANONICAL_EVIDENCE=ROOT/"qualification/engine-opt-v2-evidence.json"
CANONICAL_CONFIG=ROOT/"config/allfather.online-engine-opt-v2.json"
SELECTION=ROOT/"qualification/engine-opt-v2-candidate-selection.json"
EVIDENCE=ROOT/"qualification/engine-opt-v2-candidate-evidence.json"
REFERENCE=ROOT/"config/allfather.online-engine-opt-v2.candidate.json"
HYBRID=ROOT/"config/allfather.online-hybrid-v2.candidate.validation.json"
G3_POLICY=ROOT/"qualification/online-hybrid-v2.json"
DERIVED=ROOT/"qualification/engine-derived-lock.json"
RESULT=ROOT/"build/test-results/engine-opt-v2-candidate/report.json"
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""): h.update(block)
    return h.hexdigest()
def git(*args): return subprocess.check_output(["git","-C",str(ROOT),*args],text=True).strip()
def require(ok,msg):
    if not ok: raise EngineOptProfileError(msg)
def main():
    try:
        policy=load_json(POLICY); selection=load_json(SELECTION); evidence=load_json(EVIDENCE)
        reference=load_json(REFERENCE); derived=load_json(DERIVED)
        canonical_selection=load_json(CANONICAL_SELECTION); canonical_evidence=load_json(CANONICAL_EVIDENCE)
        validate_reference(policy,selection,reference,require_selected=False)
        require(selection.get("status")=="qualification_candidate","candidate selection must remain non-authoritative")
        require((selection.get("authority") or {}).get("promotion_authority") is False,"candidate may not grant promotion authority")
        require(selection.get("selected")==evidence.get("selected"),"candidate selection/evidence mismatch")
        bundle=ROOT/policy["bundle_root"]; manifest=load_json(bundle/"build-manifest.json")
        source=git("rev-parse","HEAD"); tree=git("rev-parse","HEAD^{tree}")
        identity=candidate_bundle_identity(bundle,expected_source_commit=source)
        canonical_contracts={
          "vendor_lock_sha256":sha(ROOT/"vendor.lock.json"),
          "policy_sha256":sha(POLICY),
          "runtime_config_sha256":sha(CANONICAL_CONFIG),
          "selection_sha256":sha(CANONICAL_SELECTION),
          "evidence_sha256":sha(CANONICAL_EVIDENCE),
          "derived_lock_sha256":sha(DERIVED),
          "lc0_strength_lock_sha256":sha(ROOT/"qualification/lc0-strength.lock.json"),
          "lc0_strength_profile_sha256":sha(ROOT/"qualification/lc0-strength-profile.json"),
        }
        require(manifest.get("source_commit")==source and manifest.get("source_tree")==tree,"bundle is not exact head")
        require(manifest.get("contracts")==canonical_contracts,"bundle no longer binds canonical build contracts")
        expected_trees={family:git("rev-parse",f"HEAD:engines/{family}") for family in ("stockfish","reckless","lc0")}
        require(manifest.get("derived_engine_trees")==expected_trees,"derived engine tree drift")
        for family,oid in expected_trees.items():
            require(oid==(derived.get("engines") or {}).get(family,{}).get("derived_tree"),f"{family} derived tree differs from lock")
        overlay={
          "selection_sha256":sha(SELECTION),
          "evidence_sha256":sha(EVIDENCE),
          "reference_runtime_sha256":sha(REFERENCE),
          "hybrid_runtime_sha256":sha(HYBRID),
          "g3_policy_sha256":sha(G3_POLICY),
        }
        report={"schema_version":1,"profile_id":"engine-opt-v2-candidate","status":"qualification_candidate",
          "source_commit":source,"source_tree":tree,"bundle_manifest_sha256":sha(bundle/"build-manifest.json"),
          "canonical_bundle_contracts":canonical_contracts,"candidate_overlay":overlay,
          "candidate_bundle":identity,"candidate_identity_valid":True,"passed":True,
          "promotion_ready":False,"canonical_profile_changed":False,
          "claim_boundary":{"candidate_overlay":True,"canonical_profile_changed":False,"promotion_authority":False,
            "strength":False,"elo":False,"equal_compute":False,"deployment":False}}
    except Exception as exc:
        report={"schema_version":1,"profile_id":"engine-opt-v2-candidate","candidate_identity_valid":False,
          "passed":False,"promotion_ready":False,"canonical_profile_changed":False,"error":f"{type(exc).__name__}: {exc}"}
    RESULT.parent.mkdir(parents=True,exist_ok=True)
    RESULT.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True))
    return 0 if report.get("passed") is True else 1
if __name__=="__main__": raise SystemExit(main())
