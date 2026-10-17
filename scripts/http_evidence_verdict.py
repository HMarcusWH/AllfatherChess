#!/usr/bin/env python3
"""Independent status adjudication of an *observed*, canonical HTTP request.

An injected fault status is legitimate only on the exact request ID selected
by the frozen source trace. This validator never assumes syntactically valid
GET/POST equals successful or safe operation.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/"qualification/http-evidence-contract-v1.json"
MOVE_PREFIX="/api/bot/game/off3b001/move/"


def approved_status(request: object, trace: list[dict],
                    action: str="none") -> bool:
    if not isinstance(request,dict) or type(request.get("status")) is not int:
        return False
    policy=json.loads(POLICY.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1 or policy.get("public_bot_release") is True:
        return False
    method,path,status=request.get("method"),request.get("path"),request["status"]
    if method not in ("GET","POST") or type(path) is not str:
        return False
    if method == "GET" or not path.startswith(MOVE_PREFIX):
        return status == policy["non_move_status"]
    if status == policy["ordinary_move_status"]:
        return True
    target=policy["fault_specific_move_status"].get(action)
    if target is None or status != target:
        return False
    request_id=request.get("request_id")
    return (type(request_id) is str and bool(request_id) and
            sum(1 for event in trace if type(event) is dict and
                event.get("event")=="fault_activated" and
                event.get("action")==action and
                event.get("endpoint")=="move_post" and
                event.get("request_id")==request_id)==1)


def unexpected_statuses(requests: list[dict], trace: list[dict],
                        action: str) -> list[dict]:
    """Return safe bounded labels; no raw requests, URL or query strings."""
    result=[]
    for row in requests:
        if not approved_status(row,trace,action):
            result.append({
                "method":row.get("method") if row.get("method") in ("GET","POST")
                         else "unknown",
                "status":row.get("status") if type(row.get("status")) is int else None,
                "reason":"unapproved_http_status",
            })
    return result[:32]
