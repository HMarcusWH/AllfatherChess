#!/usr/bin/env python3
"""Record host/toolchain identity for ONLINE-2 qualification."""
from __future__ import annotations
import json,os,platform,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
def command(*args):
    try: return subprocess.check_output(args,text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError): return None
def cpu_model():
    p=Path("/proc/cpuinfo")
    if p.is_file():
        for line in p.read_text(encoding="utf-8",errors="replace").splitlines():
            if line.lower().startswith("model name") and ":" in line: return line.split(":",1)[1].strip()
    return platform.processor() or None
def memory_bytes():
    p=Path("/proc/meminfo")
    if p.is_file():
        for line in p.read_text(encoding="utf-8",errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                parts=line.split()
                if len(parts)>=2 and parts[1].isdigit(): return int(parts[1])*1024
    return None
def os_release():
    p=Path("/etc/os-release"); out={}
    if not p.is_file(): return out
    for line in p.read_text(encoding="utf-8",errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k,v=line.split("=",1); out[k]=v.strip().strip('"')
    return out
def probe():
    release=os_release(); runner_environment=os.environ.get("ALLFATHER_RUNNER_ENVIRONMENT")
    ref=(os.environ.get("GITHUB_ACTIONS")=="true" and runner_environment=="github-hosted"
         and platform.system()=="Linux" and release.get("ID")=="ubuntu" and release.get("VERSION_ID")=="24.04")
    return {
      "schema_version":1,
      "runner_class":"github-hosted-ubuntu-24.04-cpu-reference" if ref else "deployment-or-local-host",
      "runner_environment":runner_environment,"runner_os":os.environ.get("RUNNER_OS"),"runner_arch":os.environ.get("RUNNER_ARCH"),
      "system":platform.system(),"architecture":platform.machine(),"kernel_release":platform.release(),
      "os_release":release,"cpu_model":cpu_model(),"logical_cpus":os.cpu_count(),"memory_bytes":memory_bytes(),
      "python":platform.python_version(),
      "toolchain":{name:command(*cmd) for name,cmd in {
        "gcc":("gcc","--version"),"g++":("g++","--version"),"rustc":("rustc","--version"),
        "cargo":("cargo","--version"),"meson":("meson","--version"),"ninja":("ninja","--version"),
        "pkg-config":("pkg-config","--version"),"protoc":("protoc","--version")}.items()},
      "packages":{name:command("dpkg-query","-W","-f=${Package}=${Version}",name) for name in (
        "meson","ninja-build","pkg-config","libprotobuf-dev","protobuf-compiler","zlib1g-dev","libopenblas-dev")},
      "runner_image":{"image_os":os.environ.get("ImageOS"),"image_version":os.environ.get("ImageVersion")},
      "commit_sha":command("git","-C",str(ROOT),"rev-parse","HEAD"),
    }
def main():
    print(json.dumps(probe(),indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
