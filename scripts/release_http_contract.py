#!/usr/bin/env python3
"""Authoritative RELEASE-1 HTTP request-evidence shape, shared by two gates.

The fake server enforces these endpoints. This module only verifies recorded
HTTP method/path/status/request ID. Independent host verification separately
reconstructs chess legality, receipt timing, epochs and durable intent.
"""
from __future__ import annotations
import re
import importlib.util
from pathlib import Path

# Resolve the single approved policy by file identity. RELEASE-1's standalone
# host verifier runs as a script, often with sys.path[0] = /repo/scripts;
# importing a top-level "tests" package then depends on CWD/PYTHONPATH.
_POLICY = Path(__file__).resolve().parents[1] / "tests/lichess/http_target_contract.py"
_spec = importlib.util.spec_from_file_location("release1_http_target_policy", _POLICY)
if _spec is None or _spec.loader is None:
    raise ValueError("RELEASE-1 HTTP target policy not available")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

GAME_ID = policy.GAME_ID
MOVE_PREFIX = policy.MOVE_PREFIX
APPROVED_GETS = policy.APPROVED_GETS
APPROVED_POSTS = policy.APPROVED_POSTS
UCI = policy.UCI
canonical_target = policy.canonical_target

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
