"""ENGINE-OPT selected-profile row contracts shared by qualification and tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def validate_selected_lc0_rows(
    rows: list[dict[str, Any]],
    selected: dict[str, Any],
    qualification: dict[str, Any],
    require: Callable[[bool, str], None],
) -> None:
    require(bool(rows), "selected LC0 profile has no raw rows")
    warmup_nodes = selected.get("warmup_nodes")
    repeats = qualification.get("confirmation_repeats")
    cases = qualification.get("corpus_cases", 8)
    if isinstance(repeats, int) and not isinstance(repeats, bool) and repeats > 0:
        require(
            isinstance(cases, int) and not isinstance(cases, bool) and cases > 0,
            "selected LC0 corpus case count is invalid",
        )
        require(
            len(rows) == repeats * cases,
            "selected LC0 raw-row coverage differs from the frozen repeat/corpus contract",
        )

    seen: set[tuple[int, str]] = set()
    for row in rows:
        opts = row.get("options") or {}
        require(opts.get("NNCacheSize") == selected.get("nn_cache_size"),
                "selected NNCacheSize differs from frozen selection")
        require(opts.get("MinibatchSize") == selected.get("minibatch_size"),
                "selected MinibatchSize differs from frozen selection")
        require(opts.get("MaxPrefetch") == selected.get("max_prefetch"),
                "selected MaxPrefetch differs from frozen selection")
        require(opts.get("AdaptivePrefetch") is selected.get("adaptive_prefetch"),
                "selected AdaptivePrefetch differs from frozen selection")
        warmup = row.get("warmup")
        if warmup_nodes is None:
            require(warmup is None,
                    "selected LC0 warmup is present despite a cold-profile selection")
        else:
            require(isinstance(warmup, dict) and warmup.get("nodes") == warmup_nodes,
                    "selected LC0 warmup differs from frozen selection")

        repeat_index = row.get("repeat_index")
        case_id = row.get("case_id")
        require(
            isinstance(repeat_index, int) and not isinstance(repeat_index, bool) and repeat_index >= 0,
            "selected LC0 raw row has invalid repeat identity",
        )
        require(isinstance(case_id, str) and case_id, "selected LC0 raw row has invalid case identity")
        identity = (repeat_index, case_id)
        require(identity not in seen, "selected LC0 raw rows contain a duplicate repeat/case identity")
        seen.add(identity)

    policy = qualification.get("native_work_policy")
    if policy is None:
        return
    require(isinstance(policy, dict), "qualification native_work_policy must be an object")
    policy_id = policy.get("id")
    require(
        policy_id in {"exact-vector-v1", "lc0-node-stop-contract-v1"},
        f"unsupported LC0 native-work policy: {policy_id!r}",
    )
    if policy_id == "exact-vector-v1":
        return

    requested_nodes = policy.get("requested_nodes")
    semantics = policy.get("terminal_counter_semantics")
    required_options = policy.get("required_options")
    require(
        isinstance(requested_nodes, int) and not isinstance(requested_nodes, bool) and requested_nodes > 0,
        "lc0-node-stop-contract-v1 requested_nodes must be a positive integer",
    )
    require(
        semantics == "lc0.uci_nodes",
        "lc0-node-stop-contract-v1 terminal counter semantics drift",
    )
    require(
        isinstance(required_options, dict) and required_options,
        "lc0-node-stop-contract-v1 required_options must be a non-empty object",
    )

    for row in rows:
        require(row.get("family") == "lc0", "lc0-node-stop-contract-v1 may only qualify LC0 rows")
        require(
            row.get("nodes_requested") == requested_nodes,
            "selected LC0 requested node count differs from the frozen node-stop contract",
        )
        opts = row.get("options") or {}
        for key, value in required_options.items():
            require(
                opts.get(key) == value,
                f"selected LC0 option {key} differs from the frozen node-stop contract",
            )
        metrics = row.get("metrics")
        require(isinstance(metrics, dict), "selected LC0 metrics are missing")
        require(
            metrics.get("native_work_semantics") == semantics,
            "selected LC0 terminal counter semantics differ from the frozen node-stop contract",
        )
        native_work = metrics.get("native_work_value")
        require(
            isinstance(native_work, int) and not isinstance(native_work, bool) and native_work >= 0,
            "selected LC0 terminal counter is invalid",
        )
        require(
            metrics.get("completed_before_deadline") is True,
            "selected LC0 search did not complete inside the frozen deadline",
        )
