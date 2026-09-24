"""LC0-specific cross-feed adapter."""

from __future__ import annotations

from .base import BaseCrossFeedAdapter


class Lc0CrossFeedAdapter(BaseCrossFeedAdapter):
    family = "lc0"
    native_semantics_prefix = "lc0."
