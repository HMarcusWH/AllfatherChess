"""Execution-host and cgroup resource evidence for E00.

A physical CPU sibling topology is required for full qualification. Hosted
smoke can collect observations but may not claim host/resource qualification.
"""
from __future__ import annotations
import hashlib
import os
import platform
from pathlib import Path
import re
import time

def cpulist(raw:str)->set[int]:
    found=set()
    if not isinstance(raw,str) or not raw.strip():
        raise ValueError("empty CPU topology range")
    for part in raw.strip().split(","):
        if re.fullmatch(r"[0-9]+-[0-9]+",part):
            a,b=map(int,part.split("-"))
            if a>b or b-a>4096:
                raise ValueError("invalid E00 CPU sibling range")
            found.update(range(a,b+1))
        elif re.fullmatch(r"[0-9]+",part):
            found.add(int(part))
        else:
            raise ValueError("malformed CPU sibling topology")
    return found

def _read(path:Path,limit=1024)->str|None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        # sysfs/procfs virtual files commonly report st_size=4096 (or 0)
        # independently of their actual bytes. Bound the actual read.
        with path.open("rb") as stream:
            raw=stream.read(limit+1)
        if len(raw)>limit:
            return None
        return raw.decode("ascii").strip()
    except (OSError,UnicodeError):
        return None

def host_profile(*,strict:bool)->dict:
    allowed=tuple(sorted(os.sched_getaffinity(0)))
    if not allowed:
        raise ValueError("no observed E00 CPU affinity")
    groups={}
    for cpu in allowed:
        text=_read(Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"))
        try:
            group=tuple(sorted(cpulist(text))) if text else None
        except ValueError:
            group=None
        groups[cpu]=group
    search_cpu=harness_cpu=None
    for candidate in allowed:
        siblings=groups[candidate]
        if siblings is None:continue
        for other in allowed:
            if other!=candidate and other not in siblings and groups[other] is not None:
                search_cpu,harness_cpu=candidate,other
                break
        if search_cpu is not None:break
    if strict and (search_cpu is None or harness_cpu is None):
        raise ValueError("E00 full host needs distinct physical search/harness CPU cores")
    machine=platform.machine()
    if strict and (platform.system()!="Linux" or machine not in ("x86_64","AMD64")):
        raise ValueError("E00 full source domain requires Linux x86_64")
    try:
        cpuinfo=Path("/proc/cpuinfo").read_bytes()
        cpuinfo_digest=hashlib.sha256(cpuinfo).hexdigest()
    except OSError:
        cpuinfo_digest=None
    return {"schema_version":1,"kind":"e00_host_profile_v1",
            "observed_ns":time.monotonic_ns(),"system":platform.system(),
            "architecture":machine,"kernel":platform.release(),
            "allowed_cpus":list(allowed),
            "sibling_sets":{str(k):(None if v is None else list(v))
                            for k,v in sorted(groups.items())},
            "cpuinfo_sha256":cpuinfo_digest,
            "search_cpu":search_cpu,"harness_cpu":harness_cpu,
            "strict_physical_isolation":bool(strict),
            "cgroup_cpu_max":_read(Path("/sys/fs/cgroup/cpu.max")),
            "cgroup_cpuset_effective":_read(Path("/sys/fs/cgroup/cpuset.cpus.effective")),
            "cgroup_memory_max":_read(Path("/sys/fs/cgroup/memory.max")),
            "cpu_pressure_before":_read(Path("/proc/pressure/cpu"),4096),
            "memory_pressure_before":_read(Path("/proc/pressure/memory"),4096),
            "strength_qualified":False,
            "equal_compute":False}

def verify_profile(profile:dict,*,require_strict:bool)->None:
    if type(profile) is not dict or profile.get("schema_version")!=1 or \
       profile.get("kind")!="e00_host_profile_v1" or \
       profile.get("strength_qualified") is not False or \
       profile.get("equal_compute") is not False:
        raise ValueError("E00 host profile/claim boundary invalid")
    allowed=profile.get("allowed_cpus")
    if type(allowed) is not list or not allowed or \
       any(type(x) is not int or x<0 for x in allowed) or allowed!=sorted(set(allowed)):
        raise ValueError("E00 host CPU list invalid")
    if require_strict:
        if profile.get("strict_physical_isolation") is not True or \
           profile.get("system")!="Linux" or \
           profile.get("architecture") not in ("x86_64","AMD64"):
            raise ValueError("E00 full host physical isolation not established")
        a=profile.get("search_cpu");b=profile.get("harness_cpu")
        siblings=profile.get("sibling_sets")
        if type(a) is not int or type(b) is not int or a==b or \
           a not in allowed or b not in allowed or type(siblings) is not dict or \
           type(siblings.get(str(a))) is not list or b in siblings[str(a)]:
            raise ValueError("E00 participant/harness physical CPU overlap")

def verify_cpu_counters(before:int|None,after:int|None,observed_ms:float|None)->bool:
    if before is None or after is None:
        return observed_ms is None and before is None and after is None
    return (type(before) is int and type(after) is int and
            0<=before<=after and type(observed_ms) in (int,float) and
            abs((after-before)/1000.0-observed_ms)<1e-6)
