"""ENGINE-OPT selected-profile row contracts shared by qualification and tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def validate_selected_lc0_rows(
    rows: list[dict[str, Any]],
    selected: dict[str, Any],
    require: Callable[[bool, str], None],
) -> None:
    require(bool(rows), "selected LC0 profile has no raw rows")
    warmup_nodes = selected.get("warmup_nodes")
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
