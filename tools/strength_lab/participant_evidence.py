"""S1 independent participant receipt/session join.

Rejects missing, altered, inconsistent, unmeasured and extra participant scopes.
Receipt values are OS observations from a producer; this validator checks
retention/consistency, not an independent remeasurement of ephemeral cgroups.
"""
from __future__ import annotations

import math
from pathlib import Path

from tools.local_game.common import load, require, sha

RECEIPT_FIELDS = frozenset({
    "schema_version", "accounting", "status", "scope_id", "arm",
    "session_id", "session_sha256", "proxy_pid", "proxy_identity",
    "proxy_returncode", "started_ns", "ended_ns",
    "observed_leak_before_cleanup", "errors", "cpu_ms",
    "cgroup_child_tree_cpu_ms", "launcher_self_cpu_ms",
    "cgroup_memory_peak_bytes", "launcher_peak_rss_kib",
    "participant_peak_memory_upper_bound_bytes", "peak_is_exact",
    "same_compute_claim", "strength_qualified", "measurement_boundary",
})


def _finite_nonnegative(value: object, name: str) -> float:
    require(type(value) in (int, float)
            and math.isfinite(value) and value >= 0, f"invalid {name}")
    return float(value)


def verify_resource_receipts(job: Path, arms: tuple[str, ...]) -> dict:
    """One genuine cgroup receipt per one genuine proxy session for each arm."""
    job = Path(job)
    require(job.is_dir() and not job.is_symlink(), "missing job directory")
    require(bool(arms) and len(set(arms)) == len(arms), "invalid resource arm set")
    result: dict[str, dict] = {}
    seen_ids: set[str] = set()
    for arm in arms:
        require(isinstance(arm, str) and arm and "/" not in arm
                and arm not in (".", ".."), "invalid resource arm identity")
        session_folder = job / "sessions" / arm
        receipt_folder = job / "participant_resources" / arm
        require(session_folder.is_dir() and not session_folder.is_symlink(),
                f"{arm}: missing session directory")
        require(receipt_folder.is_dir() and not receipt_folder.is_symlink(),
                f"{arm}: missing participant receipts")
        sessions = {}
        for item in session_folder.iterdir():
            require(item.is_dir() and not item.is_symlink(), "unexpected session entry")
            path = item / "session.json"
            require(path.is_file() and not path.is_symlink(), "missing sealed session")
            summary = load(path)
            require(summary.get("status") == "completed"
                    and summary.get("returncode") == 0
                    and summary.get("arm") == arm
                    and summary.get("session_id") == item.name
                    and not summary.get("errors")
                    and not summary.get("leaked_before_cleanup")
                    and not summary.get("remaining_after_cleanup"),
                    "invalid proxy session integrity")
            sessions[item.name] = sha(path)
        require(bool(sessions), f"{arm}: no completed participant session")
        resources: dict[str, dict] = {}
        for item in receipt_folder.iterdir():
            require(item.is_file() and not item.is_symlink() and item.suffix == ".json",
                    "unexpected participant receipt object")
            record = load(item)
            require(set(record) == RECEIPT_FIELDS
                    and record["schema_version"] == 1
                    and record["accounting"] ==
                        "strict_cgroup_v2_child_tree_plus_launcher"
                    and record["status"] == "MEASURED"
                    and record["arm"] == arm
                    and record["proxy_returncode"] == 0
                    and record["observed_leak_before_cleanup"] is False
                    and record["errors"] == []
                    and record["peak_is_exact"] is False
                    and record["same_compute_claim"] is False
                    and record["strength_qualified"] is False,
                    "participant receipt is not an unqualified valid measurement")
            ident = record["proxy_identity"]
            require(isinstance(ident, dict)
                    and type(record["proxy_pid"]) is int
                    and ident.get("pid") == record["proxy_pid"]
                    and type(ident.get("start_ticks")) is int
                    and ident["start_ticks"] > 0
                    and ident.get("pgid") == record["proxy_pid"],
                    "participant proxy PID/reuse identity inconsistent")
            require(type(record["started_ns"]) is int
                    and type(record["ended_ns"]) is int
                    and 0 < record["started_ns"] < record["ended_ns"],
                    "invalid participant receipt clock ordering")
            scope_id = record["scope_id"]
            require(isinstance(scope_id, str) and len(scope_id) == 32
                    and all(c in "0123456789abcdef" for c in scope_id)
                    and item.name == scope_id + ".json"
                    and scope_id not in seen_ids,
                    "invalid or duplicate participant scope identifier")
            seen_ids.add(scope_id)
            session = record["session_id"]
            require(session in sessions
                    and sessions[session] == record["session_sha256"]
                    and session not in resources,
                    "participant receipt/session occurrence join failed")
            subtotal = _finite_nonnegative(
                record["cgroup_child_tree_cpu_ms"], "cgroup CPU")
            own = _finite_nonnegative(
                record["launcher_self_cpu_ms"], "launcher CPU")
            total = _finite_nonnegative(record["cpu_ms"], "whole-participant CPU")
            require(subtotal > 0 and own > 0
                    and abs(total - (subtotal + own)) < 1e-6,
                    "incomplete or double-counted participant CPU")
            peak = _finite_nonnegative(
                record["cgroup_memory_peak_bytes"], "cgroup memory")
            launcher_peak = _finite_nonnegative(
                record["launcher_peak_rss_kib"], "launcher memory")
            bound = _finite_nonnegative(
                record["participant_peak_memory_upper_bound_bytes"], "memory bound")
            require(peak > 0 and launcher_peak > 0
                    and abs(bound - (peak + launcher_peak * 1024)) < 1e-6,
                    "participant memory bound is incomplete")
            require(record["measurement_boundary"] ==
                    "through post-proxy cleanup / pre-receipt seal",
                    "unspecified participant measurement boundary")
            resources[session] = {
                "scope_id": scope_id,
                "cpu_ms": total,
                "child_tree_cpu_ms": subtotal,
                "launcher_cpu_ms": own,
                "peak_memory_upper_bound_bytes": int(bound),
                "memory_peak_exact": False,
                "session_sha256": record["session_sha256"],
            }
        require(set(resources) == set(sessions), f"{arm}: resource coverage mismatch")
        result[arm] = {
            "sessions": len(sessions),
            "cpu_ms": sum(r["cpu_ms"] for r in resources.values()),
            "resources": resources,
        }
    return {
        "schema_version": 1,
        "status": "S1_PARTICIPANT_EVIDENCE_RECONSTRUCTED",
        "arms": result,
        "all_measured": True,
        "peak_exact": False,
        "equal_compute": False,
        "strength_qualified": False,
        "game_result_qualified": False,
    }
