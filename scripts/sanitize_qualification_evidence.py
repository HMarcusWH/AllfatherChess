#!/usr/bin/env python3
"""Single bounded output boundary for offline qualification evidence.

All producer-controlled strings are untrusted, including values received
under apparently safe keys. Never emit arbitrary producer request targets,
exception descriptions, free-form filenames or values into CI artifacts.
"""
from __future__ import annotations
import json
import math
import re

SAFE_ENUM_FIELDS=frozenset({
    "status","reason","action","event","endpoint","observation","origin",
    "scenario","color","behavior","release_kind","termination","mode",
    "phase","kind","cause","http_method","method","run_id","run","name",
    "stop_reason","scope","type","event_id","generation",
})
HASH_FIELDS=frozenset({
    "source_commit","bridge_commit","source_sha","sha256","policy_sha256",
    "scenario_source_sha256","sanitized_sha256","manifest_sha256",
    "position_id","owner_epoch","epoch","request_target_sha256",
})
SAFE_KEYS=re.compile(r"[a-z][A-Za-z0-9_-]{0,79}\Z")
SAFE_ENUM=re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,95}\Z")
SAFE_MOVE=re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")
SAFE_ID=re.compile(r"[A-Fa-f0-9]{16,128}\Z")
SAFE_PATH=re.compile(r"/[A-Za-z0-9_./-]{0,220}\Z")
SAFE_REQUEST=re.compile(r"(?:request|stream)-\d{1,12}\Z")
MAX_DEPTH=22
MAX_SEQUENCE=2000


def safe_path(value: object) -> str:
    """Conservatively strip query/fragment, or substitute a fixed sentinel."""
    if type(value) is not str:
        return "__invalid_path__"
    path=value.split("?",1)[0].split("#",1)[0]
    return path if SAFE_PATH.fullmatch(path) else "__invalid_path__"


def safe_string(field: str, value: str) -> str | None:
    if field=="request_target":
        return None
    if field=="path":
        return safe_path(value)
    if field in ("uci","move"):
        return value if SAFE_MOVE.fullmatch(value) else "__redacted__"
    if field in ("request_id","original_request_id"):
        return value if SAFE_REQUEST.fullmatch(value) else "__redacted__"
    if field in HASH_FIELDS or field.endswith("_sha256"):
        return value if SAFE_ID.fullmatch(value) else "__redacted__"
    if field in ("error","errors","runner_errors","message","details",
                 "stdout","stderr","url","uri","query","raw","raw_path"):
        return "__redacted__"
    if field in SAFE_ENUM_FIELDS:
        return value if SAFE_ENUM.fullmatch(value) else "__redacted__"
    # Strict default deny: otherwise any new producer-controlled field can
    # leak a secret without a separate output-safety review.
    return "__redacted__"


def sanitize_report(value: object, *, field: str = "", depth: int = 0):
    if depth>MAX_DEPTH:
        return "__redacted__"
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if type(value) is str:
        return safe_string(field,value)
    if type(value) in (tuple,list):
        return [sanitize_report(row,field=field,depth=depth+1)
                for row in value[:MAX_SEQUENCE]]
    if type(value) is dict:
        report={}
        for key,item in list(value.items())[:MAX_SEQUENCE]:
            if type(key) is str and SAFE_KEYS.fullmatch(key):
                report[key]=sanitize_report(item,field=key,depth=depth+1)
        return report
    return "__redacted__"


def serialize_report(value: object) -> str:
    return json.dumps(sanitize_report(value),sort_keys=True,
                      indent=2,allow_nan=False)+"\n"
