"""Stockfish UCI telemetry v1 adapter."""

from __future__ import annotations

from .uci import BaseUciTelemetryAdapter


class StockfishTelemetryAdapter(BaseUciTelemetryAdapter):
    engine = "stockfish"
    nodes_unit = "nodes"

    def score_semantics(self, kind: str) -> tuple[str, str]:
        if kind == "cp":
            return "cp", "stockfish.uci_cp"
        if kind == "mate":
            return "mate", "stockfish.uci_mate"
        raise ValueError(f"unsupported Stockfish score kind: {kind}")
