"""Canonical b4's frozen 7+16 G3 qualification corpus; no runtime authority."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

B4_POLICY = "qualification/online-hybrid-v2-b4.json"
LEGACY_POLICY = "qualification/online-hybrid-v2.json"
CANDIDATE_POLICY = "qualification/online-hybrid-v2-candidate.json"
WITNESS_CORPUS = "qualification/online-hybrid-v2-candidate-witnesses.json"
CANONICAL_RUNTIME = "config/allfather.online-hybrid-v2.validation.json"
CANONICAL_REFERENCE = "config/allfather.online-engine-opt-v2.json"


class B4G3ContractError(ValueError):
    pass


def require(ok: bool, why: str) -> None:
    if not ok:
        raise B4G3ContractError(why)


def load_object(root: Path, path: str) -> dict[str, Any]:
    data = json.loads((root / path).read_text(encoding="utf-8"))
    require(isinstance(data, dict), f"{path}: expected object")
    return data


def file_hash(root: Path, path: str) -> str:
    return hashlib.sha256((root / path).read_bytes()).hexdigest()


def validate_b4_policy(root: Path) -> dict[str, Any]:
    """Mechanically bind the historic witness set to canonical b4.

    Only the runtime/reference paths and candidate-only claim metadata change.
    The seven baseline cases, discovery provenance, authority requirements,
    clock budgets, node limits and frozen sixteen-case set must be identical.
    """
    root = Path(root)
    policy = load_object(root, B4_POLICY)
    candidate = load_object(root, CANDIDATE_POLICY)
    legacy = load_object(root, LEGACY_POLICY)
    corpus = load_object(root, WITNESS_CORPUS)
    require(candidate.get("profile_id") == legacy.get("profile_id") == "online-hybrid-v2",
            "G3 source policies have incompatible identities")
    expected = copy.deepcopy(candidate)
    expected["runtime_config"] = CANONICAL_RUNTIME
    expected["reference_runtime"] = CANONICAL_REFERENCE
    expected["claim_boundary"] = copy.deepcopy(legacy["claim_boundary"])
    require(policy == expected, "canonical b4 G3 policy differs from frozen corpus transformation")
    cases = policy.get("positive_cases") or []
    original = [{key: value for key, value in c.items() if key != "source_set"}
                for c in cases if c.get("source_set") == "legacy_v1"]
    discovered = [c for c in cases if c.get("source_set") == "discovery_local1_v1"]
    archived_form = [{**c, "moves": " ".join(c["moves"])} for c in discovered]
    require(len(cases) == 23 and len(original) == 7 and len(discovered) == 16,
            "canonical b4 case count or source classification drift")
    require(original == legacy.get("positive_cases"),
            "canonical b4 changed frozen seven-case baseline")
    require(policy.get("positive_requirement") == legacy.get("positive_requirement"),
            "canonical b4 weakened G3 non-anchor witness requirement")
    meta = policy.get("candidate_witnesses") or {}
    require(meta.get("path") == WITNESS_CORPUS and meta.get("legacy_policy_path") == LEGACY_POLICY,
            "G3 corpus provenance path drift")
    require(meta.get("sha256") == file_hash(root, WITNESS_CORPUS),
            "G3 witness corpus bytes no longer match frozen policy")
    require(meta.get("discovery_source") == corpus.get("source"),
            "G3 historical discovery source changed")
    require(meta.get("selection_algorithm") == corpus.get("selection_algorithm"),
            "G3 deterministic witness selection changed")
    require(meta.get("corpus_id") == corpus.get("corpus_id"),
            "G3 corpus identity changed")
    require(meta.get("legacy_case_count") == 7 and meta.get("discovery_case_count") == 16,
            "G3 case count declaration changed")
    require(archived_form == corpus.get("cases"), "G3 witness cases differ from frozen archive")
    return policy


def require_positive_g3(report: dict[str, Any], *, policy_sha256: str) -> dict[str, Any]:
    """Require a real qualifying witness, not a diagnostic or negative report."""
    require(isinstance(report, dict), "G3 report must be an object")
    require(report.get("evidence_valid") is True and
            report.get("authority_qualified") is True and
            report.get("passed") is True, "canonical b4 G3 positive authority witness missing")
    require((report.get("contracts") or {}).get("policy_sha256") == policy_sha256,
            "G3 report is not bound to canonical b4 policy bytes")
    positive = report.get("positive_case")
    require(isinstance(positive, dict), "G3 positive witness record missing")
    rows = report.get("cases") or []
    require(isinstance(rows, list) and positive in rows and
            isinstance(positive.get("run_id"), str) and bool(positive["run_id"]),
            "G3 positive witness is not retained among tested cases")
    require(positive.get("authority") == "HYBRID" and
            positive.get("authorization_policy") == "clocked_staged_preanchor_v1" and
            positive.get("authorization_granted") is True and
            positive.get("terminal_source") == "staged_verification" and
            positive.get("route_action") == "BUY_STAGED_VERIFY" and
            positive.get("staged_complete") is True,
            "G3 positive witness lacks frozen staged HYBRID authorization")
    proposal = positive.get("proposal_move")
    require(isinstance(proposal, str) and bool(proposal) and
            proposal != positive.get("anchor_move") and
            proposal == positive.get("emitted_move"),
            "G3 positive witness is not a distinct emitted specialist move")
    require(positive.get("envelope_claimed") is True and
            positive.get("resource_qualified") is True and
            positive.get("route_resource_qualified") is True and
            (positive.get("clock_outcome") or {}).get("output_within_deadline") is True,
            "G3 positive witness did not qualify its physical/outward envelope")
    return positive
