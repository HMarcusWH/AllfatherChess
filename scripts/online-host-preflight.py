#!/usr/bin/env python3
"""Record actual Linux process/cgroup host limits; no synthetic authority."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def record(*, proc=Path("/proc"), cgroups=Path("/sys/fs/cgroup")) -> dict:
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    hierarchy = read(proc / "self/cgroup") or ""
    matches = [s.split("::", 1)[1] for s in hierarchy.splitlines() if s.startswith("0::")]
    cg = cgroups / (matches[0].lstrip("/") if len(matches) == 1 else "")
    # Permit only cgroup paths below the observed controller root.
    if not cg.resolve().is_relative_to(cgroups.resolve()):
        cg = cgroups / "_invalid"
    cpu = read(cg / "cpu.max")
    mem = read(cg / "memory.max")
    quota_cpus = None
    if cpu:
        parts = cpu.split()
        if len(parts) == 2 and parts[0].isdecimal() and parts[1].isdecimal() and int(parts[1]) > 0:
            quota_cpus = int(parts[0]) / int(parts[1])
    mem_bytes = int(mem) if mem and mem.isdecimal() else None
    visible = len(affinity) if affinity is not None else None
    usable = min(visible, quota_cpus) if visible is not None and quota_cpus is not None else None
    return {
        "schema_version": 1, "scope": "observation_only",
        "system": platform.system(), "machine": platform.machine(),
        "affinity_cpu_count": visible, "cpu_max": cpu,
        "cpu_quota_cpus": quota_cpus, "memory_max": mem,
        "memory_limit_bytes": mem_bytes, "bounded_usable_cpus": usable,
        "cgroup_path": str(cg),
        "capacity_complete": usable is not None and mem_bytes is not None,
        "j12_authority_qualified": False, "generic_host_portability": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-capacity", action="store_true")
    parser.add_argument("--min-usable-cpus", type=float, default=4.0)
    parser.add_argument("--min-memory-mib", type=int, default=4096)
    args = parser.parse_args(argv)
    try:
        result = record()
        result["operator_minimums_met"] = (
            result["capacity_complete"] and result["bounded_usable_cpus"] >= args.min_usable_cpus
            and result["memory_limit_bytes"] >= args.min_memory_mib * 1024**2
            and result["system"] == "Linux" and result["machine"] in ("x86_64", "AMD64")
        )
        body = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(body, encoding="utf-8")
        else:
            sys.stdout.write(body)
        if args.require_capacity and not result["operator_minimums_met"]:
            print("ONLINE-3A host preflight: incomplete or undersized capacity", file=sys.stderr)
            return 2
        return 0
    except (OSError, ValueError) as exc:
        print("ONLINE-3A preflight failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
