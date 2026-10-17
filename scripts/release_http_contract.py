#!/usr/bin/env python3
"""Authoritative RELEASE-1 HTTP request-evidence shape, shared by two gates.

The fake server enforces these endpoints. This module only verifies recorded
HTTP method/path/status/request ID. Independent host verification separately
reconstructs chess legality, receipt timing, epochs and durable intent.
"""
from __future__ import annotations
import re

GAME_ID = "off3b001"
MOVE_PREFIX = f"/api/bot/game/{GAME_ID}/move/"
ALLOWED_POSTS = frozenset({
    "/api/token/test",
    f"/api/challenge/{GAME_ID}/accept",
    f"/api/challenge/{GAME_ID}/decline",
})
ALLOWED_GETS = frozenset({
    "/api/stream/event",
    f"/api/bot/game/stream/{GAME_ID}",
    "/api/account",
    "/api/account/playing",
    "/api/users/status",
    f"/game/export/{GAME_ID}",
})
UCI_TOKEN = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")


def allowed_release_http(row: object, key: str) -> bool:
    """No implicit wildcard GET and no successful forbidden HTTP response."""
    if (type(row) is not dict or key not in
            ("r00-white", "r00-black", "r02-white", "r10-white")):
        return False
    method = row.get("method")
    path = row.get("path")
    status = row.get("status")
    rid = row.get("request_id")
    if (method not in ("GET", "POST") or
            type(path) is not str or type(status) is not int):
        return False
    if method == "GET":
        return path in ALLOWED_GETS and status == 200 and rid is None
    if path in ALLOWED_POSTS:
        return status == 200 and rid is None
    if not path.startswith(MOVE_PREFIX):
        return False
    uci = path[len(MOVE_PREFIX):]
    return (UCI_TOKEN.fullmatch(uci) is not None and
            len(uci) in (4, 5) and
            isinstance(rid, str) and bool(rid.strip()) and
            status in ((200, 429) if key == "r10-white" else (200,)))
