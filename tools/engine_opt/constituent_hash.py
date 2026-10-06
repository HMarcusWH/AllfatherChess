"""Deterministic repeated constituent-hash qualification.

The selected hash must remain inside the frozen efficiency band on every
challenger comparison. Ten complete repeat blocks are the uncertainty unit.
Crossing the band is INCONCLUSIVE, never a pass.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "qualification/constituent-benchmark-v2.json"


class ConstituentHashError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ConstituentHashError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_policy(path: Path = POLICY) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    require(raw.get("schema_version") == 1, "unsupported constituent benchmark schema")
    require(raw.get("protocol_id") == "constituent-hash-v2", "constituent benchmark id drift")
    require(raw.get("hash_mb") == [16, 32, 64, 128, 256], "hash grid drift")
    require(raw.get("selected_hash_mb") == 16, "selected hash drift")
    require(raw.get("repeats") == 10, "repeat count drift")
    require(raw.get("efficiency_band") == 1.03, "efficiency band drift")
    require(raw.get("attempt_policy") == "single-pass-no-retry-v1", "retry policy drift")
    inference = raw.get("repeat_inference")
    require(
        inference
        == {
            "unit": "complete_repeat_block",
            "transform": "log(selected_wall_ms/challenger_wall_ms)",
            "per_repeat_statistic": "mean_across_8_paired_positions",
            "center": "mean_log_ratio",
            "interval": "student_t",
            "confidence": "90% two-sided; equivalent 95% one-sided bound at each edge",
            "degrees_of_freedom": 9,
            "critical_value": 1.833113,
            "back_transform": "exp",
        },
        "repeat inference drift",
    )
    return raw


def load_case_ids(policy: dict[str, Any]) -> list[str]:
    path = ROOT / str(policy["corpus_path"])
    require(sha256_file(path) == policy["corpus_sha256"], "constituent corpus digest drift")
    rows: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            case_id = line.split("|", 1)[0].strip()
            require(case_id and case_id not in rows, "duplicate/empty corpus case")
            rows.append(case_id)
    require(len(rows) == 8, "constituent corpus must contain eight cases")
    return rows


def schedule(case_ids: list[str], *, repeats: int = 10) -> list[dict[str, Any]]:
    hashes = [16, 32, 64, 128, 256]
    plan: list[dict[str, Any]] = []
    for repeat in range(repeats):
        rotated_cases = case_ids[repeat % len(case_ids):] + case_ids[:repeat % len(case_ids)]
        offset = repeat % len(hashes)
        hash_order = hashes[offset:] + hashes[:offset]
        if repeat >= len(hashes):
            hash_order = list(reversed(hash_order))
        for case_id in rotated_cases:
            for order_index, hash_mb in enumerate(hash_order):
                plan.append({
                    "repeat_index": repeat,
                    "case_id": case_id,
                    "hash_mb": hash_mb,
                    "order_index": order_index,
                    "attempt_ordinal": len(plan),
                    "attempt_index": 0,
                })
    return plan


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    number = float(value)
    require(math.isfinite(number), f"{label} must be finite")
    require(number > 0 if positive else number >= 0, f"{label} out of range")
    return number


def _quantile(values: list[float], q: float) -> float:
    require(values and 0 <= q <= 1, "invalid quantile")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return ordered[lo]
    fraction = position - lo
    return ordered[lo] * (1 - fraction) + ordered[hi] * fraction


def expected_options(family: str, hash_mb: int) -> dict[str, Any]:
    require(family in {"stockfish", "reckless"}, "unsupported hash family")
    options: dict[str, Any] = {
        "Threads": 1,
        "Hash": hash_mb,
        "MultiPV": 1,
        "UCI_Chess960": False,
    }
    if family == "reckless":
        options["Minimal"] = False
    return options


def qualify_hash_matrix(
    document: dict[str, Any],
    *,
    expected_source_commit: str,
    selected_hash_mb: int,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = load_policy() if policy is None else policy
    require(document.get("schema_version") == 2, "hash matrix must use schema v2")
    require(document.get("kind") == "engine-opt-hash-matrix-v2", "wrong hash matrix kind")
    family = document.get("family")
    require(family in {"stockfish", "reckless"}, "unsupported hash family")
    require((document.get("source") or {}).get("commit") == expected_source_commit, "hash matrix source is not exact head")
    require(document.get("errors") == [], "hash matrix contains execution errors")
    require(document.get("protocol") == {
        "protocol_id": policy["protocol_id"],
        "repeats": policy["repeats"],
        "hash_mb": policy["hash_mb"],
        "ordering": policy["ordering"],
        "attempt_policy": policy["attempt_policy"],
        "efficiency_band": policy["efficiency_band"],
        "repeat_inference": policy["repeat_inference"],
    }, "hash matrix protocol drift")
    require(document.get("nodes") == policy["nodes"][family], "hash matrix work target drift")
    require(selected_hash_mb == policy["selected_hash_mb"], "selected hash differs from frozen protocol")
    binary = document.get("binary")
    require(
        isinstance(binary, dict)
        and isinstance(binary.get("sha256"), str)
        and len(binary["sha256"]) == 64,
        "hash matrix binary identity missing",
    )

    case_ids = load_case_ids(policy)
    expected = schedule(case_ids, repeats=policy["repeats"])
    rows = document.get("rows")
    require(isinstance(rows, list) and len(rows) == len(expected), "hash matrix row coverage incomplete")

    observed: dict[tuple[int, str, int], dict[str, Any]] = {}
    for row, slot in zip(rows, expected):
        require(isinstance(row, dict), "hash row must be an object")
        for key, value in slot.items():
            require(row.get(key) == value and type(row.get(key)) is type(value), f"hash row schedule drift: {key}")
        require(row.get("family") == family, "hash row family drift")
        require(row.get("binary_sha256") == binary["sha256"], "hash row binary identity mismatch")
        require(row.get("hash_mb") == slot["hash_mb"], "hash row hash drift")
        require(row.get("options") == expected_options(family, slot["hash_mb"]), "hash row options drift")
        require(row.get("nodes_requested") == policy["nodes"][family], "hash row nodes drift")
        metrics = row.get("metrics")
        require(isinstance(metrics, dict), "hash row metrics missing")
        wall = _number(metrics.get("wall_ms"), "wall_ms", positive=True)
        _number(metrics.get("cpu_ms"), "cpu_ms")
        work = metrics.get("native_work_value")
        require(type(work) is int and work >= policy["nodes"][family], "native work target not reached")
        bestmove = metrics.get("bestmove")
        require(isinstance(bestmove, str) and bestmove, "bestmove missing")
        transcript = row.get("transcript")
        require(isinstance(transcript, list), "hash row transcript missing")
        effective_hash = f">> setoption name Hash value {slot['hash_mb']}"
        require(sum(line == effective_hash for line in transcript) == 1, "hash row transcript option drift")
        require(sum(line == f">> go nodes {policy['nodes'][family]}" for line in transcript) == 1, "hash row go command drift")
        key = (slot["repeat_index"], slot["case_id"], slot["hash_mb"])
        require(key not in observed, "duplicate hash row")
        observed[key] = {"wall_ms": wall, "bestmove": bestmove, "native_work_value": work}

    behavioral: list[str] = []
    for case_id in case_ids:
        moves = {
            observed[(repeat, case_id, hash_mb)]["bestmove"]
            for repeat in range(policy["repeats"])
            for hash_mb in policy["hash_mb"]
        }
        if len(moves) != 1:
            behavioral.append(f"{case_id}: cross-hash bestmove disagreement")
        for hash_mb in policy["hash_mb"]:
            work = {
                observed[(repeat, case_id, hash_mb)]["native_work_value"]
                for repeat in range(policy["repeats"])
            }
            if len(work) != 1:
                behavioral.append(f"{case_id}/hash{hash_mb}: native work not repeatable")

    contrasts: dict[str, Any] = {}
    inference = policy["repeat_inference"]
    critical = float(inference["critical_value"])
    band = float(policy["efficiency_band"])
    for challenger in policy["hash_mb"]:
        if challenger == selected_hash_mb:
            continue
        repeat_logs: list[float] = []
        for repeat in range(policy["repeats"]):
            log_ratios = [
                math.log(
                    observed[(repeat, case_id, selected_hash_mb)]["wall_ms"]
                    / observed[(repeat, case_id, challenger)]["wall_ms"]
                )
                for case_id in case_ids
            ]
            repeat_logs.append(statistics.mean(log_ratios))
        mean_log = statistics.mean(repeat_logs)
        stdev = statistics.stdev(repeat_logs)
        stderr = stdev / math.sqrt(len(repeat_logs))
        lower = math.exp(mean_log - critical * stderr)
        upper = math.exp(mean_log + critical * stderr)
        center = math.exp(mean_log)
        repeat_ratios = [math.exp(value) for value in repeat_logs]
        if upper <= band:
            disposition = "QUALIFIED"
        elif lower > band:
            disposition = "NOT_QUALIFIED"
        else:
            disposition = "INCONCLUSIVE"
        contrasts[str(challenger)] = {
            "ratio": center,
            "lower": lower,
            "upper": upper,
            "repeat_ratios": repeat_ratios,
            "disposition": disposition,
        }

    dispositions = {item["disposition"] for item in contrasts.values()}
    if behavioral:
        disposition = "NOT_QUALIFIED_BEHAVIOR"
    elif "NOT_QUALIFIED" in dispositions:
        disposition = "NOT_QUALIFIED"
    elif "INCONCLUSIVE" in dispositions:
        disposition = "INCONCLUSIVE"
    else:
        disposition = "QUALIFIED"
    return {
        "qualified": disposition == "QUALIFIED",
        "disposition": disposition,
        "selected_hash_mb": selected_hash_mb,
        "efficiency_band": band,
        "contrasts": contrasts,
        "behavioral_failures": behavioral,
    }
