#!/usr/bin/env python3
"""Read-only forensic report for one LOCAL-1 replay; never grants qualification.

Only original manifest/resource/route bytes are consumed. The output is a
diagnostic account of negative evidence and can never be used as DRY_RUN_PASS.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

FAILURE_MARKER=re.compile(
    r"freeze_wall_ms=(?P<freeze>[0-9.]+), serialization_ms=(?P<serialization>[0-9.]+), "
    r"durable_write_ms=(?P<durable>[0-9.]+), persisted_wall_ms=(?P<persisted>[0-9.]+)"
)


def inspect(run: Path, *, source_sha: str | None=None) -> dict[str, object]:
    run=Path(run)
    files={}
    data={}
    for filename in ("manifest.json","route.json","resource.json"):
        path=run/filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing/linked original replay evidence: "+filename)
        raw=path.read_bytes()
        files[filename]=hashlib.sha256(raw).hexdigest()
        data[filename]=json.loads(raw)
    manifest=data["manifest.json"];route=data["route.json"];resource=data["resource.json"]
    run_id=manifest.get("run_id")
    if run_id != run.name or route.get("run_id")!=run_id or resource.get("run_id")!=run_id:
        raise ValueError("mismatched original replay identifiers")
    if source_sha is not None and (
        not isinstance(source_sha,str) or
        re.fullmatch(r"[0-9a-f]{40}",source_sha) is None
    ):
        raise ValueError("source SHA must be a literal full hexadecimal commit")
    claim=route.get("envelope_claim") or {}
    budget=route.get("budget") or {}
    envelope=route.get("envelope") or {}
    processes=resource.get("processes") or {}
    categories=set()
    wall_ms=budget.get("elapsed_ms")
    limit_ms=envelope.get("wall_ms")
    excess_ms=None
    if (type(wall_ms) in (int,float) and type(limit_ms) in (int,float)
        and limit_ms > 0):
        excess_ms=round(max(0.0,float(wall_ms)-float(limit_ms)),3)
        if wall_ms>limit_ms or claim.get("wall_within_envelope") is False:
            categories.add("WALL_OVERRUN")
    if any(
        isinstance(row,dict) and row.get("complete") is False
        and "terminal sample failed" in str(row.get("reason") or "")
        for row in processes.values()
    ):
        categories.add("PROCESS_TERMINAL_SAMPLE_MISSING")
    if resource.get("qualified") is False:
        categories.add("PHYSICAL_RESOURCE_UNQUALIFIED")
    phases=None
    for note in manifest.get("notes") or ():
        if not isinstance(note,str):
            continue
        match=FAILURE_MARKER.search(note)
        if match:
            phases={name:float(value) for name,value in match.groupdict().items()}
    if phases and phases["durable"] >= 10.0:
        categories.add("SEAL_IO_LATENCY")
    output=manifest.get("clock_outcome")
    if isinstance(output,dict) and output.get("output_within_deadline") is not True:
        categories.add("PRE_PUBLICATION_FAILURE")
    return {
        "schema_version":1,
        "diagnostic_only":True,
        "qualifies":False,
        "source_sha_declared_not_attested":source_sha,
        "run_id":run_id,
        "original_file_sha256":files,
        "categories":sorted(categories),
        "wall_elapsed_ms":wall_ms,
        "wall_limit_ms":limit_ms,
        "wall_excess_ms":excess_ms,
        "seal_phase_diagnostic_ms":phases,
        "original_claimed":claim.get("claimed") is True,
        "original_cpu_qualified":resource.get("qualified") is True,
        "incomplete_processes":sorted(name for name,row in processes.items()
                                        if isinstance(row,dict) and row.get("complete") is False),
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("replay",type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    record=inspect(args.replay,source_sha=args.source_sha)
    payload=json.dumps(record,indent=2,sort_keys=True)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload,end="")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
