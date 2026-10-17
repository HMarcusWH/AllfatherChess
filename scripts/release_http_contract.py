#!/usr/bin/env python3
"""Authoritative RELEASE-1 HTTP request-evidence shape, shared by two gates.

The fake server enforces these endpoints. This module only verifies recorded
HTTP method/path/status/request ID. Independent host verification separately
reconstructs chess legality, receipt timing, epochs and durable intent.
"""
from __future__ import annotations
import re

from tests.lichess.http_target_contract import (
    GAME_ID, MOVE_PREFIX, APPROVED_GETS, APPROVED_POSTS, UCI,
    canonical_target)

ALLOWED_GETS = APPROVED_GETS
ALLOWED_POSTS = APPROVED_POSTS
UCI_TOKEN = UCI

def allowed_release_http(row: object, key: str) -> bool:
    """No implicit wildcard GET and no successful forbidden HTTP response."""
    if (type(row) is not dict or key not in
            ("r00-white", "r00-black", "r02-white", "r10-white")):
        return False
    method = row.get("method")
    path = row.get("path")
    status = row.get("status")
    rid = row.get("request_id")
    target = row.get("request_target")
    if (method not in ("GET", "POST") or
            type(path) is not str or type(status) is not int or
            row.get("target_canonical") is not True or
            not canonical_target(method, target) or
            path != target.split("?", 1)[0]):
        return False
    if method == "GET":
        return status == 200 and rid is None
    if path in ALLOWED_POSTS:
        return status == 200 and rid is None
    if not path.startswith(MOVE_PREFIX):
        return False
    uci = path[len(MOVE_PREFIX):]
    return (UCI_TOKEN.fullmatch(uci) is not None and
            len(uci) in (4, 5) and
            isinstance(rid, str) and bool(rid.strip()) and
            status in ((200, 429) if key == "r10-white" else (200,)))
