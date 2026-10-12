#!/usr/bin/env python3
"""Observation-only RELEASE-1 host and storage report.

One CI runner is not the selected destination host. Admission evidence alone
never sets the deployment_host_qualified or public release flags.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def preflight_module(root: Path):
    path = root / "scripts/online-host-preflight.py"
    spec = importlib.util.spec_from_file_location("release1_online_preflight", path)
    if spec is None or spec.loader is None:
        raise ValueError("canonical online preflight missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def examine(root: Path, persistent: Path, runtime: Path,
            *, min_cpus=4.0, min_memory_mib=6144):
    module = preflight_module(root)
    observation = module.record()
    enough = module.operator_minimums(observation, min_cpus, min_memory_mib)
    paths = {}
    for name, path in (("persistent",persistent),("ephemeral",runtime)):
        if path.is_dir() and not path.is_symlink():
            st = os.statvfs(path)
            paths[name] = {
                "exists": True, "free_bytes": st.f_bavail * st.f_frsize,
                "writable": os.access(path, os.W_OK|os.X_OK),
                "device": os.stat(path).st_dev,
                "is_symlink": False,
            }
        else:
            paths[name] = {"exists":False,"writable":False,"is_symlink":path.is_symlink()}
    separate = (len(paths) == 2 and all(v.get("exists") for v in paths.values())
                and paths["persistent"].get("device") != os.stat("/").st_dev
                and paths["persistent"].get("device") != paths["ephemeral"].get("device"))
    return {
        "schema_version":1,"source":"canonical-linux-host-preflight",
        "observed_hostname_omitted":True,"platform":platform.system().lower(),
        "capacity":observation,"operator_envelope_met":bool(enough),
        "filesystem_observations":paths,"durable_volume_separate":bool(separate),
        "test_host_observed":True, "destination_host_identified":False,
        "destination_host_qualified":False,"network_recovery_qualified":False,
        "public_bot_release":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=ROOT)
    p.add_argument("--persistent",type=Path,default=Path("/var/lib/allfather"))
    p.add_argument("--runtime",type=Path,default=Path("/run/allfather"))
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        report=examine(a.root,a.persistent,a.runtime)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
        print(json.dumps({"test_host_observed":True,
                          "capacity_met":report["operator_envelope_met"],
                          "destination_host_qualified":False}))
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        p.exit(2,"RELEASE-1 host observation failed: "+str(exc)+"\n")


if __name__=="__main__":
    main()
