#!/usr/bin/env python3
"""Independent M14-J J10 allocator qualification.

J10 is qualified in two separate senses:
1. allocator/bundle mechanism and authority separation;
2. adaptive STOP policy promotion.

The current PR intentionally qualifies (1) while leaving (2) false because the
pre-outcome independent seed corpus has only 16 source groups versus the frozen
32-group minimum.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.resource_allocator import (
    BUY_BUNDLE,
    STAGED_BUNDLE_ID,
    load_allocation_policy,
    validate_bundle_against_scheduler,
)
from controller.work_scheduler import load_work_scheduler_catalog


J9_CONFIG = ROOT / "config/allfather.m14-j-j9.validation.json"
J10_CONFIG = ROOT / "config/allfather.m14-j-j10.validation.json"
POLICY = ROOT / "qualification/adaptive-resource-allocation-v1.json"
CORPUS = ROOT / "qualification/j10-calibration-corpus-v1.json"
SOURCE = ROOT / "qualification/j10-calibration-source-v1.json"
STAGED_STATUS = ROOT / "qualification/j10-staged-decision-model-v1.json"
REGIME_STATUS = ROOT / "qualification/j10-regime-support-model-v1.json"
J7 = ROOT / "qualification/resource-profile-selection-v1.json"
ENGINE_SELECTION = ROOT / "qualification/engine-opt-v2-selection.json"
SCHEDULER = ROOT / "qualification/work-grant-scheduler-v1.json"


class J10QualificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise J10QualificationError(message)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path}: root must be object")
    return value


def parse_seed(path: Path) -> tuple[int, set[str]]:
    rows = 0
    groups: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        require(len(parts) == 4, f"malformed J10 seed row: {raw!r}")
        case_id, group, fen, _ = parts
        require(bool(case_id), "J10 seed case id is empty")
        require(bool(group), "J10 seed source group is empty")
        require(group not in groups, f"duplicate J10 source group {group!r}")
        require(len(fen.split()) == 6, f"{case_id}: malformed FEN")
        groups.add(group)
        rows += 1
    return rows, groups


def main() -> int:
    j9 = load(J9_CONFIG)
    j10 = load(J10_CONFIG)
    corpus = load(CORPUS)
    source = load(SOURCE)
    staged = load(STAGED_STATUS)
    regime = load(REGIME_STATUS)
    j7 = load(J7)
    engine_selection = load(ENGINE_SELECTION)

    allocation = load_allocation_policy(POLICY)
    scheduler, _ = load_work_scheduler_catalog(SCHEDULER)
    validate_bundle_against_scheduler(
        allocation,
        scheduler_catalog_id=scheduler.catalog_id,
        scheduler_catalog_digest=scheduler.digest,
        scheduler_chunks=scheduler.chunks,
    )

    require(
        allocation.bundle.bundle_id == STAGED_BUNDLE_ID,
        "J10 v1 bundle id drift",
    )
    require(
        set(allocation.bundle.chunk_ids)
        == {
            "compat/stockfish/staged-verify/n32",
            "compat/reckless/staged-verify/n32",
            "compat/lc0/staged-verify/n32",
        },
        "J10 v1 bundle must contain exactly the three J9 staged n32 chunks",
    )
    require(
        allocation.stop_promotion is False,
        "J10 STOP promotion unexpectedly enabled",
    )
    require(
        allocation.staged_model_path is None
        and allocation.regime_model_path is None,
        "unpromoted J10 policy may not load serving models",
    )

    seed_path = ROOT / str(corpus["path"])
    seed_sha = hashlib.sha256(seed_path.read_bytes()).hexdigest()
    require(
        seed_sha == corpus.get("content_sha256"),
        "J10 seed corpus SHA-256 drift",
    )
    rows, groups = parse_seed(seed_path)
    require(rows == 16 and len(groups) == 16, "J10 seed corpus must freeze 16 unique groups")
    frozen_groups = corpus.get("source_groups")
    require(
        isinstance(frozen_groups, list)
        and all(isinstance(group, str) and group for group in frozen_groups)
        and len(frozen_groups) == len(set(frozen_groups))
        and set(frozen_groups) == groups,
        "J10 corpus source_groups must exactly match the pre-label seed",
    )
    require(
        corpus.get("independent_groups") == 16
        and corpus.get("minimum_independent_groups_for_stop_promotion") == 32,
        "J10 independence threshold metadata drift",
    )
    require(
        corpus.get("promotion_eligible") is False
        and corpus.get("labels_frozen") is False,
        "J10 seed corpus must remain pre-label and non-promotable",
    )

    retained = source.get("retained_descriptive_evidence") or {}
    require(
        source.get("source_pr_head")
        == "91fb84f0479973e2382e1b8f3f9ac87e3e6910be",
        "J10 descriptive source head drift",
    )
    require(
        source.get("workflow_run_id") == 36856445310,
        "J10 descriptive source workflow drift",
    )
    artifact = source.get("artifact") or {}
    require(
        artifact.get("id") == 11164651195
        and artifact.get("sha256")
        == "2b54238221df4641c850280c707fbf991f52d82851738ce616253d5a29976605",
        "J10 descriptive source artifact drift",
    )
    require(
        retained.get("completed_staged_interventions") == 796
        and retained.get("unique_position_ids") == 737
        and retained.get("decision_policy_changed") == 183
        and retained.get("terminal_vector_changed") == 375,
        "J10 retained descriptive counts drift",
    )
    require(
        (source.get("independence_assessment") or {}).get(
            "promotion_training_eligible"
        )
        is False,
        "correlated PR56 plies may not become J10 promotion training data",
    )

    for status, label in ((staged, "staged"), (regime, "regime")):
        require(
            status.get("promotion") is False,
            f"J10 {label} model unexpectedly promoted",
        )
        require(
            status.get("status")
            == "not_fitted_insufficient_independent_groups",
            f"J10 {label} model status drift",
        )
        require(
            status.get("independent_groups") == 16
            and status.get("required_independent_groups") == 32,
            f"J10 {label} independence counters drift",
        )

    # J10 is an additive profile over J9: replay root, allocator-policy identity
    # and explicit resource_allocator block are the only config differences.
    stripped = copy.deepcopy(j10)
    allocator_block = stripped.pop("resource_allocator", None)
    stripped["shadow"]["replay_root"] = j9["shadow"]["replay_root"]
    stripped["orchestration"]["allocator_policy_id"] = j9["orchestration"][
        "allocator_policy_id"
    ]
    require(stripped == j9, "J10 runtime changes behavior outside J10 additions")
    require(
        allocator_block
        == {
            "enabled": True,
            "policy": "adaptive_resource_v1",
            "catalog": "qualification/adaptive-resource-allocation-v1.json",
        },
        "J10 runtime allocator block drift",
    )
    require(
        j10["orchestration"]["allocator_policy_id"]
        == "adaptive-resource-v1",
        "J10 MoveResourcePlan allocator policy identity drift",
    )

    require(j10.get("hybrid_authority") is None, "J10 gained hybrid move authority")
    require(j10.get("crossfeed") is None, "J10 gained crossfeed")
    require(j10.get("counterfactual") is None, "J10 gained counterfactual")
    require(j10.get("refinement") is None, "J10 gained REFINE")
    require(
        (j10.get("routing") or {}).get("policy") == "conservative_v1",
        "J10 must retain conservative_v1 resource authority",
    )

    # Transaction rollback safety freezes VERIFY/STAGED_VERIFY to identical
    # effective option states.
    for name, spec in j10["instances"].items():
        if spec.get("role") == "anchor":
            continue
        base = dict(spec.get("options") or {})
        phase = spec.get("phase_options") or {}
        verify = {**base, **dict(phase.get("VERIFY") or {})}
        staged_opts = {**base, **dict(phase.get("STAGED_VERIFY") or {})}
        require(
            verify == staged_opts,
            f"{name}: J10 VERIFY/STAGED_VERIFY options differ",
        )
        selected = engine_selection.get("selected") or {}
        if spec["family"] == "stockfish":
            require(
                spec["options"].get("Hash") == (selected.get("stockfish") or {}).get("hash_mb"),
                f"{name}: Stockfish Hash differs from canonical ENGINE-OPT selection",
            )
        if spec["family"] == "reckless":
            require(
                spec["options"].get("Hash") == (selected.get("reckless") or {}).get("hash_mb"),
                f"{name}: Reckless Hash differs from canonical ENGINE-OPT selection",
            )
        if spec["family"] == "lc0":
            lc0_selected = selected.get("lc0") or {}
            for option, value in {
                "NNCacheSize": lc0_selected.get("nn_cache_size"),
                "MinibatchSize": lc0_selected.get("minibatch_size"),
                "MaxPrefetch": lc0_selected.get("max_prefetch"),
                "AdaptivePrefetch": lc0_selected.get("adaptive_prefetch"),
                "DefectTelemetry": lc0_selected.get("defect_telemetry"),
            }.items():
                require(
                    spec["options"].get(option) == value,
                    f"{name}: LC0 {option} differs from canonical ENGINE-OPT selection",
                )

    require(
        (j7.get("claim_boundary") or {}).get("runtime_profile_selection")
        is False,
        "J10 may not consume J7 runtime profile selection",
    )

    report = {
        "schema_version": 1,
        "qualified": True,
        "policy": allocation.policy_id,
        "bundle_id": allocation.bundle.bundle_id,
        "bundle_chunks": list(allocation.bundle.chunk_ids),
        "scheduler_catalog_digest": scheduler.digest,
        "allocator_mechanism_qualified": True,
        "adaptive_stop_promoted": False,
        "runtime_default_action": BUY_BUNDLE,
        "calibration": {
            "seed_rows": rows,
            "independent_groups": len(groups),
            "minimum_independent_groups": allocation.minimum_independent_groups,
            "labels_frozen": False,
        },
        "authority": {
            "allocation_nomination": True,
            "resource_authorization": False,
            "outward_move": False,
        },
        "next": "collect leakage-free J10 calibration evidence or proceed to J11 evidence hardening with BUY-only J10",
    }
    out = ROOT / "build/test-results/adaptive-resource-allocator/report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (J10QualificationError, OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"J10 adaptive allocator qualification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
