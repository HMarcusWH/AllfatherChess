#!/usr/bin/env python3
"""Same-host diagnostic for two LC0 binaries under the frozen v2 profile.

This is diagnostic evidence only. It never promotes a profile or changes a
qualification threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.compare import bestmove_agreement, summarize
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.report import execution_identity, sha256, write_report
from tools.engine_opt.runner import run_case


OPTIONS = {
    "Backend": "blas",
    "BackendOptions": "",
    "NNCacheSize": 262144,
    "MinibatchSize": 7,
    "MaxConcurrentSearchers": 1,
    "TaskWorkers": 0,
    "Threads": 1,
    "MultiPV": 1,
    "ScoreType": "WDL_mu",
    "MaxPrefetch": 8,
    "AdaptivePrefetch": False,
    "DefectTelemetry": False,
    "UCI_Chess960": False,
}
ENVIRONMENT = {
    "OPENBLAS_NUM_THREADS": "1",
    "GOTO_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
}


def run_side(
    binary: Path,
    weights: Path,
    *,
    nodes: int,
    warmup_nodes: int,
    deadline_ms: float,
) -> list[dict]:
    rows = []
    options = {**OPTIONS, "WeightsFile": str(weights.resolve())}
    for case in load_epd(ROOT / "tests/fixtures/engine_opt/positions.epd"):
        rows.append(
            run_case(
                binary=binary.resolve(),
                cwd=ROOT,
                family="lc0",
                case=case,
                options=dict(options),
                nodes=nodes,
                deadline_ms=deadline_ms,
                environment=dict(ENVIRONMENT),
                args=["--show-hidden"],
                warmup_nodes=warmup_nodes,
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--left-label", default="historical")
    parser.add_argument("--right-label", default="current")
    parser.add_argument("--nodes", type=int, default=16)
    parser.add_argument("--warmup-nodes", type=int, default=64)
    parser.add_argument("--deadline-ms", type=float, default=3500.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    left = run_side(
        args.left,
        args.weights,
        nodes=args.nodes,
        warmup_nodes=args.warmup_nodes,
        deadline_ms=args.deadline_ms,
    )
    right = run_side(
        args.right,
        args.weights,
        nodes=args.nodes,
        warmup_nodes=args.warmup_nodes,
        deadline_ms=args.deadline_ms,
    )
    payload = {
        "schema_version": 1,
        "kind": "lc0-same-host-binary-diagnostic",
        "claim_boundary": {
            "diagnostic_only": True,
            "profile_promotion": False,
            "strength": False,
            "elo": False,
        },
        "nodes": args.nodes,
        "warmup_nodes": args.warmup_nodes,
        "deadline_ms": args.deadline_ms,
        "weights_sha256": sha256(args.weights),
        "left": {
            "label": args.left_label,
            "binary_sha256": sha256(args.left),
            "execution_domain": execution_identity(args.left),
            "summary": summarize(left),
            "rows": left,
        },
        "right": {
            "label": args.right_label,
            "binary_sha256": sha256(args.right),
            "execution_domain": execution_identity(args.right),
            "summary": summarize(right),
            "rows": right,
        },
        "bestmove_agreement": bestmove_agreement(left, right),
    }
    write_report(args.output, payload)
    print(
        json.dumps(
            {
                "bestmove_agreement": payload["bestmove_agreement"],
                "left_binary": payload["left"]["binary_sha256"],
                "right_binary": payload["right"]["binary_sha256"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
