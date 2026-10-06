"""Fail-closed canonical b4 promotion gate for ENGINE-OPT-V2."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class PromotionGateError(ValueError):
    pass


ALLOWED_DISPOSITIONS = {"QUALIFIED_EXACT_HOST_ONLY", "QUALIFIED_REUSABLE_DOMAIN"}
EXPECTED_LC0 = {
    "matrix_profile": "b4-p0-c256k-cold",
    "nn_cache_size": 262144,
    "minibatch_size": 4,
    "max_prefetch": 0,
    "adaptive_prefetch": False,
    "defect_telemetry": False,
    "warmup_nodes": None,
}
RUNTIME_FILES = (
    "config/allfather.online-engine-opt-v2.json",
    "config/allfather.online-hybrid-v2.validation.json",
    "config/allfather.m14-j-j8.validation.json",
    "config/allfather.m14-j-j9.validation.json",
    "config/allfather.m14-j-j10.validation.json",
    "config/allfather.orchestrated-v1.validation.json",
)
UNCHANGED_GUARD_FILES = (
    "qualification/online-engine-opt-v2.json",
    "qualification/engine-derived-lock.json",
    "qualification/lc0-strength.lock.json",
    "qualification/lc0-strength-profile.json",
    "vendor.lock.json",
    "scripts/build-online-engine-opt-v2.sh",
    "scripts/build-lc0-strength.sh",
    "scripts/lc0-strength-lock.py",
)
ENGINE_TREES = ("engines/stockfish", "engines/reckless", "engines/lc0")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise PromotionGateError(message)


def _git(repo_root: Path, *args: str, text: bool = True) -> str | bytes:
    result = subprocess.check_output(["git", "-C", str(repo_root), *args], text=text)
    return result.strip() if text else result


def _base_json(repo_root: Path, base_sha: str, path: str) -> dict[str, Any]:
    try:
        raw = _git(repo_root, "show", f"{base_sha}:{path}", text=False)
        assert isinstance(raw, bytes)
        value = json.loads(raw.decode("utf-8"))
    except (subprocess.CalledProcessError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionGateError(f"cannot reconstruct PR-base file {path}: {exc}") from exc
    require(isinstance(value, dict), f"PR-base file {path} must contain an object")
    return value


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionGateError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path}: root must be object")
    return value


def _assert_b4_runtime(doc: dict[str, Any], label: str) -> None:
    lc0 = ((doc.get("instances") or {}).get("lc0-shadow") or {})
    options = lc0.get("options") or {}
    require(options.get("NNCacheSize") == 262144, f"{label}: NNCacheSize drift")
    require(options.get("MinibatchSize") == 4, f"{label}: MinibatchSize drift")
    require(options.get("MaxPrefetch") == 0, f"{label}: MaxPrefetch drift")
    require(options.get("AdaptivePrefetch") is False, f"{label}: AdaptivePrefetch drift")
    require(options.get("DefectTelemetry") is False, f"{label}: DefectTelemetry drift")
    require("warmup" not in lc0, f"{label}: cold b4 profile unexpectedly has warmup")


def compare_promotion_surface(repo_root: Path, base_sha: str) -> dict[str, Any]:
    require(isinstance(base_sha, str) and len(base_sha) == 40, "PR base SHA missing")
    head = str(_git(repo_root, "rev-parse", "HEAD"))
    parent = str(_git(repo_root, "rev-parse", "HEAD^"))

    base_selection = _base_json(repo_root, base_sha, "qualification/engine-opt-v2-selection.json")
    current_selection = _load(repo_root / "qualification/engine-opt-v2-selection.json")
    base_selected = base_selection.get("selected") or {}
    current_selected = current_selection.get("selected") or {}
    require(
        ((base_selected.get("lc0") or {}).get("matrix_profile")) == "b7-p8-c256k-warm64",
        "PR base is not the expected b7 canonical profile",
    )
    current_lc0 = current_selected.get("lc0") or {}
    for key, value in EXPECTED_LC0.items():
        require(current_lc0.get(key) == value, f"canonical b4 selection {key} drift")
    for key in ("stockfish", "reckless", "resource_estimates_ms"):
        require(current_selected.get(key) == base_selected.get(key), f"promotion changed {key}")
    require(
        current_selection.get("qualification") == base_selection.get("qualification"),
        "promotion changed matrix qualification thresholds",
    )

    changed_runtime: list[str] = []
    for rel in RUNTIME_FILES:
        base = _base_json(repo_root, base_sha, rel)
        current = _load(repo_root / rel)
        _assert_b4_runtime(current, rel)
        normalized = json.loads(json.dumps(current))
        normalized_lc0 = normalized["instances"]["lc0-shadow"]
        base_lc0 = base["instances"]["lc0-shadow"]
        for option in ("NNCacheSize", "MinibatchSize", "MaxPrefetch", "AdaptivePrefetch", "DefectTelemetry"):
            normalized_lc0["options"][option] = base_lc0["options"][option]
        if "warmup" in base_lc0:
            normalized_lc0["warmup"] = base_lc0["warmup"]
        else:
            normalized_lc0.pop("warmup", None)
        require(normalized == base, f"{rel}: promotion changed behavior outside canonical LC0 profile")
        changed_runtime.append(rel)

    unchanged_files: list[str] = []
    for rel in UNCHANGED_GUARD_FILES:
        current = (repo_root / rel).read_bytes()
        base = _git(repo_root, "show", f"{base_sha}:{rel}", text=False)
        require(current == base, f"promotion changed guarded build/source contract {rel}")
        unchanged_files.append(rel)

    unchanged_trees: list[str] = []
    for rel in ENGINE_TREES:
        base_tree = str(_git(repo_root, "rev-parse", f"{base_sha}:{rel}"))
        current_tree = str(_git(repo_root, "rev-parse", f"HEAD:{rel}"))
        require(base_tree == current_tree, f"promotion changed engine tree {rel}")
        unchanged_trees.append(rel)

    catalog = _load(repo_root / "qualification/resource-profile-catalog-v1.json")
    require(catalog.get("selection_enabled") is False, "promotion enabled runtime profile selection")
    require(catalog.get("fallback_profile") == "engine-opt-v2", "promotion changed fallback profile")
    lc0_profiles = [row for row in catalog.get("profiles", []) if row.get("profile_id") == "lc0/specialist-engine-opt-v2"]
    require(len(lc0_profiles) == 1, "catalog LC0 canonical profile missing")
    options = {row.get("name"): row.get("value") for row in lc0_profiles[0].get("options", [])}
    require(options.get("MinibatchSize") == 4 and options.get("MaxPrefetch") == 0, "catalog LC0 b4 options drift")
    runtime_meta = (catalog.get("profile_runtime") or {}).get("lc0/specialist-engine-opt-v2") or {}
    require(runtime_meta.get("warmup") is None, "catalog LC0 b4 profile is not cold")

    policy = _load(repo_root / "qualification/engine-opt-v2-native-work-policy.json")
    require(policy.get("policy_id") == "lc0-node-stop-contract-v1", "canonical node-stop policy drift")
    require(policy.get("applies_to") == {
        "selection_profile_id": "engine-opt-v2-selection",
        "selection_status": "selected",
        "matrix_profile": "b4-p0-c256k-cold",
    }, "canonical node-stop policy applicability drift")
    require(policy.get("requested_nodes") == 16, "canonical node-stop request drift")
    require(policy.get("terminal_counter_semantics") == "lc0.uci_nodes", "canonical node-stop semantics drift")
    require(policy.get("require_exact_terminal_counter_repeatability") is False, "canonical node-stop policy re-enabled exact terminal counter repeatability")

    return {
        "base_sha": base_sha,
        "head_sha": head,
        "parent_sha": parent,
        "authorized_profile_change": True,
        "runtime_files_checked": changed_runtime,
        "guard_files_unchanged": unchanged_files,
        "engine_trees_unchanged": unchanged_trees,
        "runtime_profile_selection_enabled": False,
    }


def _domain_digest(report: dict[str, Any]) -> str | None:
    domain = ((report.get("details") or {}).get("execution_domain") or {})
    value = domain.get("execution_domain_digest")
    return value if isinstance(value, str) else None


def evaluate_promotion_gate(
    *,
    aggregate: dict[str, Any],
    host_binding: dict[str, Any],
    catalog: dict[str, Any],
    promotion_surface: dict[str, Any],
    current_head: str,
) -> dict[str, Any]:
    require(promotion_surface.get("authorized_profile_change") is True, "promotion surface is not authorized")
    require(aggregate.get("source_commit") == current_head, "canonical aggregate is not bound to the exact current head")
    require(aggregate.get("evidence_valid") is True, "canonical aggregate evidence invalid")
    require(not (aggregate.get("invalid_evidence") or []), "canonical aggregate contains invalid evidence")
    require(aggregate.get("profile_qualified") is True, "canonical b4 profile is not qualified")
    require(aggregate.get("passed") is True, "canonical b4 aggregate did not pass")
    require(aggregate.get("qualification_disposition") in ALLOWED_DISPOSITIONS, "canonical b4 disposition is not promotable")
    require(not (aggregate.get("qualification_failures") or []), "canonical b4 aggregate has qualification failures")
    lc0 = (aggregate.get("details") or {}).get("lc0") or {}
    require(lc0.get("selected_profile") == "b4-p0-c256k-cold", "aggregate did not qualify canonical b4")
    require(lc0.get("native_work_policy") == "lc0-node-stop-contract-v1", "aggregate did not use canonical node-stop policy")
    lifecycle = (aggregate.get("details") or {}).get("local1_g3") or {}
    require(lifecycle.get("validated_games") == 28, "canonical b4 LOCAL-1 lifecycle incomplete")
    require(lifecycle.get("g3_authority_qualified") is True, "canonical b4 lacks G3 non-anchor authority qualification")

    digest = _domain_digest(aggregate)
    require(isinstance(digest, str) and len(digest) == 64, "canonical aggregate execution-domain digest missing")

    prior_head = promotion_surface.get("parent_sha")
    require(
        isinstance(prior_head, str) and len(prior_head) == 40,
        "promotion surface does not identify the immediately preceding qualification head",
    )
    bound = host_binding.get("current_domain_bound_qualification") or {}
    require(
        bound.get("qualified_head") == prior_head,
        "immediately preceding promotion head has not been frozen into host binding",
    )
    require(bound.get("qualification_disposition") in ALLOWED_DISPOSITIONS, "promotion host binding is not qualified")

    snapshot = catalog.get("qualification_snapshot") or {}
    require(
        snapshot.get("qualified_head") == prior_head,
        "immediately preceding promotion head has not been frozen into catalog qualification snapshot",
    )
    require(snapshot.get("qualification_disposition") in ALLOWED_DISPOSITIONS, "catalog promotion snapshot is not qualified")
    require(
        snapshot.get("execution_domain_digest") == bound.get("execution_domain_digest"),
        "catalog promotion snapshot differs from the frozen promotion-head domain",
    )

    seed = host_binding.get("b4_candidate_promotion_seed") or {}
    require(seed.get("canonical_promotion_complete") is True, "promotion seed has not been closed by exact-head requalification")

    return {
        "schema_version": 1,
        "passed": True,
        "disposition": "CANONICAL_B4_PROMOTION_QUALIFIED",
        "source_commit": current_head,
        "execution_domain_digest": digest,
        "promotion_surface": promotion_surface,
        "claim_boundary": {
            "canonical_b4_promotion": True,
            "runtime_profile_selection": False,
            "adaptive_stop_promoted": False,
            "generic_host_portability": aggregate.get("qualification_disposition") == "QUALIFIED_REUSABLE_DOMAIN",
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }
