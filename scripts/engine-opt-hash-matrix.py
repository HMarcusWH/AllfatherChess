#!/usr/bin/env python3
"""Repeated, counterbalanced transposition-hash sensitivity benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.constituent_hash import (
    load_case_ids,
    load_policy,
    schedule,
)
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.runner import run_case
from tools.engine_opt.compare import summarize
from tools.engine_opt.report import host_identity, source_identity, sha256, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("stockfish", "reckless"), required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--nodes", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    policy = load_policy()
    nodes = policy["nodes"][args.family]
    if args.nodes is not None and args.nodes != nodes:
        raise SystemExit(
            f"{args.family}: --nodes {args.nodes} differs from frozen protocol {nodes}"
        )
    cases = load_epd(ROOT / policy["corpus_path"])
    case_by_id = {case.case_id: case for case in cases}
    case_ids = load_case_ids(policy)
    if set(case_by_id) != set(case_ids):
        raise SystemExit("frozen corpus parser/case identity mismatch")

    rows: list[dict] = []
    errors: list[dict] = []
    summaries: dict[str, dict] = {}
    plan = schedule(case_ids, repeats=policy["repeats"])
    for slot in plan:
        hash_mb = slot["hash_mb"]
        case = case_by_id[slot["case_id"]]
        options = {
            "Threads": 1,
            "Hash": hash_mb,
            "MultiPV": 1,
            "UCI_Chess960": False,
        }
        if args.family == "reckless":
            options["Minimal"] = False
        try:
            row = run_case(
                binary=args.binary.resolve(),
                cwd=ROOT,
                family=args.family,
                case=case,
                options=dict(options),
                nodes=nodes,
                deadline_ms=10000.0,
            )
            row.update(slot)
            rows.append(row)
        except Exception as exc:
            errors.append({
                **slot,
                "error": f"{type(exc).__name__}: {exc}",
            })

    for hash_mb in policy["hash_mb"]:
        group = [row for row in rows if row.get("hash_mb") == hash_mb]
        if group:
            summaries[str(hash_mb)] = summarize(group)

    protocol = {
        "protocol_id": policy["protocol_id"],
        "repeats": policy["repeats"],
        "hash_mb": policy["hash_mb"],
        "ordering": policy["ordering"],
        "attempt_policy": policy["attempt_policy"],
        "efficiency_band": policy["efficiency_band"],
        "repeat_interval": policy["repeat_interval"],
    }
    payload = {
        "schema_version": 2,
        "kind": "engine-opt-hash-matrix-v2",
        "family": args.family,
        "source": source_identity(ROOT),
        "host": host_identity(),
        "binary": {"path": str(args.binary), "sha256": sha256(args.binary)},
        "nodes": nodes,
        "protocol": protocol,
        "rows": rows,
        "summaries": summaries,
        "errors": errors,
        "claim_boundary": {
            "strength": False,
            "elo": False,
            "benchmark_only": True,
        },
    }
    write_report(args.output, payload)
    print(json.dumps({
        "family": args.family,
        "rows": len(rows),
        "errors": len(errors),
        "repeats": policy["repeats"],
        "summaries": summaries,
    }, sort_keys=True))
    expected = len(case_ids) * len(policy["hash_mb"]) * policy["repeats"]
    return 0 if len(rows) == expected and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
