#!/usr/bin/env python3
"""Deterministic live contract for M14-D bounded recursive REFINE.

The contract uses the repository fake UCI engines so the disagreement pattern
and recursion depth are stable. Real-engine descendant-prefix capability remains
covered separately by scripts/prefix-shard-contract.py.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from controller.refinement import (
    load_refinement_manifest,
    verify_refinement_integrity,
)
from controller.replay import verify_bundle_integrity
from tests.controller.test_shadow_runtime import (
    ANCHOR,
    run_shell,
    write_shadow_config,
)


ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "build" / "test-results" / "recursive-refinement"


class ContractError(RuntimeError):
    pass


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = write_shadow_config(
            root,
            mode="active",
            refinement=True,
            dispatch_nodes=64,
            verification_nodes=32,
            refinement_nodes=32,
            instance_args={
                ANCHOR: ["--info-lines", "400", "--info-delay-ms", "10"],
                "stockfish-shadow": ["--leader-schedule", "e2e4"],
                "reckless-shadow": ["--leader-schedule", "d2d4"],
                "lc0-shadow": ["--leader-schedule", "g1f3"],
            },
        )
        document = json.loads(config.read_text(encoding="utf-8"))
        document["refinement"].update(
            {
                "recursive_nomination_method": "stage_terminal_bestmove_v1",
                "max_depth": 3,
                "max_expansions": 2,
            }
        )
        document["budget"] = {
            "wall_ms": 12000,
            "cpu_ms": 60000,
            "gpu_ms": 0,
            "verification_reserve_fraction": 0.20,
            "refinement_reserve_fraction": 0.50,
            "controller_overhead_reserve_ms": 1500,
        }
        document["routing"] = {
            "policy": "conservative_v1",
            "calibration": None,
            "min_observation_nodes": 1,
            "checkpoint_interval_ms": 50,
            "max_stages_per_owner": 1,
            "extend_nodes": 64,
            "stop_max_reversal_risk": 0.05,
            "stop_min_support": 25,
            "stop_min_stability_fraction": 0.6,
            "stage_cpu_ms_estimate": 500,
            "anchor_cpu_ms_estimate": 12000,
            "stage_gpu_ms_estimate": 0,
            "verify_stage_cpu_ms_estimate": 500,
            "verify_stage_gpu_ms_estimate": 0,
            "refine_stage_cpu_ms_estimate": 500,
            "refine_stage_gpu_ms_estimate": 0,
            "refine_oracle_cpu_ms_estimate": 200,
            "refine_oracle_gpu_ms_estimate": 0,
        }
        document["resource_measurement"] = {
            "enabled": True,
            "provider": "linux-procfs-v1",
            "require_cpu_for_claim": True,
            "require_gpu_for_claim": False,
            "record_memory": True,
        }
        config.write_text(json.dumps(document), encoding="utf-8")

        lines = run_shell(
            config,
            ["go movetime 8000", "await:bestmove "],
            timeout=45.0,
        )
        outward = [line for line in lines if line.startswith("bestmove ")]
        if len(outward) != 1:
            raise ContractError(f"expected exactly one outward bestmove, got {outward}")

        run_dirs = sorted(
            path for path in (root / "replays").iterdir() if path.is_dir()
        )
        if len(run_dirs) != 1:
            raise ContractError(f"expected one replay run, got {len(run_dirs)}")
        run_dir = run_dirs[0]

        parent_problems = verify_bundle_integrity(run_dir)
        refine_problems = verify_refinement_integrity(run_dir)
        if parent_problems:
            raise ContractError(f"parent replay integrity failed: {parent_problems}")
        if refine_problems:
            raise ContractError(f"recursive REFINE integrity failed: {refine_problems}")

        manifest = load_refinement_manifest(run_dir)
        if manifest.get("schema_version") != 2:
            raise ContractError("recursive REFINE did not seal schema v2")
        recursive = manifest.get("recursive_policy") or {}
        if recursive.get("max_depth") != 3 or recursive.get("max_expansions") != 2:
            raise ContractError(f"unexpected recursive bounds: {recursive}")

        expansions = manifest.get("expansions") or []
        if len(expansions) != 2:
            raise ContractError(f"expected exactly two recursive expansions, got {len(expansions)}")
        if any(row.get("depth") != 2 for row in expansions):
            raise ContractError(f"recursive expansion depth is not deterministic: {expansions}")
        if any((row.get("disposition") or {}).get("expansion") != "completed" for row in expansions):
            raise ContractError("a recursive expansion did not complete cleanly")

        route = json.loads((run_dir / "route.json").read_text(encoding="utf-8"))
        budget = route.get("budget") or {}
        if budget.get("open_reservations") != 0:
            raise ContractError(f"recursive run leaked reservations: {budget}")
        if not budget.get("within_envelope") or not budget.get("within_partition_caps"):
            raise ContractError(f"recursive run exceeded declared budget: {budget}")

        specialist = route.get("specialist_actions") or []
        grants = [
            row
            for row in specialist
            if row.get("event") == "authorize" and row.get("granted")
        ]
        phases = {row.get("phase") for row in grants}
        if not {"verify", "refine", "refine_oracle"}.issubset(phases):
            raise ContractError(f"missing recursive specialist authorization phases: {phases}")

        final_snapshot = (manifest.get("prefix_ledger") or {}).get(
            "final_v2_snapshot"
        ) or {}
        by_id = {
            row.get("id"): row
            for row in final_snapshot.get("shards") or []
            if isinstance(row, dict)
        }
        for expansion in expansions:
            source = by_id.get(expansion.get("source_shard_id"))
            if source is None or source.get("state") != "retired":
                raise ContractError(
                    f"recursive source shard was not retired: {expansion.get('expansion_id')}"
                )

        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1,
            "outward": outward[0],
            "refinement_schema_version": manifest["schema_version"],
            "recursive_policy": recursive,
            "expansions": [
                {
                    "expansion_id": row["expansion_id"],
                    "prefix": row["prefix"],
                    "depth": row["depth"],
                }
                for row in expansions
            ],
            "open_reservations": budget.get("open_reservations"),
            "within_envelope": budget.get("within_envelope"),
            "within_partition_caps": budget.get("within_partition_caps"),
            "specialist_phases": sorted(str(value) for value in phases),
        }
        (RESULT_DIR / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        "recursive refinement contract: "
        f"expansions={len(expansions)}, phases={sorted(str(value) for value in phases)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
