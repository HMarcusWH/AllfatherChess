#!/usr/bin/env python3
"""Record the host and runtime substrate for an LC0 qualification run."""

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
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def build_report(binary: Path | None) -> dict:
    release = os_release()
    runner_environment = os.environ.get("ALLFATHER_RUNNER_ENVIRONMENT")
    is_reference_runner = (
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
        "cpu_model": capabilities.cpu_model_name,
        "logical_cpus": os.cpu_count(),
        "memory_bytes": capabilities.physical_memory_bytes,
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
        "openblas_package": command(
            "dpkg-query",
            "-W",
            "-f=${Package}=${Version}",
            "libopenblas-dev",
        ),
        "python": platform.python_version(),
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
    binary = args.binary
    if binary is None:
        candidate = ROOT / "engines" / "lc0" / "build" / "release" / "lc0"
        binary = candidate if candidate.is_file() else None
    print(json.dumps(build_report(binary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
