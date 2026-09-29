#!/usr/bin/env python3
"""Record the hardware/software environment for an LC0 qualification run."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.host_capabilities import discover_host_capabilities
from controller.runtime_substrate import RuntimeSubstrate

BUILD_PACKAGES = (
    "meson",
    "ninja-build",
    "pkg-config",
    "libprotobuf-dev",
    "protobuf-compiler",
    "zlib1g-dev",
    "libopenblas-dev",
)


def command(*args: str) -> str | None:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def cpu_model() -> str | None:
    path = Path("/proc/cpuinfo")
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    return platform.processor() or None


def memory_bytes() -> int | None:
    path = Path("/proc/meminfo")
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                fields = line.split()
                if len(fields) >= 2 and fields[1].isdigit():
                    return int(fields[1]) * 1024
    return None


def os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def main() -> int:
    release = os_release()
    capabilities = discover_host_capabilities()
    runner_environment = os.environ.get("ALLFATHER_RUNNER_ENVIRONMENT")
    openblas_package = command(
        "dpkg-query", "-W", "-f=${Package}=${Version}", "libopenblas-dev"
    )
    libc_name, libc_version = platform.libc_ver()
    substrate = RuntimeSubstrate.from_observation(
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
        clock_ticks_per_second=os.sysconf("SC_CLK_TCK"),
    )
    is_reference_runner = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and runner_environment == "github-hosted"
        and platform.system() == "Linux"
        and release.get("ID") == "ubuntu"
        and release.get("VERSION_ID") == "24.04"
    )
    report = {
        "schema_version": 1,
        "runner_class": (
            "github-hosted-ubuntu-24.04-cpu-reference"
            if is_reference_runner
            else "unclassified"
        ),
        "runner_environment": runner_environment,
        "runner_os": os.environ.get("RUNNER_OS"),
        "runner_arch": os.environ.get("RUNNER_ARCH"),
        "os": platform.platform(),
        "os_release": release,
        "system": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_bytes": memory_bytes(),
        "packages": {
            package: command(
                "dpkg-query", "-W", "-f=${Package}=${Version}", package
            )
            for package in BUILD_PACKAGES
        },
        "toolchain": {
            "gcc": command("gcc", "--version"),
            "g++": command("g++", "--version"),
            "meson": command("meson", "--version"),
            "ninja": command("ninja", "--version"),
            "pkg-config": command("pkg-config", "--version"),
            "protoc": command("protoc", "--version"),
        },
        "runner_image": {
            "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"),
        },
        "openblas_package": openblas_package,
        "python": platform.python_version(),
        "resource_measurement": {
            "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
        },
        "host_capabilities": capabilities.as_dict(),
        "host_capability_id": capabilities.capability_id,
        "host_qualification_domain_id": capabilities.qualification_domain_id,
        "host_qualification_domain_digest": capabilities.qualification_domain_digest,
        "runtime_substrate": substrate.as_dict(),
        "runtime_substrate_id": substrate.substrate_id,
        "runtime_substrate_digest": substrate.digest,
        "commit_sha": command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
