"""Post-promotion canonical b4 continuity verification (not promotion authority)."""
from pathlib import Path
from tools.engine_opt.promotion_gate import (
    PromotionGateError, require, _git, _load, _base_json,
    verify_promotion_git_history, EXPECTED_LC0, RUNTIME_FILES,
    UNCHANGED_GUARD_FILES, ENGINE_TREES, ALLOWED_DISPOSITIONS,
    _assert_b4_runtime,
)
from tools.engine_opt.g3_b4 import B4_POLICY, file_hash, validate_b4_policy

B4 = "b4-p0-c256k-cold"
EXTRA_GUARDS = (
    "qualification/engine-opt-v2-evidence.json",
    "qualification/engine-opt-v2-host-binding.json",
    "qualification/engine-opt-v2-native-work-policy.json",
    "qualification/engine-opt-v2-candidate-selection.json",
    "qualification/resource-profile-catalog-v1.json",
    "qualification/online-hybrid-v2-b4.json",
    "qualification/online-hybrid-v2.json",
    "qualification/online-hybrid-v2-candidate.json",
    "qualification/online-hybrid-v2-candidate-witnesses.json",
)
PROTECTED = ("qualification/engine-opt-v2-selection.json", *RUNTIME_FILES,
             *UNCHANGED_GUARD_FILES, *EXTRA_GUARDS)


def compare_continuity_surface(repo_root: Path, base_sha: str) -> dict:
    root = Path(repo_root)
    ids = verify_promotion_git_history(root, base_sha)
    base = _base_json(root, base_sha, "qualification/engine-opt-v2-selection.json")
    head = _load(root / "qualification/engine-opt-v2-selection.json")
    for label, doc in (("base", base), ("HEAD", head)):
        lc0 = (doc.get("selected") or {}).get("lc0") or {}
        require(lc0.get("matrix_profile") == B4, f"{label}: canonical b4 absent")
        for name, expected in EXPECTED_LC0.items():
            require(lc0.get(name) == expected, f"{label}: {name} drift")

    for path in PROTECTED:
        old = _git(root, "show", f"{base_sha}:{path}", text=False)
        new = _git(root, "show", f"HEAD:{path}", text=False)
        require(isinstance(old, bytes) and isinstance(new, bytes),
                f"{path}: missing protected Git content")
        require(old == new, f"{path}: canonical guard changed")
        require((root / path).read_bytes() == new, f"{path}: dirty checkout")

    for path in RUNTIME_FILES:
        _assert_b4_runtime(_load(root / path), path)

    for path in ENGINE_TREES:
        require(_git(root, "rev-parse", f"{base_sha}:{path}") ==
                _git(root, "rev-parse", f"HEAD:{path}"),
                f"{path}: engine source changed")

    catalog = _load(root / "qualification/resource-profile-catalog-v1.json")
    require(catalog.get("selection_enabled") is False and
            catalog.get("fallback_profile") == "engine-opt-v2",
            "canonical catalog authority drift")
    lc0_profiles = [p for p in catalog.get("profiles", [])
                    if p.get("profile_id") == "lc0/specialist-engine-opt-v2"]
    require(len(lc0_profiles) == 1, "canonical LC0 catalog entry missing")
    opts = {r.get("name"): r.get("value") for r in lc0_profiles[0].get("options", [])}
    require(opts.get("MinibatchSize") == 4 and opts.get("MaxPrefetch") == 0,
            "canonical LC0 catalog options drift")
    require(((catalog.get("profile_runtime") or {}).get("lc0/specialist-engine-opt-v2") or {}).get("warmup") is None,
            "cold b4 warmup drift")
    try:
        validate_b4_policy(root)
    except (ValueError, OSError, KeyError) as exc:
        raise PromotionGateError(f"frozen G3 policy invalid: {exc}") from exc
    return {
        **ids, "authorized_profile_change": False,
        "canonical_b4_continuity_verified": True,
        "g3_b4_policy_sha256": file_hash(root, B4_POLICY),
        "guard_files_unchanged": list(PROTECTED),
        "engine_trees_unchanged": list(ENGINE_TREES),
    }


def evaluate_continuity_gate(*, aggregate: dict, surface: dict,
                             current_head: str) -> dict:
    require(surface.get("canonical_b4_continuity_verified") is True and
            surface.get("authorized_profile_change") is False,
            "continuity cannot grant promotion")
    require(surface.get("head_sha") == current_head and
            aggregate.get("source_commit") == current_head,
            "aggregate and guard must match exact HEAD")
    require(aggregate.get("passed") is True and
            aggregate.get("evidence_valid") is True and
            aggregate.get("profile_qualified") is True and
            not (aggregate.get("invalid_evidence") or []) and
            not (aggregate.get("qualification_failures") or []),
            "canonical aggregate evidence not qualified")
    disposition = aggregate.get("qualification_disposition")
    require(disposition in ALLOWED_DISPOSITIONS, "invalid aggregate disposition")
    details = aggregate.get("details") or {}
    lc0 = details.get("lc0") or {}
    require(lc0.get("selected_profile") == B4 and
            lc0.get("native_work_policy") == "lc0-node-stop-contract-v1",
            "canonical b4 LC0 qualification drift")
    g3 = details.get("local1_g3") or {}
    require(g3.get("validated_games") == 28 and
            g3.get("g3_authority_qualified") is True and
            g3.get("g3_policy_sha256") == surface.get("g3_b4_policy_sha256"),
            "frozen G3 / LOCAL-1 qualification drift")
    digest = (details.get("execution_domain") or {}).get("execution_domain_digest")
    require(isinstance(digest, str) and len(digest) == 64 and
            all(x in "0123456789abcdef" for x in digest.lower()),
            "missing exact execution-domain digest")
    return {
        "schema_version": 1, "passed": True,
        "disposition": "CANONICAL_B4_CONTINUITY_QUALIFIED",
        "source_commit": current_head, "execution_domain_digest": digest,
        "aggregate_disposition": disposition,
        "promotion_surface": surface,
        "claim_boundary": {
            "canonical_b4_continuity": True, "canonical_b4_promotion": False,
            "runtime_profile_selection": False, "adaptive_stop_promoted": False,
            "generic_host_portability": False, "strength": False, "elo": False,
            "equal_compute": False, "deployment": False,
        },
    }
