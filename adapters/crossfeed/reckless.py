"""Reckless-specific cross-feed adapter."""

from __future__ import annotations

from .base import BaseCrossFeedAdapter


class RecklessCrossFeedAdapter(BaseCrossFeedAdapter):
    family = "reckless"
    native_semantics_prefix = "reckless."
