#!/usr/bin/env python3
"""E00 source contracts, real-engine smoke/full campaign and independent replay."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
# Script entrypoints run with scripts/ on sys.path; add the exact source tree.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.strength_lab.e00_corpus import POLICY
from tools.strength_lab.e00_runner import run
from tools.strength_lab.e00_verify import verify_campaign

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",required=True,choices=("contracts","smoke","full","native-smoke","verify"))
    parser.add_argument("--run",type=Path)
    args=parser.parse_args()
    policy=json.loads(POLICY.read_text(encoding="utf-8"))
    if any(policy["claims"].values()):
        raise ValueError("E00 may not promote source policy flags")
    if args.phase=="contracts":
        from controller.strength.e00_nodes import E00NodeSettings
        assert E00NodeSettings().policy=="strength_e00_fixed_nodes_watchdog_v1"
        print(json.dumps({"status":"SOURCE_CONTRACTS_PRESENT",
                          "history_recipe_count":policy["sample_count"],
                          "e00_qualified":False,"real_binary_executed":False},sort_keys=True))
        return 0
    if args.phase=="verify":
        if args.run is None:
            parser.error("--run is required for --phase verify")
        folder=args.run
    else:
        if args.run is not None:
            parser.error("--run only applies to verification")
        folder=run(phase=args.phase,strict_cgroup=(args.phase=="full"))
    result=verify_campaign(folder)
    target=folder/"independent-verdict.json"
    target.write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n",
                      encoding="utf-8")
    print(json.dumps({**result,"run":str(folder)},sort_keys=True,allow_nan=False))
    if args.phase=="full":
        return 0 if result["status"]=="PASS" else 2
    return 0 if result["status"] in ("PASS","INCONCLUSIVE") else 2

if __name__=="__main__":
    raise SystemExit(main())
