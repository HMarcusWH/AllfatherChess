"""Collect read-only binary, toolchain and host identity for a diagnostic run."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for piece in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()


def version(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.strip()[:12000],
            "stderr": result.stderr.strip()[:4000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "error": f"{type(exc).__name__}: {exc}"}


def elf_text(path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        section = Path(tmp) / "text.bin"
        try:
            result = subprocess.run(
                ["objcopy", "--dump-section", f".text={section}", str(path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if result.returncode != 0 or not section.is_file():
                return {"available": False, "reason": result.stderr.strip()[:500]}
            return {"available": True, "sha256": sha256(section),
                    "size_bytes": section.stat().st_size}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "reason": str(exc)[:500]}


def binaries(paths: dict[str, Path]) -> dict[str, Any]:
    result = {}
    for label, file in sorted(paths.items()):
        if not file.is_file():
            raise ValueError(f"missing {label} binary: {file}")
        result[label] = {
            "path": str(file.resolve()), "sha256": sha256(file),
            "size_bytes": file.stat().st_size, "elf_text": elf_text(file),
            "elf_notes": version(["readelf", "-n", str(file)]),
        }
    return result


def host() -> dict[str, Any]:
    cpu_model = None
    microcode = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if cpu_model is None and line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
            if microcode is None and line.startswith("microcode"):
                microcode = line.split(":", 1)[1].strip()
    except OSError:
        pass
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model_name": cpu_model,
        "microcode": microcode,
        "logical_cpus": os.cpu_count(),
        "allowed_cpus_before_pin": sorted(os.sched_getaffinity(0)),
        "runner_image_os": os.getenv("ImageOS"),
        "runner_image_version": os.getenv("ImageVersion"),
        "runner_environment": os.getenv("ALLFATHER_RUNNER_ENVIRONMENT"),
        "kernel": platform.release(),
        "rustc": version(["rustc", "--version", "--verbose"]),
        "cargo": version(["cargo", "--version"]),
        "linker": version(["ld", "--version"]),
        "resolved_build_rustflags": "-Ctarget-cpu=x86-64",
    }


def provenance(paths: dict[str, Path], model: Path,
               build_logs: dict[str, Path], host_report: Path) -> dict[str, Any]:
    if not model.is_file() or not host_report.is_file():
        raise ValueError("verified Reckless model or J2 host report missing")
    doc = json.loads(host_report.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("J2 host report root is not an object")
    return {
        "binaries": binaries(paths),
        "network": {"sha256": sha256(model), "size_bytes": model.stat().st_size},
        "host": host(),
        "j2_host_report_sha256": sha256(host_report),
        "build_logs": {
            key: {"sha256": sha256(value), "size_bytes": value.stat().st_size}
            for key, value in build_logs.items()
        },
        "exact_binary_identity_required": True,
        "historical_artifacts_used": False,
    }
