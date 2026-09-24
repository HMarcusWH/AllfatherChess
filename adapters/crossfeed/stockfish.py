"""Stockfish-specific cross-feed adapter."""

from __future__ import annotations

from .base import BaseCrossFeedAdapter


class StockfishCrossFeedAdapter(BaseCrossFeedAdapter):
    family = "stockfish"
    native_semantics_prefix = "stockfish."
