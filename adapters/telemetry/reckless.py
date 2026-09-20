"""Reckless UCI telemetry v1 adapter."""

from __future__ import annotations

from .uci import BaseUciTelemetryAdapter


class RecklessTelemetryAdapter(BaseUciTelemetryAdapter):
    engine = "reckless"
    nodes_unit = "nodes"

    def score_semantics(self, kind: str) -> tuple[str, str]:
        if kind == "cp":
            return "cp", "reckless.uci_cp"
        if kind == "mate":
            return "mate", "reckless.uci_mate"
        raise ValueError(f"unsupported Reckless score kind: {kind}")
