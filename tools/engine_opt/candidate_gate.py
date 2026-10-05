"""Final ENGINE-OPT-V2 candidate gate with explicit canonical host semantics.

The canonical profile remains authoritative only under its existing claim boundary.
A canonical matrix failure on the exact bound host (or after generic portability is
established) blocks the gate. On an unmatched host, a structurally valid failure
may be retained as a diagnostic only when the canonical profile surface is
unchanged from the PR base and the failure is limited to the already-documented
execution-sensitive LC0 diagnostics.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class CandidateGateError(ValueError):
    pass


ALLOWED_CANDIDATE_DISPOSITIONS = {
    "QUALIFIED_EXACT_HOST_ONLY",
    "QUALIFIED_REUSABLE_DOMAIN",
}

HOST_SENSITIVE_CANONICAL_FAILURES = {
    "NOT_QUALIFIED_REPEATABILITY",
    "NOT_QUALIFIED_BEHAVIORAL_EQUIVALENCE",
}

# Files/trees that define the canonical ENGINE-OPT-V2 engine/profile surface.
# PR-local controller/resource accounting work is intentionally outside this
# surface; mutating any item below makes an unmatched-host canonical failure
# blocking again.
CANONICAL_FILES = (
    "config/allfather.online-engine-opt-v2.json",
    "qualification/engine-opt-v2-selection.json",
    "qualification/engine-opt-v2-evidence.json",
    "qualification/engine-opt-v2-host-binding.json",
    "qualification/online-engine-opt-v2.json",
    "qualification/engine-derived-lock.json",
    "qualification/lc0-strength.lock.json",
    "qualification/lc0-strength-profile.json",
    "vendor.lock.json",
    "scripts/build-online-engine-opt-v2.sh",
    "scripts/build-lc0-strength.sh",
    "scripts/lc0-strength-lock.py",
)

CANONICAL_TREES = (
    "engines/stockfish",
    "engines/reckless",
    "engines/lc0",
)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise CandidateGateError(message)


def _git(repo_root: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(
        ["git", "-C", str(repo_root), *args],
        text=text,
    ).strip()


def compare_canonical_surface(repo_root: Path, base_sha: str) -> dict[str, Any]:
    """Compare the canonical engine/profile surface against the PR base."""
    require(isinstance(base_sha, str) and len(base_sha) == 40, "PR base SHA missing")
    current_head = str(_git(repo_root, "rev-parse", "HEAD"))
    changed_files: list[str] = []
    changed_trees: list[str] = []

    for rel in CANONICAL_FILES:
        current = (repo_root / rel).read_bytes()
        try:
            base = _git(repo_root, "show", f"{base_sha}:{rel}", text=False)
        except subprocess.CalledProcessError as exc:
            raise CandidateGateError(f"canonical base file unavailable: {rel}") from exc
        if current != base:
            changed_files.append(rel)

    for rel in CANONICAL_TREES:
        try:
            base_tree = str(_git(repo_root, "rev-parse", f"{base_sha}:{rel}"))
            current_tree = str(_git(repo_root, "rev-parse", f"HEAD:{rel}"))
        except subprocess.CalledProcessError as exc:
            raise CandidateGateError(f"canonical engine tree unavailable: {rel}") from exc
        if current_tree != base_tree:
            changed_trees.append(rel)

    return {
        "base_sha": base_sha,
        "head_sha": current_head,
        "unchanged": not changed_files and not changed_trees,
        "changed_files": changed_files,
        "changed_trees": changed_trees,
        "files_checked": list(CANONICAL_FILES),
        "trees_checked": list(CANONICAL_TREES),
    }


def _domain_digest(report: dict[str, Any]) -> str | None:
    details = report.get("details") or {}
    domain = details.get("execution_domain") or {}
    digest = domain.get("execution_domain_digest")
    return digest if isinstance(digest, str) else None


def evaluate_gate(
    *,
    canonical: dict[str, Any],
    candidate: dict[str, Any],
    host_binding: dict[str, Any],
    canonical_surface: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate the final candidate gate without upgrading a diagnostic to qualification."""
    require(canonical.get("evidence_valid") is True, "canonical matrix evidence is invalid")
    require(not (canonical.get("invalid_evidence") or []), "canonical matrix has invalid evidence")

    candidate_ok = (
        candidate.get("evidence_valid") is True
        and not (candidate.get("invalid_evidence") or [])
        and candidate.get("candidate_qualified") is True
        and candidate.get("promotion_ready") is True
        and candidate.get("canonical_profile_changed") is False
        and candidate.get("passed") is True
        and candidate.get("qualification_disposition") in ALLOWED_CANDIDATE_DISPOSITIONS
        and not (candidate.get("qualification_failures") or [])
    )
    require(candidate_ok, "b4 candidate is not independently promotion-ready")

    current_digest = _domain_digest(canonical)
    candidate_digest = _domain_digest(candidate)
    require(current_digest is not None, "canonical execution-domain digest missing")
    require(candidate_digest == current_digest, "candidate/canonical execution domains differ")

    outer_binding = host_binding.get("host_binding") or {}
    bound = host_binding.get("current_domain_bound_qualification") or {}
    require(
        bound.get("qualification_disposition") == "QUALIFIED_EXACT_HOST_ONLY",
        "canonical bound qualification is not exact-host-only",
    )
    require(bound.get("binding_scope") == "exact_host_observation", "canonical binding scope drift")
    require(bound.get("generic_host_portability_established") is False, "canonical bound qualification claims portability")
    bound_digest = bound.get("execution_domain_digest")
    require(isinstance(bound_digest, str) and len(bound_digest) == 64, "canonical bound domain digest missing")

    canonical_qualified = (
        canonical.get("qualified") is True
        and canonical.get("passed") is True
        and not (canonical.get("qualification_failures") or [])
    )
    same_bound_host = current_digest == bound_digest

    if canonical_qualified:
        disposition = "QUALIFIED_CANONICAL_AND_CANDIDATE"
        canonical_status = "QUALIFIED_ON_CURRENT_HOST"
        canonical_authority_on_current_host = True
    else:
        failures = canonical.get("qualification_failures") or []
        codes = {
            row.get("code")
            for row in failures
            if isinstance(row, dict) and isinstance(row.get("code"), str)
        }
        require(failures and len(codes) == len(failures), "canonical diagnostic failures malformed")
        require(
            codes.issubset(HOST_SENSITIVE_CANONICAL_FAILURES),
            "canonical failure includes a non-host-diagnostic code",
        )
        require(not same_bound_host, "canonical failed on its exact bound host")
        require(
            outer_binding.get("generic_host_portability_established") is False,
            "canonical host binding claims portability",
        )
        require(
            outer_binding.get("selection_eligible_on_unmatched_host") is False,
            "canonical selection is unexpectedly eligible on unmatched hosts",
        )
        require(canonical_surface.get("unchanged") is True, "canonical engine/profile surface changed in this PR")
        disposition = "QUALIFIED_CANDIDATE_WITH_UNMATCHED_CANONICAL_DIAGNOSTIC"
        canonical_status = "UNMATCHED_HOST_DIAGNOSTIC_NOT_QUALIFIED"
        canonical_authority_on_current_host = False

    return {
        "schema_version": 1,
        "passed": True,
        "disposition": disposition,
        "candidate": {
            "qualified": True,
            "promotion_ready": True,
            "qualification_disposition": candidate.get("qualification_disposition"),
            "execution_domain_digest": candidate_digest,
        },
        "canonical": {
            "status": canonical_status,
            "qualified": canonical_qualified,
            "authority_on_current_host": canonical_authority_on_current_host,
            "current_execution_domain_digest": current_digest,
            "bound_execution_domain_digest": bound_digest,
            "same_bound_host": same_bound_host,
            "qualification_failures": canonical.get("qualification_failures") or [],
        },
        "canonical_surface": canonical_surface,
        "claim_boundary": {
            "canonical_b4_promotion": False,
            "canonical_unmatched_host_requalified": False,
            "generic_host_portability_established": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
    }
