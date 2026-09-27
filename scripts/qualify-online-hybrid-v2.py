#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import load_json, validate_hybrid
RESULT=ROOT/"build/test-results/online-hybrid-v2/report.json"
def main():
 try:
  p=load_json(ROOT/"qualification/online-hybrid-v2.json"); ref=load_json(ROOT/p["reference_runtime"]); hybrid=load_json(ROOT/p["runtime_config"]); validate_hybrid(p,ref,hybrid)
  report={"schema_version":1,"profile_id":"online-hybrid-v2","passed":True,"claim_boundary":p["claim_boundary"]}
 except Exception as exc:
  report={"schema_version":1,"profile_id":"online-hybrid-v2","passed":False,"error":f"{type(exc).__name__}: {exc}"}
 RESULT.parent.mkdir(parents=True,exist_ok=True); RESULT.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n"); print(json.dumps(report,sort_keys=True)); return 0 if report["passed"] else 1
if __name__=="__main__": raise SystemExit(main())
