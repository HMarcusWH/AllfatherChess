"""Shared qualification logic for canonical and candidate LC0 runtime matrices."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from tools.engine_opt.domain import (
    require_same_execution_domain,
    validate_execution_domain,
)
from tools.engine_opt.selection import validate_selected_lc0_rows

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_NATIVE_WORK_POLICY_PATH = (
    ROOT / "qualification/engine-opt-v2-candidate-native-work-policy.json"
)


class MatrixQualificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixQualificationError(message)


def verify_report_seal(doc: dict[str, Any], label: str = "LC0 matrix") -> None:
    observed = doc.get("content_sha256")
    require(isinstance(observed, str) and len(observed) == 64, f"{label}: content seal missing")
    core = dict(doc)
    core.pop("content_sha256", None)
    encoded = json.dumps(
        core,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    require(hashlib.sha256(encoded).hexdigest() == observed, f"{label}: content seal mismatch")


def repeat_evidence(
    matrix: dict[str, Any],
    profile: str,
    repeats: int,
    cases: int,
) -> dict[str, Any]:
    rows = (matrix.get("repeat_summaries") or {}).get(profile) or []
    require(len(rows) == repeats, f"{profile}: expected {repeats} repeats, found {len(rows)}")
    bestmoves: list[tuple[str, ...]] = []
    native_work: list[tuple[Any, ...]] = []
    for index, row in enumerate(rows):
        require(row.get("cases") == cases, f"{profile} repeat {index}: incomplete corpus")
        require(row.get("completion_rate") == 1.0, f"{profile} repeat {index}: incomplete search")
        best = tuple(row.get("bestmoves") or [])
        native = tuple(row.get("native_work_values") or [])
        require(len(best) == cases, f"{profile} repeat {index}: malformed bestmove vector")
        require(len(native) == cases, f"{profile} repeat {index}: malformed native-work vector")
        require(
            all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and float(value) >= 0
                for value in native
            ),
            f"{profile} repeat {index}: invalid native-work value",
        )
        bestmoves.append(best)
        native_work.append(native)
    return {
        "rows": rows,
        "bestmove_vectors": [list(row) for row in bestmoves],
        "native_work_vectors": [list(row) for row in native_work],
        "bestmove_stable": len(set(bestmoves)) == 1,
        "native_work_stable": len(set(native_work)) == 1,
        "bestmove_vector": list(bestmoves[0]),
        "native_work_vector": list(native_work[0]),
    }


def _load_candidate_native_work_policy(
    selection: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if selection.get("status") != "qualification_candidate":
        return None, None
    try:
        raw = CANDIDATE_NATIVE_WORK_POLICY_PATH.read_bytes()
        policy = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MatrixQualificationError(
            f"cannot load candidate native-work policy: {exc}"
        ) from exc
    require(isinstance(policy, dict), "candidate native-work policy root must be an object")
    applies = policy.get("applies_to")
    expected = {
        "selection_profile_id": selection.get("profile_id"),
        "selection_status": selection.get("status"),
        "matrix_profile": ((selection.get("selected") or {}).get("lc0") or {}).get("matrix_profile"),
    }
    require(applies == expected, "candidate native-work policy applicability drift")
    return policy, hashlib.sha256(raw).hexdigest()


def _selected_native_work_policy(policy: dict[str, Any] | None) -> tuple[str, bool]:
    if policy is None:
        return "exact-vector-v1", True
    require(isinstance(policy, dict), "native-work policy must be an object")
    require(policy.get("schema_version") == 1, "native-work policy schema drift")
    policy_id = policy.get("policy_id")
    require(
        policy_id in {"exact-vector-v1", "lc0-node-stop-contract-v1"},
        f"unsupported LC0 native-work policy: {policy_id!r}",
    )
    exact = policy.get("require_exact_terminal_counter_repeatability")
    if policy_id == "exact-vector-v1":
        require(exact in {None, True}, "exact-vector-v1 must require exact terminal-counter repeatability")
        return str(policy_id), True
    require(
        exact is False,
        "lc0-node-stop-contract-v1 must explicitly disable exact terminal-counter repeatability",
    )
    return str(policy_id), False


def qualify_lc0_matrix(
    *,
    matrix: dict[str, Any],
    selection: dict[str, Any],
    expected_source_commit: str,
    expected_execution_domain: dict[str, Any],
    candidate_bundle: dict[str, Any],
    native_work_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verify_report_seal(matrix)
    require(
        (matrix.get("source") or {}).get("commit") == expected_source_commit,
        "LC0 matrix source is not exact head",
    )
    require(not matrix.get("errors"), "LC0 matrix contains failed cases")

    domain = validate_execution_domain(
        matrix.get("execution_domain"),
        expected_source_commit=expected_source_commit,
    )
    require_same_execution_domain(
        {"profile_domain": expected_execution_domain, "lc0_matrix": domain},
        expected_source_commit=expected_source_commit,
    )

    require(
        (matrix.get("binary") or {}).get("sha256")
        == candidate_bundle["artifacts"]["engines"]["lc0"]["sha256"],
        "LC0 matrix binary differs from candidate bundle",
    )
    require(
        (matrix.get("weights") or {}).get("sha256")
        == candidate_bundle["artifacts"]["networks"]["lc0"]["sha256"],
        "LC0 matrix network differs from candidate bundle",
    )

    lc0 = ((selection.get("selected") or {}).get("lc0") or {})
    q = selection.get("qualification") or {}
    selected_profile = str(lc0.get("matrix_profile") or "")
    baseline_profile = str(q.get("baseline_profile") or "v1-current-cold")
    repeats = q.get("confirmation_repeats")
    cases = q.get("corpus_cases", 8)
    require(
        isinstance(repeats, int) and not isinstance(repeats, bool) and repeats >= 2,
        "selection confirmation_repeats is invalid",
    )
    require(
        isinstance(cases, int) and not isinstance(cases, bool) and cases >= 1,
        "selection corpus_cases is invalid",
    )

    policy_sha256: str | None = None
    if native_work_policy is None:
        native_work_policy, policy_sha256 = _load_candidate_native_work_policy(selection)
    native_work_policy_id, selected_native_work_exact = _selected_native_work_policy(native_work_policy)

    confirmation = matrix.get("confirmation") or {}
    require(confirmation.get("selected_profile") == selected_profile, "matrix selected profile drift")
    require(confirmation.get("baseline_profile") == baseline_profile, "matrix baseline profile drift")
    require(confirmation.get("repeats") == repeats, "matrix confirmation repeat count drift")

    baseline = repeat_evidence(matrix, baseline_profile, repeats, cases)
    selected = repeat_evidence(matrix, selected_profile, repeats, cases)
    failures: list[dict[str, str]] = []

    def fail(code: str, message: str) -> None:
        failures.append({"code": code, "message": message})

    if not baseline["bestmove_stable"] or not selected["bestmove_stable"]:
        fail(
            "NOT_QUALIFIED_REPEATABILITY",
            "LC0 bestmove vectors are not repeatable within the bound domain",
        )
    if not baseline["native_work_stable"]:
        fail(
            "NOT_QUALIFIED_REPEATABILITY",
            "LC0 baseline native-work vectors are not repeatable within the bound domain",
        )
    if selected_native_work_exact and not selected["native_work_stable"]:
        fail(
            "NOT_QUALIFIED_REPEATABILITY",
            "LC0 selected native-work vectors are not repeatable within the bound domain",
        )
    if selected["bestmove_vector"] != baseline["bestmove_vector"]:
        fail(
            "NOT_QUALIFIED_BEHAVIORAL_EQUIVALENCE",
            "selected LC0 profile changed the frozen baseline bestmove vector",
        )

    baseline_rows = baseline["rows"]
    selected_rows = selected["rows"]
    worst_selected = max(float(row["median_wall_ms"]) for row in selected_rows)
    fastest_baseline = min(float(row["median_wall_ms"]) for row in baseline_rows)
    ratio = worst_selected / fastest_baseline
    observed_max = max(float(row["max_wall_ms"]) for row in selected_rows)
    if ratio > float(q.get("max_median_wall_ratio", 0.40)):
        fail(
            "NOT_QUALIFIED_RESOURCE_BOUND",
            "selected LC0 profile lost the frozen median-wall advantage",
        )
    if observed_max > float(q.get("max_wall_ms", 600.0)):
        fail(
            "NOT_QUALIFIED_RESOURCE_BOUND",
            "selected LC0 profile exceeded the frozen n16 wall bound",
        )

    raw = [row for row in matrix.get("rows", []) if row.get("profile") == selected_profile]
    validate_selected_lc0_rows(
        raw,
        lc0,
        require,
        qualification=q,
        native_work_policy=native_work_policy,
    )

    details = {
        "execution_domain": domain,
        "baseline_profile": baseline_profile,
        "selected_profile": selected_profile,
        "confirmation_repeats": repeats,
        "native_work_policy": native_work_policy_id,
        "native_work_policy_sha256": policy_sha256,
        "selected_native_work_exact_repeatability_required": selected_native_work_exact,
        "baseline": {
            "bestmove_stable": baseline["bestmove_stable"],
            "native_work_stable": baseline["native_work_stable"],
            "bestmove_vectors": baseline["bestmove_vectors"],
            "native_work_vectors": baseline["native_work_vectors"],
        },
        "selected": {
            "bestmove_stable": selected["bestmove_stable"],
            "native_work_stable": selected["native_work_stable"],
            "bestmove_vectors": selected["bestmove_vectors"],
            "native_work_vectors": selected["native_work_vectors"],
        },
        "wall_ratio_worst_selected_over_fastest_baseline": ratio,
        "selected_max_wall_ms": observed_max,
        "selected_max_cpu_ms": max(
            float(row.get("max_cpu_ms") or 0.0) for row in selected_rows
        ),
    }
    return {
        "evidence_valid": True,
        "qualified": not failures,
        "qualification_failures": failures,
        "details": details,
    }
