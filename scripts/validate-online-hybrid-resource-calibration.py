#!/usr/bin/env python3
"""Validate the evidence-backed legacy G3 resource reservation calibration."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "qualification/online-hybrid-v1-resource-calibration.json"
POLICY = ROOT / "qualification/online-hybrid-authority.json"
CONFIG = ROOT / "config/allfather.online-hybrid.validation.json"

EXPECTED_OUTER = {
    "wall_ms": 4000,
    "cpu_ms": 12000,
    "gpu_ms": 0,
    "verification_reserve_fraction": 0.3,
    "controller_overhead_reserve_ms": 250,
}
EXPECTED_CLAIM = {
    "legacy_v1_resource_reservation_calibration": True,
    "canonical_b4_promotion": False,
    "outer_envelope_increase": False,
    "resource_authorization": False,
    "outward_move_authority": False,
    "strength": False,
    "elo": False,
    "equal_compute": False,
    "deployment": False,
}

class CalibrationError(ValueError):
    pass

def require(ok: bool, message: str) -> None:
    if not ok:
        raise CalibrationError(message)

def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"{path}: cannot load JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path}: root must be object")
    return value

def finite_nonnegative(value: Any, label: str) -> float:
    require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    number = float(value)
    require(math.isfinite(number) and number >= 0.0, f"{label} must be finite/non-negative")
    return number

def digest(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value),
        f"{label} must be lowercase SHA-256",
    )
    return value

def commit(value: Any, label: str) -> str:
    require(
        isinstance(value, str) and len(value) == 40
        and all(ch in "0123456789abcdef" for ch in value),
        f"{label} must be a 40-character commit",
    )
    return value

def validate(
    calibration_path: Path = CALIBRATION,
    policy_path: Path = POLICY,
    config_path: Path = CONFIG,
) -> dict[str, Any]:
    calibration = load(calibration_path)
    policy = load(policy_path)
    config = load(config_path)

    require(calibration.get("schema_version") == 1, "unsupported calibration schema")
    require(
        calibration.get("profile_id") == "online-hybrid-v1-resource-calibration",
        "wrong calibration profile id",
    )
    require(calibration.get("claim_boundary") == EXPECTED_CLAIM, "calibration claim boundary drift")
    require(calibration.get("outer_envelope") == EXPECTED_OUTER, "outer envelope calibration drift")
    require(
        policy.get("resource_calibration")
        == {
            "path": "qualification/online-hybrid-v1-resource-calibration.json",
            "profile_id": "online-hybrid-v1-resource-calibration",
        },
        "G3 policy does not bind the frozen calibration",
    )

    sources = calibration.get("retained_local1_sources")
    require(isinstance(sources, list) and len(sources) == 2, "exactly two retained LOCAL-1 sources required")
    seen_runs: set[int] = set()
    maxima = {"controller": 0.0, "explore": 0.0, "verify": 0.0}
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"source {index} must be object")
        run = source.get("workflow_run")
        artifact_id = source.get("artifact_id")
        require(isinstance(run, int) and run > 0 and run not in seen_runs, "workflow run missing/duplicated")
        seen_runs.add(run)
        require(isinstance(artifact_id, int) and artifact_id > 0, "artifact id missing")
        digest(source.get("artifact_sha256"), f"source {index} artifact_sha256")
        commit(source.get("source_commit"), f"source {index} source_commit")
        require(isinstance(source.get("campaign_id"), str) and source["campaign_id"], f"source {index} campaign id missing")
        require(isinstance(source.get("route_count"), int) and source["route_count"] > 0, f"source {index} route count missing")
        observed = source.get("observed")
        require(isinstance(observed, dict), f"source {index} observations missing")
        for name in (
            "controller_total_cpu_ms",
            "lc0_explore_cpu_ms",
            "lc0_verify_cpu_ms",
            "lc0_staged_verify_extension_cpu_ms",
        ):
            row = observed.get(name)
            require(isinstance(row, dict), f"source {index} missing {name}")
            require(isinstance(row.get("samples"), int) and row["samples"] > 0, f"{name} samples missing")
            for metric in ("median", "p95", "p99", "max"):
                finite_nonnegative(row.get(metric), f"{name}.{metric}")
            require(
                row["median"] <= row["p95"] <= row["max"]
                and row["median"] <= row["p99"] <= row["max"],
                f"{name} percentile ordering invalid",
            )
        maxima["controller"] = max(maxima["controller"], float(observed["controller_total_cpu_ms"]["max"]))
        maxima["explore"] = max(maxima["explore"], float(observed["lc0_explore_cpu_ms"]["max"]))
        maxima["verify"] = max(
            maxima["verify"],
            float(observed["lc0_verify_cpu_ms"]["max"]),
            float(observed["lc0_staged_verify_extension_cpu_ms"]["max"]),
        )

    selection = calibration.get("selection")
    require(isinstance(selection, dict), "calibration selection missing")
    require(selection.get("controller_overhead_reserve_ms") == 250, "controller reserve must remain 250 ms")
    require(maxima["controller"] <= 250.0, "retained controller spend exceeds the selected reserve")
    explore = selection.get("explore_cpu_ms_by_owner")
    verify = selection.get("verify_cpu_ms_by_owner")
    fallback = selection.get("fallback_cpu_ms_per_stage")
    require(explore == {"stockfish": 100, "reckless": 100, "lc0": 1800}, "EXPLORE owner reservations drift")
    require(verify == {"stockfish": 100, "reckless": 100, "lc0": 1400}, "VERIFY owner reservations drift")
    require(fallback == {"explore": 500, "verify": 750}, "fallback reservations drift")
    require(
        explore["lc0"] == int(math.ceil(maxima["explore"] / 100.0) * 100),
        "LC0 EXPLORE selection no longer reconstructs from retained maxima",
    )
    require(
        verify["lc0"] == int(math.ceil(maxima["verify"] / 100.0) * 100 + 100),
        "LC0 VERIFY selection no longer reconstructs from retained maxima + headroom",
    )

    budget = config.get("budget") or {}
    for key, value in EXPECTED_OUTER.items():
        require(budget.get(key) == value, f"runtime outer envelope drift: {key}")
    routing = config.get("routing") or {}
    require(
        routing.get("stage_cpu_ms_estimate_by_owner") == explore,
        "runtime EXPLORE owner reservations differ from calibration",
    )
    require(
        routing.get("verify_stage_cpu_ms_estimate_by_owner") == verify,
        "runtime VERIFY owner reservations differ from calibration",
    )
    require(
        routing.get("stage_cpu_ms_estimate") == fallback["explore"]
        and routing.get("verify_stage_cpu_ms_estimate") == fallback["verify"],
        "runtime fallback reservation estimates differ from calibration",
    )
    require(
        (policy.get("explore") or {}).get("reservation_cpu_ms_by_owner") == explore,
        "G3 policy EXPLORE owner reservations differ from calibration",
    )
    require(
        (policy.get("verification") or {}).get("reservation_cpu_ms_by_owner") == verify,
        "G3 policy VERIFY owner reservations differ from calibration",
    )
    require(
        (policy.get("explore") or {}).get("reservation_cpu_ms_per_stage") == fallback["explore"]
        and (policy.get("verification") or {}).get("reservation_cpu_ms_per_stage") == fallback["verify"],
        "G3 policy fallback reservations differ from calibration",
    )

    return {
        "schema_version": 1,
        "profile_id": calibration["profile_id"],
        "qualified": True,
        "retained_sources": len(sources),
        "observed_maxima_ms": maxima,
        "controller_overhead_reserve_ms": 250,
        "explore_cpu_ms_by_owner": explore,
        "verify_cpu_ms_by_owner": verify,
        "outer_envelope": EXPECTED_OUTER,
        "claim_boundary": EXPECTED_CLAIM,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.calibration, args.policy, args.config)
    except CalibrationError as exc:
        print(f"online-hybrid resource calibration FAILED: {exc}")
        return 2
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
