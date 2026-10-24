#!/usr/bin/env python3
"""Named-host observation, not admission, promotion or credential permission."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def _import(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _docker(*args):
    return json.loads(subprocess.check_output(["docker",*args],text=True,timeout=20))

def inspect_namespaces(bridge: str,gateway: str)->dict:
    if not bridge or not gateway or bridge==gateway:
        return {"bridge_egress_test_pass":False,"gateway_private_port_test_pass":False}
    b,g=(_docker("inspect",name)[0] for name in (bridge,gateway))
    if b["State"]["Running"] is not True or g["State"]["Running"] is not True:
        raise ValueError("canary containers not running")
    bn=b["NetworkSettings"]["Networks"]
    gn=g["NetworkSettings"]["Networks"]
    networks={n["Name"]:n for n in _docker("network","inspect",*sorted(set(bn)|set(gn)))}
    bridge_only=(len(bn)==1 and all(networks[k]["Internal"] is True for k in bn))
    gateway_dual=(len(gn)==2 and any(networks[k]["Internal"] is False for k in gn)
                  and set(bn).issubset(gn))
    distinct=(b["State"]["Pid"]!=g["State"]["Pid"] and
              b["HostConfig"]["NetworkMode"]!= "container:"+g["Id"])
    no_ports=(not g["HostConfig"]["PortBindings"] and not b["HostConfig"]["PortBindings"])
    secret_gateway_only=(
        any(str(x.get("Destination","")).startswith("/run/secrets/") for x in g["Mounts"])
        and not any(str(x.get("Destination","")).startswith("/run/secrets/") for x in b["Mounts"]))
    # Actual attempted TCP to an IP, not just Compose/config inspection. A
    # refused connection, timeout, or resolved DNS alone does not prove egress
    # blocked; internal Docker network topology is also required.
    test=subprocess.run(["docker","exec",bridge,"python3","-c",
        "import socket; s=socket.socket(); s.settimeout(2); s.connect(('1.1.1.1',443))"],
        capture_output=True,timeout=8)
    denied=(test.returncode!=0)
    return {
        "bridge_egress_test_pass":bool(bridge_only and distinct and denied),
        "gateway_private_port_test_pass":bool(gateway_dual and no_ports and distinct and secret_gateway_only),
        "bridge_only_internal":bridge_only,"gateway_dual_homed":gateway_dual,
        "separate_network_namespaces":distinct,"no_host_ports":no_ports,
        "gateway_only_secret_mount":secret_gateway_only,
        "bridge_external_tcp_denied":denied,
    }

def observe(host_id: str,persistent: Path,runtime: Path,bridge: str="",gateway: str="")->dict:
    if not host_id or not all(c.isalnum() or c in "._-" for c in host_id):
        raise ValueError("named host required")
    prior=_import("release_host",ROOT/"scripts/qualify-release-host.py")
    base=prior.examine(ROOT,persistent,runtime,min_cpus=4,min_memory_mib=6144)
    net=inspect_namespaces(bridge,gateway) if bridge and gateway else {
        "bridge_egress_test_pass":False,
        "gateway_private_port_test_pass":False}
    value={
        "schema_version":1,"scope":"host-observation-only",
        "host_id":host_id,"platform":platform.system().lower(),
        "machine":platform.machine().lower(),
        "capacity":base["capacity"],
        "operator_envelope_met":base["operator_envelope_met"],
        "durable_volume_separate":base["durable_volume_separate"],
        "filesystem_observations":base["filesystem_observations"],
        **net,
        "destination_host_qualified":False,"public_bot_release":False,
        "live_game_authorized":False,
    }
    raw=json.dumps(value,sort_keys=True,separators=(",",":")).encode()
    value["observation_digest"]=hashlib.sha256(raw).hexdigest()
    return value

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--host-id",required=True)
    p.add_argument("--persistent",type=Path,default=Path("/var/lib/allfather"))
    p.add_argument("--runtime",type=Path,default=Path("/run/allfather"))
    p.add_argument("--bridge-container",default="")
    p.add_argument("--gateway-container",default="")
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--require-minimums",action="store_true")
    a=p.parse_args()
    try:
        result=observe(a.host_id,a.persistent,a.runtime,a.bridge_container,a.gateway_container)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
        minimum=(result["operator_envelope_met"] and result["durable_volume_separate"]
                 and result["bridge_egress_test_pass"]
                 and result["gateway_private_port_test_pass"])
        print(json.dumps({"host_observed":True,"test_minimums":bool(minimum),
                          "destination_host_qualified":False}))
        return 0 if minimum or not a.require_minimums else 2
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.SubprocessError) as exc:
        print("live host observation failed: "+type(exc).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
