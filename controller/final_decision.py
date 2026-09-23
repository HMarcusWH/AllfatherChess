"""Persistence for the actual outward M14-C decision.

The live decision is selected entirely in memory on the anchor completion path.
This module runs only after stdout emission and seals a hash-bound description
of which authority selected the move. It never participates in authorization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from controller.decision import FinalDecision, canonical_digest
from controller.replay import sha256_file


FINAL_DECISION_SCHEMA_VERSION = 1


class FinalDecisionError(RuntimeError):
    """Raised when final-decision evidence cannot be sealed or verified."""


_SOURCE_PATHS = (
    "manifest.json",
    "verification/manifest.json",
    "crossfeed/manifest.json",
    "decision/counterfactual.json",
    "route.json",
    "resource.json",
)


def _source_hashes(run_dir: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for relative in _SOURCE_PATHS:
        path = run_dir / relative
        if path.is_file():
            sources[relative] = sha256_file(path)
    return sources


def seal_final_decision_artifact(
    decision: FinalDecision,
    run_dir: Path | str,
) -> dict[str, Any]:
    """Seal the actual selected authority after the source artifacts finalize."""

    run_dir = Path(run_dir)
    core = {
        "schema_version": FINAL_DECISION_SCHEMA_VERSION,
        "decision": decision.as_dict(),
        "sources": _source_hashes(run_dir),
        "semantics": {
            "authorization_timing": "bounded in-memory at anchor completion",
            "terminal_resource_timing": "post-output; may qualify claims but cannot rewrite the played move",
        },
    }
    digest = canonical_digest(core)
    artifact = {
        **core,
        "decision_id": f"final-v1:{digest[:16]}",
        "content_sha256": digest,
    }
    target_dir = run_dir / "decision"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "final.json"
    target.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def load_final_decision_artifact(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "decision" / "final.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalDecisionError(f"cannot load final decision artifact {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FinalDecisionError("final decision artifact root must be an object")
    if data.get("schema_version") != FINAL_DECISION_SCHEMA_VERSION:
        raise FinalDecisionError(
            f"unsupported final decision schema_version: {data.get('schema_version')!r}"
        )
    return data


def verify_final_decision_integrity(run_dir: Path | str) -> list[str]:
    """Check self-digest and the source hashes captured after output."""

    run_dir = Path(run_dir)
    try:
        artifact = load_final_decision_artifact(run_dir)
    except FinalDecisionError as exc:
        return [str(exc)]

    problems: list[str] = []
    core = {
        "schema_version": artifact.get("schema_version"),
        "decision": artifact.get("decision"),
        "sources": artifact.get("sources"),
        "semantics": artifact.get("semantics"),
    }
    expected = canonical_digest(core)
    if artifact.get("content_sha256") != expected:
        problems.append("final decision content digest mismatch")

    stored_sources = artifact.get("sources")
    if not isinstance(stored_sources, dict):
        problems.append("final decision sources must be an object")
        return problems
    for relative, stored_hash in stored_sources.items():
        path = run_dir / str(relative)
        if not path.is_file():
            problems.append(f"final decision source missing: {relative}")
            continue
        if sha256_file(path) != stored_hash:
            problems.append(f"final decision source hash mismatch: {relative}")
    return problems
