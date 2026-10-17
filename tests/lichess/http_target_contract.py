"""Canonical HTTP request-target policy for the pinned offline Lichess fixture.

This is used by the fake server, RELEASE-1 smoke verifier and aggregate.
Do not store arbitrary raw query strings; unexpected values may be secrets.
"""
from __future__ import annotations

import hashlib
import re

GAME_ID = "off3b001"
MOVE_PREFIX = f"/api/bot/game/{GAME_ID}/move/"
APPROVED_GETS = frozenset({
    "/api/stream/event", f"/api/bot/game/stream/{GAME_ID}",
    "/api/account", "/api/account/playing", "/api/users/status",
    f"/game/export/{GAME_ID}",
})
APPROVED_POSTS = frozenset({
    "/api/token/test", f"/api/challenge/{GAME_ID}/accept",
    f"/api/challenge/{GAME_ID}/decline",
})
UCI = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")
SAFE_PATH = re.compile(r"/[A-Za-z0-9_./-]{0,220}\Z")


def path_of(target: object) -> str:
    if not isinstance(target, str):
        return "__invalid_path__"
    path = target.split("?", 1)[0]
    return path if SAFE_PATH.fullmatch(path) else "__invalid_path__"


def canonical_target(method: object, raw: object) -> bool:
    """Admit only raw HTTP origin-form requests matched to pinned bridge usage."""
    if (method not in ("GET", "POST") or not isinstance(raw, str)
            or not 1 <= len(raw) <= 255 or not raw.startswith("/")
            or raw.startswith("//") or raw.count("?") > 1
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in raw)
            or "#" in raw or "%" in raw or "\\" in raw):
        return False
    path, sep, query = raw.partition("?")
    if not SAFE_PATH.fullmatch(path):
        return False
    if method == "GET":
        if path not in APPROVED_GETS:
            return False
        if not sep:
            return True
        # The frozen upstream is_online() sends params={"ids": user_id}.
        return path == "/api/users/status" and query == "ids=allfatherfixture"
    if path in APPROVED_POSTS:
        return not sep
    if not path.startswith(MOVE_PREFIX) or not UCI.fullmatch(
            path[len(MOVE_PREFIX):]):
        return False
    # Frozen upstream make_move() calls params={"offeringDraw":"true|false"}.
    return not sep or query in ("offeringDraw=true", "offeringDraw=false")


def safe_audit_fields(method: object, raw: object) -> dict:
    """An approved raw target is retained; rejected raw values are hashed."""
    path = path_of(raw)
    if canonical_target(method, raw):
        return {"path": path, "request_target": raw,
                "target_canonical": True}
    body = raw.encode("utf-8", errors="replace") if isinstance(raw, str) else b""
    return {"path": path, "request_target": None, "target_canonical": False,
            "request_target_sha256": hashlib.sha256(body).hexdigest()}
