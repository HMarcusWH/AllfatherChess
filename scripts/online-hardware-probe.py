#!/usr/bin/env python3
"""Record host/toolchain identity for ONLINE-2 qualification."""
from __future__ import annotations
import json,os,platform,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from controller.host_capabilities import discover_host_capabilities
from controller.runtime_substrate import RuntimeSubstrate
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
    capabilities=discover_host_capabilities()
    openblas_package=command("dpkg-query","-W","-f=${Package}=${Version}","libopenblas-dev")
    libc_name,libc_version=platform.libc_ver()
    substrate=RuntimeSubstrate.from_observation(
      os_id=release.get("ID") or "unknown",
      os_version_id=release.get("VERSION_ID") or "unknown",
      kernel_release=platform.release(),
      architecture=platform.machine(),
      libc_name=libc_name or "unknown",
      libc_version=libc_version or "unknown",
      python_version=platform.python_version(),
      runner_image_os=os.environ.get("ImageOS"),
      runner_image_version=os.environ.get("ImageVersion"),
      openblas_package=openblas_package,
      clock_ticks_per_second=os.sysconf("SC_CLK_TCK"))
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
      "openblas_package":openblas_package,
      "runner_image":{"image_os":os.environ.get("ImageOS"),"image_version":os.environ.get("ImageVersion")},
      "resource_measurement":{"clock_ticks_per_second":os.sysconf("SC_CLK_TCK")},
      "host_capabilities":capabilities.as_dict(),
      "host_capability_id":capabilities.capability_id,
      "host_qualification_domain_id":capabilities.qualification_domain_id,
      "host_qualification_domain_digest":capabilities.qualification_domain_digest,
      "runtime_substrate":substrate.as_dict(),
      "runtime_substrate_id":substrate.substrate_id,
      "runtime_substrate_digest":substrate.digest,
      "commit_sha":command("git","-C",str(ROOT),"rev-parse","HEAD"),
    }
def main():
    print(json.dumps(probe(),indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
