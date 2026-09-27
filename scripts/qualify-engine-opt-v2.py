#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import load_json, validate_reference, EngineOptProfileError
POLICY=ROOT/"qualification/online-engine-opt-v2.json"; SELECTION=ROOT/"qualification/engine-opt-v2-selection.json"; CONFIG=ROOT/"config/allfather.online-engine-opt-v2.json"; RESULT=ROOT/"build/test-results/engine-opt-v2/report.json"
def sha(path):
 h=hashlib.sha256(); h.update(Path(path).read_bytes()); return h.hexdigest()
def main():
 try:
  policy=load_json(POLICY); selection=load_json(SELECTION); config=load_json(CONFIG); validate_reference(policy,selection,config,require_selected=False)
  bundle=ROOT/policy["bundle_root"]; manifest=load_json(bundle/"build-manifest.json"); source=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
  if manifest.get("source_commit")!=source: raise EngineOptProfileError("v2 bundle source mismatch")
  for category in ("engines","networks"):
   for family,row in manifest["artifacts"][category].items():
    path=bundle/row["path"]
    if not path.is_file() or sha(path)!=row["sha256"]: raise EngineOptProfileError(f"{family} {category} artifact mismatch")
  report={"schema_version":1,"profile_id":"engine-opt-v2","status":selection.get("status"),"source_commit":source,"bundle_manifest_sha256":sha(bundle/"build-manifest.json"),"selection_sha256":sha(SELECTION),"passed":True,"promotion_ready":selection.get("status")=="selected","claim_boundary":{"strength":False,"elo":False,"deployment":False}}
 except Exception as exc:
  report={"schema_version":1,"profile_id":"engine-opt-v2","passed":False,"promotion_ready":False,"error":f"{type(exc).__name__}: {exc}"}
 RESULT.parent.mkdir(parents=True,exist_ok=True); RESULT.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n"); print(json.dumps(report,sort_keys=True)); return 0 if report["passed"] else 1
if __name__=="__main__": raise SystemExit(main())
