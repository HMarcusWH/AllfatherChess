#!/usr/bin/env python3
"""Record host/toolchain/runtime identity for ONLINE qualification."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.host_capabilities import discover_host_capabilities
from controller.runtime_substrate import capture_runtime_substrate


def command(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value.strip().strip('"')
    return out


def probe(binary: Path | None = None) -> dict:
    release = os_release()
    runner_environment = os.environ.get("ALLFATHER_RUNNER_ENVIRONMENT")
    reference = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and runner_environment == "github-hosted"
        and platform.system() == "Linux"
        and release.get("ID") == "ubuntu"
        and release.get("VERSION_ID") == "24.04"
    )
    capabilities = discover_host_capabilities()
    runtime = None
    if binary is not None and binary.is_file():
        runtime = capture_runtime_substrate(binary)

    return {
        "schema_version": 2,
        "runner_class": (
            "github-hosted-ubuntu-24.04-cpu-reference"
            if reference
            else "deployment-or-local-host"
        ),
        "runner_environment": runner_environment,
        "runner_os": os.environ.get("RUNNER_OS"),
        "runner_arch": os.environ.get("RUNNER_ARCH"),
        "system": platform.system(),
        "architecture": platform.machine(),
        "kernel_release": platform.release(),
        "os_release": release,
        "cpu_model": capabilities.cpu_model_name,
        "logical_cpus": os.cpu_count(),
        "memory_bytes": capabilities.physical_memory_bytes,
        "python": platform.python_version(),
        "toolchain": {
            name: command(*cmd)
            for name, cmd in {
                "gcc": ("gcc", "--version"),
                "g++": ("g++", "--version"),
                "rustc": ("rustc", "--version"),
                "cargo": ("cargo", "--version"),
                "meson": ("meson", "--version"),
                "ninja": ("ninja", "--version"),
                "pkg-config": ("pkg-config", "--version"),
                "protoc": ("protoc", "--version"),
            }.items()
        },
        "packages": {
            name: command(
                "dpkg-query", "-W", "-f=${Package}=${Version}", name
            )
            for name in (
                "meson",
                "ninja-build",
                "pkg-config",
                "libprotobuf-dev",
                "protobuf-compiler",
                "zlib1g-dev",
                "libopenblas-dev",
            )
        },
        "runner_image": {
            "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"),
        },
        "commit_sha": command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "host_capabilities": capabilities.as_dict(),
        "host_capability_id": capabilities.capability_id,
        "host_qualification_domain_id": capabilities.qualification_domain_id,
        "runtime_substrate": (
            None if runtime is None else runtime.as_dict()
        ),
        "runtime_substrate_id": (
            None if runtime is None else runtime.runtime_substrate_id
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.binary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
