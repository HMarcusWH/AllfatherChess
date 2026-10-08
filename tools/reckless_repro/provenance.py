"""Collect read-only binary, toolchain and host identity for a diagnostic run."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
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



class BinaryMutationError(RuntimeError):
    """An original executable changed after its immediately-post-build hash."""


def binary_identity(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BinaryMutationError(f"missing executable: {path}")
    info = path.stat()
    mode = stat.S_IMODE(info.st_mode)
    if not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
        raise BinaryMutationError(f"not executable: {path}")
    return {"sha256": sha256(path), "size_bytes": info.st_size, "mode": mode}


def assert_identity(path: Path, original: dict[str, Any], operation: str) -> None:
    after = binary_identity(path)
    if after != original:
        raise BinaryMutationError(
            f"binary mutated {operation}: {path}; before={original}, after={after}"
        )


def preflight_binary_hashes(paths: dict[str, Path], manifest: Path) -> dict[str, str]:
    """Verify exact executable bytes against immediately-post-build sha256sum."""
    if not manifest.is_file():
        raise BinaryMutationError(f"missing build hash manifest: {manifest}")
    resolved = {str(p.resolve()): label for label, p in paths.items()}
    recorded: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            raise BinaryMutationError("malformed build hash manifest")
        digest, name = match.groups()
        full = str(Path(name).resolve())
        if full not in resolved or full in recorded:
            raise BinaryMutationError(f"unexpected or duplicate build hash path: {name}")
        recorded[full] = digest
    if set(recorded) != set(resolved):
        raise BinaryMutationError("build hash manifest must cover all three executables")
    for full, label in resolved.items():
        if binary_identity(paths[label])["sha256"] != recorded[full]:
            raise BinaryMutationError(f"built executable mutated before run: {label}")
    return {label: recorded[full] for full, label in resolved.items()}


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
    original = binary_identity(path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "inspection-elf"
            section = Path(tmp) / "text.bin"
            shutil.copy2(path, copy)
            try:
                result = subprocess.run(
                    ["objcopy", "--dump-section", f".text={section}", str(copy)],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                if result.returncode != 0 or not section.is_file():
                    return {"available": False, "reason": result.stderr.strip()[:500]}
                return {"available": True, "sha256": sha256(section),
                        "size_bytes": section.stat().st_size}
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"available": False, "reason": str(exc)[:500]}
    finally:
        assert_identity(path, original, "during disposable-copy objcopy")


def first_pass_mutator_probe(path: Path) -> dict[str, Any]:
    """Attribute possible first-pass ELF rewriting on a throwaway fresh copy only."""
    original = binary_identity(path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "fresh-probe.elf"
            section = Path(tmp) / "text.bin"
            shutil.copy2(path, probe)
            def probe_identity() -> dict[str, Any]:
                info = probe.stat()
                return {"sha256": sha256(probe), "size_bytes": info.st_size,
                        "mode": stat.S_IMODE(info.st_mode)}
            before = probe_identity()
            operations = {}
            for name, command in (
                ("objcopy", ["objcopy", "--dump-section", f".text={section}", str(probe)]),
                ("readelf", ["readelf", "-n", str(probe)]),
            ):
                try:
                    result = subprocess.run(
                        command, capture_output=True, text=True, timeout=20, check=False,
                    )
                    status = {"returncode": result.returncode,
                              "stderr": result.stderr.strip()[:500]}
                except (OSError, subprocess.TimeoutExpired) as exc:
                    status = {"error": f"{type(exc).__name__}: {exc}"}
                after = probe_identity()
                operations[name] = {
                    **status, "before": before, "after": after,
                    "mutated_probe": before != after,
                }
                before = after
            return {"copy_only": True, "operations": operations,
                    "first_mutator": next(
                        (name for name in ("objcopy", "readelf")
                         if operations[name]["mutated_probe"]), None,
                    )}
    finally:
        assert_identity(path, original, "during first-pass disposable attribution probe")


def binaries(paths: dict[str, Path]) -> dict[str, Any]:
    result = {}
    for label, file in sorted(paths.items()):
        original = binary_identity(file)
        probe = first_pass_mutator_probe(file)
        assert_identity(file, original, "after first-pass probe")
        text = elf_text(file)
        assert_identity(file, original, "after elf_text")
        notes = version(["readelf", "-n", str(file)])
        assert_identity(file, original, "after readelf")
        result[label] = {
            "path": str(file.resolve()),
            **original,
            "elf_text": text,
            "elf_notes": notes,
            "first_pass_mutator_probe": probe,
            "inspection_preserved_original": True,
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
