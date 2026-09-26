"""M14-G3 static composition contract.

This validator leaves ONLINE-2 frozen and validates a separate clock-aware
staged-authority profile on top of the same real-network artifact identities.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

POLICY_SCHEMA_VERSION = 1

class OnlineHybridProfileError(RuntimeError):
    pass

def load_json(path: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnlineHybridProfileError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OnlineHybridProfileError(f"{path}: root must be an object")
    return data

def _eq(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise OnlineHybridProfileError(
            f"{label} mismatch: expected {expected!r}, got {actual!r}"
        )

def validate_online_hybrid_profile(
    policy: dict[str, Any],
    config: dict[str, Any],
    online2_config: dict[str, Any],
) -> None:
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise OnlineHybridProfileError("G3 qualification schema_version must be 1")
    if policy.get("qualification_tier") != "online-hybrid-authority":
        raise OnlineHybridProfileError("qualification_tier must be online-hybrid-authority")
    if policy.get("source_profile") != "online-cpu-reference-v1":
        raise OnlineHybridProfileError("G3 must derive from frozen ONLINE-2")
    if policy.get("bundle_root") != "build/online-cpu-reference":
        raise OnlineHybridProfileError("G3 must reuse the isolated ONLINE-2 bundle")
    if policy.get("allow_skipped_extension_authority") is not False:
        raise OnlineHybridProfileError("G3 may not license SKIP-derived authority")

    for key in ("instances", "resource_measurement"):
        _eq(config.get(key), online2_config.get(key), f"runtime.{key}")

    online = config.get("online_time")
    online2 = online2_config.get("online_time")
    if not isinstance(online, dict) or not isinstance(online2, dict):
        raise OnlineHybridProfileError("G3 and ONLINE-2 require online_time objects")
    for key, value in online2.items():
        if key == "max_move_ms":
            continue
        _eq(online.get(key), value, f"runtime.online_time.{key}")
    _eq(
        online.get("max_move_ms"),
        policy.get("clock", {}).get("max_move_ms"),
        "runtime.online_time.max_move_ms",
    )
    if online["max_move_ms"] > (config.get("budget") or {}).get("wall_ms", 0):
        raise OnlineHybridProfileError(
            "G3 max_move_ms may not exceed the frozen outer wall envelope"
        )
    for key in ("wall_ms", "cpu_ms", "gpu_ms", "controller_overhead_reserve_ms"):
        _eq(
            (config.get("budget") or {}).get(key),
            (online2_config.get("budget") or {}).get(key),
            f"budget.{key}",
        )
    if (config.get("budget") or {}).get("verification_reserve_fraction", 0) <= 0:
        raise OnlineHybridProfileError("G3 requires a positive VERIFY reserve")

    verify = config.get("verification")
    if not isinstance(verify, dict) or verify.get("enabled") is not True:
        raise OnlineHybridProfileError("G3 requires verification.enabled")
    _eq(verify.get("dispatch_limit"), policy["verification"]["base"], "base VERIFY")
    ext = verify.get("staged_extension")
    if not isinstance(ext, dict) or ext.get("enabled") is not True:
        raise OnlineHybridProfileError("G3 requires staged VERIFY")
    _eq(ext.get("dispatch_limit"), policy["verification"]["extension"], "staged VERIFY")
    _eq(ext.get("intervention"), policy["verification"]["intervention"], "intervention")

    if (config.get("routing") or {}).get("policy") != "unified_value_v1":
        raise OnlineHybridProfileError("G3 requires unified_value_v1 routing")
    if (config.get("routing") or {}).get("staged_decision_calibration") is not None:
        raise OnlineHybridProfileError("G3 does not promote a learned SKIP model")
    if (config.get("routing") or {}).get("regime_support_calibration") is not None:
        raise OnlineHybridProfileError("G3 does not promote a regime SKIP model")

    _eq(config.get("crossfeed"), {"enabled": True, "policy": "typed_verify_refine_v1"}, "crossfeed")
    _eq(config.get("counterfactual"), {"enabled": True, "policy": "unanimous_verify_v1"}, "counterfactual")
    authority = config.get("hybrid_authority")
    if not isinstance(authority, dict) or authority.get("enabled") is not True:
        raise OnlineHybridProfileError("G3 requires hybrid_authority.enabled")
    _eq(authority.get("policy"), policy["authority_policy"], "authority policy")
    _eq(authority.get("request_class"), "online_time_v1", "authority request class")
    _eq(authority.get("terminal_source_policy"), policy["terminal_source_policy"], "terminal source policy")
    if authority.get("allow_skipped_extension_authority") is not False:
        raise OnlineHybridProfileError("G3 SKIP authority must remain disabled")
    if "refinement" in config:
        raise OnlineHybridProfileError("G3 keeps recursive REFINE out of authority")
