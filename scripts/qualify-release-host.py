#!/usr/bin/env python3
"""Observe release-class host capacity without certifying an unnamed destination."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import platform
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def observation():
    spec=importlib.util.spec_from_file_location(
        "online_preflight",ROOT/"scripts/online-host-preflight.py")
    if spec is None or spec.loader is None:
        raise ValueError("canonical host provider unavailable")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    facts=mod.record()
    policy=json.loads((ROOT/"qualification/release-image-v1.json").read_text())
    sufficient=mod.operator_minimums(
        facts,policy["exact_host_operator_capacity_cpus"],
        policy["exact_host_operator_memory_mib"])
    return {
        "schema_version":1,"profile_id":"release-1-host-observation-v1",
        "runner_system":platform.system().lower(),
        "runner_machine":platform.machine(),
        "observed_capacity":facts,
        "operator_minimums_observed":sufficient,
        "scope":"CI host observation only, NOT destination qualification",
        "actual_destination_host_id":None,
        "destination_host_qualified":False,
        "public_bot_release":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        report=observation()
    except (OSError,ValueError,RuntimeError) as exc:
        p.exit(2,"RELEASE-1 host observation rejected: "+str(exc)+"\n")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"host_observed":True,"operator_minimums_observed":report["operator_minimums_observed"],
                      "destination_host_qualified":False},sort_keys=True))

if __name__=="__main__":
    main()
