"""Read-only, bounded CI host/storage context for exploratory G3 game attempts.

Runs strictly before/after the match, never on the UCI bestmove or replay seal
path. Host observations are diagnostic; they cannot qualify a chess match.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import time

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
PHASES = frozenset(("before", "after"))


def _read(path: Path, *, limit: int = 2048) -> str | None:
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return None
        return raw.decode("ascii", errors="replace").strip()
    except (OSError, ValueError):
        return None


def _io_pressure(path: Path) -> dict | None:
    raw = _read(path)
    if raw is None:
        return None
    observations = {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        fields = {}
        for token in parts[1:]:
            if "=" not in token:
                continue
            k, v = token.split("=", 1)
            if k in ("avg10", "avg60", "avg300", "total"):
                try:
                    fields[k] = float(v) if k != "total" else int(v)
                except ValueError:
                    pass
        observations[parts[0]] = fields
    return observations or None


def observe(*, phase: str, source_commit: str, root: Path,
            pressure_path: Path = Path("/proc/pressure/io"),
            cgroup_path: Path = Path("/sys/fs/cgroup")) -> dict:
    if phase not in PHASES:
        raise ValueError("host observation phase must be before or after")
    if not isinstance(source_commit, str) or HEX40.fullmatch(source_commit) is None:
        raise ValueError("source commit must be exact 40-character lowercase SHA")
    root = Path(root)
    fs = os.statvfs(root)
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    return {
        "schema_version": 1,
        "kind": "g3-pilot-host-io-context-v1",
        "diagnostic_only": True,
        "qualifies": False,
        "authorizes_deployment": False,
        "phase": phase,
        "source_commit": source_commit,
        "observed_utc_epoch_s": round(time.time(), 3),
        "runtime": {
            "platform": platform.system(),
            "architecture": platform.machine(),
            "kernel_release": platform.release(),
            "affinity_cpus": affinity,
            "runner_os": os.getenv("RUNNER_OS"),
            "runner_image_os": os.getenv("ImageOS"),
            "runner_image_version": os.getenv("ImageVersion"),
            "github_run_id": os.getenv("GITHUB_RUN_ID"),
            "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        },
        "filesystem": {
            "fragment_size": fs.f_frsize,
            "blocks": fs.f_blocks,
            "available_blocks": fs.f_bavail,
            "available_inodes": fs.f_favail,
        },
        "cgroup_v2_controllers_present": (cgroup_path / "cgroup.controllers").is_file(),
        "cgroup_memory_max": _read(cgroup_path / "memory.max", limit=64),
        "io_pressure": _io_pressure(pressure_path),
        "qualification_scope": "host_observation_only_uncontrolled_ci_not_hardware_matched",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=sorted(PHASES), required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    payload = observe(phase=args.phase, source_commit=args.source_commit, root=args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
