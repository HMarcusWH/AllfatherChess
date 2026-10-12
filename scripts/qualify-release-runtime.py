#!/usr/bin/env python3
"""Independently check the recorded Docker runtime confinement of a real case."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

def require(ok,message):
    if not ok:raise ValueError(message)

def check(inspection:dict,*,source_commit:str,case:str)->dict:
    config=inspection.get("Config") or {}
    host=inspection.get("HostConfig") or {}
    require(config.get("User")=="10001:10001","runtime not nonroot")
    require(host.get("NetworkMode")=="none","offline run allowed network")
    require(host.get("ReadonlyRootfs") is True,"root filesystem not read only")
    caps=[x.lower() for x in host.get("CapDrop") or []]
    require("all" in caps,"capabilities not dropped")
    opts=host.get("SecurityOpt") or []
    require(any(x=="no-new-privileges" or x=="no-new-privileges:true" for x in opts),
            "privilege escalation not blocked")
    require(host.get("PidsLimit") == 128,"unbounded child process count")
    require(host.get("Memory")==6*1024**3,"runtime memory quota drift")
    require(host.get("NanoCpus")==4*10**9,"runtime CPU quota drift")
    require(not host.get("Privileged"),"privileged container refused")
    require(not host.get("PortBindings"),"published network port refused")
    require(config.get("Entrypoint")==["/opt/online3b/bin/python",
                                     "/opt/release/entrypoint.py"],
            "runtime bypassed guarded entrypoint")
    mounts=inspection.get("Mounts") or []
    paths={x.get("Destination"):x for x in mounts}
    need={"/var/lib/allfather","/opt/allfather/build/replays-online-hybrid-v2","/work"}
    require(need.issubset(paths),"durable/replay/read-only source mounts missing")
    require(paths["/work"].get("RW") is False,"CI repository mount writable")
    require(paths["/var/lib/allfather"].get("RW") is True,"SQLite journal mount not writable")
    require(paths["/opt/allfather/build/replays-online-hybrid-v2"].get("RW") is True,
            "replay mount not writable")
    require(source_commit and len(source_commit)==40,"exact source SHA missing")
    return {"schema_version":1,"source_commit":source_commit,"scenario":case,
            "root_read_only":True,"network_none":True,"nonroot_user":True,
            "bounded_resources":True,"durable_journal_mount":True,
            "separate_replay_mount":True,"public_bot_release":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--docker-inspect",required=True,type=Path)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--case",required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        obj=json.loads(a.docker_inspect.read_text())
        require(isinstance(obj,list) and len(obj)==1,"one actual container required")
        record=check(obj[0],source_commit=a.source_sha,case=a.case)
    except (OSError,ValueError,TypeError,KeyError) as e:
        p.exit(2,"RELEASE-1 runtime confinement rejected: "+str(e)+"\n")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(record,indent=2,sort_keys=True)+"\n")

if __name__=="__main__":
    main()
