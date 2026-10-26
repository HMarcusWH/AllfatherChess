"""Read-only exploratory backend participation and source hygiene evidence.

This module must not change move selection, dynamic search budgets or
the strict source_identity() contract used by LOCAL-1/META-1.
"""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import stat
import subprocess

from tools.local_game.common import ROOT, load, require, sha


def source_hygiene(root: Path = ROOT) -> dict:
    """Describe unexpected files by path/size/hash, never copy unknown bytes."""
    root = root.resolve()
    names: set[str] = set()
    for args in (
        ("ls-files", "--others", "--exclude-standard", "-z"),
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z",
         "--", "tests/fixtures/local_full_game"),
    ):
        raw = subprocess.check_output(["git", "-C", str(root), *args])
        names.update(os.fsdecode(n) for n in raw.split(b"\0") if n)
    entries = []
    for relative in sorted(names)[:64]:
        path = root / relative
        require(path.resolve().is_relative_to(root),
                "pilot source hygiene path escapes checkout")
        try:
            info = path.lstat()
        except OSError:
            entries.append({"path": relative, "status": "vanished"})
            continue
        record = {"path": relative, "size": info.st_size}
        if stat.S_ISREG(info.st_mode) and info.st_size <= 1024*1024:
            record["sha256"] = sha(path)
            record["status"] = "bounded_regular_hash_only"
        else:
            record["status"] = "special_or_oversized_not_read"
        entries.append(record)
    return {
        "schema_version": 1,
        "unexpected_count": len(names),
        "entries": entries,
        "truncated": len(names) > 64,
        "raw_contents_retained": False,
    }


def require_clean_hygiene(observation: dict) -> None:
    require(type(observation) is dict
            and observation.get("schema_version") == 1
            and observation.get("unexpected_count") == 0
            and observation.get("entries") == []
            and observation.get("truncated") is False
            and observation.get("raw_contents_retained") is False,
            "untracked repository source detected; campaign not qualified")



def _nested_verification(run: Path, final_record: dict, kind: str) -> dict:
    """Count only independently verified nested terminal evidence.

    Absent means NOT ATTEMPTED only if the final decision also denies the
    artifact's existence. A missing declared source, orphan staged manifest,
    bad hash, or malformed owner triple is a qualification failure.
    """
    from controller.verification import (
        load_verification_manifest, verify_verification_integrity,
    )
    from controller.staged_verification import (
        load_staged_verification_manifest,
        verify_staged_verification_integrity,
    )
    require(kind in ("verification", "staged_verification"),
            "unknown pilot nested evidence type")
    declared_sources = final_record.get("sources")
    require(type(declared_sources) is dict,
            "final decision source index is unavailable")
    relative = f"{kind}/manifest.json"
    declared = relative in declared_sources
    child = run / relative
    present = child.is_file() and not child.is_symlink()
    require(declared == present,
            f"{kind} manifest presence differs from sealed final sources")
    require(not (child.parent.exists() and not child.parent.is_dir()),
            f"{kind} evidence directory is not a regular directory")
    if not declared:
        require(not child.parent.exists(),
                f"{kind} orphan evidence directory present without final source link")
        return {"attempted": 0, "complete": 0, "incomplete": 0,
                "completed_owner_stages": 0, "state": "NOT_ATTEMPTED"}
    require(type(declared_sources[relative]) is str
            and len(declared_sources[relative]) == 64
            and declared_sources[relative] == sha(child),
            f"{kind} final decision hash differs from nested manifest")
    integrity = (verify_verification_integrity(run) if kind == "verification"
                 else verify_staged_verification_integrity(run))
    require(integrity == [], f"{kind} source integrity failure: {integrity[:4]}")
    manifest = (load_verification_manifest(run) if kind == "verification"
                else load_staged_verification_manifest(run))
    state = (manifest.get("disposition") or {}).get("run")
    require(state in ("completed", "incomplete", "stopped", "failed"),
            f"{kind} has unknown disposition")
    stages = manifest.get("stages")
    require(type(stages) is list and len(stages) <= 3,
            f"{kind} contains invalid stage array")
    owners = ("stockfish", "reckless", "lc0")
    seen = set()
    completed_owner_stages = 0
    for stage in stages:
        require(type(stage) is dict, f"{kind} stage must be an object")
        owner = stage.get("owner")
        require(owner in owners and owner not in seen
                and stage.get("family") == owner,
                f"{kind} stage owner/family invalid or duplicated")
        seen.add(owner)
        if stage.get("disposition") == "completed":
            require(stage.get("failure") is None
                    and type(stage.get("bestmove")) is str,
                    f"{kind} completed stage has missing/invalid terminal evidence")
            completed_owner_stages += 1
    is_complete = (state == "completed" and len(stages) == 3
                   and seen == set(owners)
                   and completed_owner_stages == 3)
    require(state != "completed" or is_complete,
            f"{kind} claims completed without three healthy owner terminals")
    return {"attempted": 1, "complete": int(is_complete),
            "incomplete": int(not is_complete),
            "completed_owner_stages": completed_owner_stages,
            "state": state}



def observe_replay_backend(run: Path) -> dict:
    """Derive backend vitality and VERIFY/proposal coverage from sealed replays."""
    manifest = load(run / "manifest.json")
    final_record = load(run / "decision/final.json")
    final = final_record["decision"]
    health = manifest.get("shadow_health")
    require(type(health) is dict, "G3 replay has no shadow-health evidence")
    failures = []
    for name in ("stockfish-shadow", "reckless-shadow", "lc0-shadow"):
        state = health.get(name)
        if not isinstance(state, dict) or state.get("alive") is not True or state.get("failure") is not None:
            failures.append({
                "replay_id": run.name,
                "instance": name,
                "failure": state.get("failure") or "missing/not alive"
                    if isinstance(state, dict) else "missing health",
            })
    stages = manifest.get("stages")
    require(type(stages) is list, "G3 replay missing executed stage records")
    verification = _nested_verification(run, final_record, "verification")
    staged = _nested_verification(run, final_record, "staged_verification")
    require(not staged["attempted"] or verification["complete"],
            "staged VERIFY exists without completed base VERIFY")
    complete = {(row.get("owner"), row.get("phase"))
                for row in stages
                if type(row) is dict and row.get("disposition") == "completed"
                and row.get("failure") is None}
    authorization = final.get("authorization") or {}
    snapshot = final.get("authorization_snapshot") or {}
    frozen = bool(final.get("proposal_move")
                  and final.get("proposal_evidence_digest")
                  and snapshot.get("authority_evidence_frozen_before_soft_deadline") is True)
    return {
        "backend_failures": failures,
        "lc0_explore_stages_completed": int(("lc0", "EXPLORE") in complete),
        "three_family_explore_completed": int(all(
            (owner, "EXPLORE") in complete for owner in
            ("stockfish", "reckless", "lc0"))),
        # VERIFY/STAGED_VERIFY live under nested source-bound manifests;
        # the parent EXPLORE stage array cannot measure either.
        "verify_triples_completed": verification["complete"],
        "verify_attempted": verification["attempted"],
        "verify_incomplete": verification["incomplete"],
        "verify_owner_stage_completions": verification["completed_owner_stages"],
        "staged_verify_triples_completed": staged["complete"],
        "staged_verify_attempted": staged["attempted"],
        "staged_verify_incomplete": staged["incomplete"],
        "staged_verify_owner_stage_completions": staged["completed_owner_stages"],
        "proposals_frozen_before_deadline": int(frozen),
        "hybrid_authorizations": int(
            authorization.get("authorized") is True
            and final.get("authority") == "HYBRID"),
        "actual_anchor_changing_overrides": int(
            final.get("authority") == "HYBRID"
            and final.get("emitted_move") != final.get("anchor_move")),
        "authorization_denial": str(authorization.get("reason") or "")
            if authorization.get("authorized") is not True else None,
    }


def aggregate_backend_coverage(observations: list[dict]) -> dict:
    require(bool(observations), "G3 pilot has no replay backend observations")
    failures = [row for item in observations for row in item["backend_failures"]]
    counts = Counter(str(item["authorization_denial"]) for item in observations
                     if item["authorization_denial"] is not None)
    return {
        "g3_replays_inspected": len(observations),
        "lc0_explore_stages_observed": sum(
            row["lc0_explore_stages_completed"] for row in observations),
        "three_family_explore_completions": sum(
            row["three_family_explore_completed"] for row in observations),
        "verify_stages_completed": sum(
            row["verify_triples_completed"] for row in observations),
        "verify_attempted": sum(row["verify_attempted"] for row in observations),
        "verify_incomplete": sum(row["verify_incomplete"] for row in observations),
        "staged_verify_stages_completed": sum(
            row["staged_verify_triples_completed"] for row in observations),
        "staged_verify_attempted": sum(
            row["staged_verify_attempted"] for row in observations),
        "staged_verify_incomplete": sum(
            row["staged_verify_incomplete"] for row in observations),
        "verify_owner_stage_completions": sum(
            row["verify_owner_stage_completions"] for row in observations),
        "staged_verify_owner_stage_completions": sum(
            row["staged_verify_owner_stage_completions"] for row in observations),
        "proposals_frozen_before_deadline": sum(
            row["proposals_frozen_before_deadline"] for row in observations),
        "hybrid_authorizations": sum(
            row["hybrid_authorizations"] for row in observations),
        "anchor_changing_overrides": sum(
            row["actual_anchor_changing_overrides"] for row in observations),
        "backend_startup_failures": failures[:40],
        "backend_failure_count": len(failures),
        "three_family_evidence_complete": (
            not failures and
            any(row["three_family_explore_completed"] for row in observations)),
        "authorization_denial_reasons": dict(sorted(counts.items())),
    }
