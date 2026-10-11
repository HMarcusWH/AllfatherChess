#!/usr/bin/env python3
"""Observation-only ONLINE-3A host preflight using M14-J's canonical Linux facts."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.resource.linux_host import LinuxHostProvider
from controller.host_capabilities import build_host_capabilities

def record(*, proc=Path("/proc"), cgroups=Path("/sys/fs/cgroup"), provider=None) -> dict:
    # Canonical provider inspects the whole ancestor chain, not just leaf limits.
    facts = (provider or LinuxHostProvider(proc_root=proc, cgroup_root=cgroups)).observe_capabilities()
    caps = build_host_capabilities(facts)
    finite_cpu = caps.cpu_quota_equivalents if caps.cpu_quota_status == "limited" else None
    finite_memory = (caps.effective_memory_limit_bytes
                     if caps.cgroup_memory_status == "limited" else None)
    count = len(caps.allowed_cpus) if caps.allowed_cpus is not None else None
    usable = min(count, finite_cpu) if count is not None and finite_cpu is not None else None
    complete = bool(caps.capacity_complete and facts.cpu_max_complete
                    and facts.memory_max_complete and usable is not None
                    and finite_memory is not None)
    return {
        "schema_version": 2, "scope": "observation_only",
        "provider_id": facts.provider_id,
        "system": caps.platform, "machine": caps.architecture,
        "affinity_cpu_count": len(caps.affinity_cpus) if caps.affinity_cpus else None,
        "allowed_cpu_count": count, "cgroup_path": facts.cgroup_path,
        "cpu_quota_status": caps.cpu_quota_status,
        "cpu_quota_cpus": finite_cpu,
        "cpu_max_chain": [row.as_dict() for row in caps.cpu_quota_observations],
        "memory_limit_status": caps.cgroup_memory_status,
        "memory_limit_bytes": finite_memory,
        "memory_max_chain": [row.as_dict() for row in caps.memory_limit_observations],
        "bounded_usable_cpus": usable, "capacity_complete": complete,
        "observation_faults": list(caps.faults),
        "j12_authority_qualified": False, "generic_host_portability": False,
    }

def operator_minimums(facts: dict, min_cpus: float, min_memory_mib: int) -> bool:
    return bool(facts["capacity_complete"]
                and facts["bounded_usable_cpus"] >= min_cpus
                and facts["memory_limit_bytes"] >= min_memory_mib * 1024**2
                and facts["system"] == "linux"
                and facts["machine"] in ("x86_64", "amd64"))

def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path)
    p.add_argument("--require-capacity", action="store_true")
    p.add_argument("--min-usable-cpus", type=float, default=4.0)
    p.add_argument("--min-memory-mib", type=int, default=4096)
    a = p.parse_args(argv)
    try:
        value = record()
        value["operator_minimums_met"] = operator_minimums(
            value, a.min_usable_cpus, a.min_memory_mib)
        body = json.dumps(value, sort_keys=True, indent=2) + "\n"
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            a.output.write_text(body)
        else:
            sys.stdout.write(body)
        if a.require_capacity and not value["operator_minimums_met"]:
            print("ONLINE-3A host preflight: incomplete or undersized capacity", file=sys.stderr)
            return 2
        return 0
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print("ONLINE-3A preflight failed: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
