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


def observe_replay_backend(run: Path) -> dict:
    """Derive backend vitality and VERIFY/proposal coverage from sealed replays."""
    manifest = load(run / "manifest.json")
    final = load(run / "decision/final.json")["decision"]
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
        "verify_triples_completed": int(all(
            (owner, "VERIFY") in complete for owner in
            ("stockfish", "reckless", "lc0"))),
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
