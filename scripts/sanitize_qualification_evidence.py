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

ACTIONS=frozenset({
    "none","drop_before_commit","commit_then_close","commit_then_504",
    "duplicate_move","delay_state","duplicate_state","stale_state",
    "partial_chunk","replace_event_stream","rate_limit_429","wrong_game",
    "low_clock_close","terminate_bridge","duplicate_terminal",
})
SCENARIOS=frozenset(f"R{i:02d}" for i in range(15))
BEHAVIORS=frozenset({
    "SAFE_CONTINUATION","UNSAFE_WITNESS","OBSERVED_COMPLETION",
    "OBSERVED_STOP_OR_UNRESOLVED","NORMAL_COMPLETION","RECOVERED",
    "SAFE_SUSPENSION","INCONCLUSIVE","OFFLINE_SMOKE_PASS",
    "OFFLINE_SMOKE_FAIL","CONFIRMED","UNKNOWN_ACK",
})
SAFE_ENUM_VALUES={
    "status":BEHAVIORS,
    "behavior":BEHAVIORS,
    "scenario":SCENARIOS,
    "color":frozenset(("white","black")),
    "origin":frozenset(("allfather","scripted_opponent")),
    "method":frozenset(("GET","POST")),
    "http_method":frozenset(("GET","POST")),
    "action":ACTIONS,
    "endpoint":frozenset(("move","move_post","game_state","event_stream",
                          "terminal_state","account","token","challenge")),
    "reason":frozenset(("noncanonical_request_target",
                         "unapproved_http_status",
                         "position_illegal_or_wrong_turn",
                         "repeated_move_submission_same_ply",
                         "once_legal_retry_after_lost_ack",
                         "duplicate_unconfirmed_move_submission")),
    "observation":frozenset(("complete_confirmed","restart_after_lost_ack",
                           "rate_limited_suspension","fault_triggered_bridge_kill",
                           "bounded_postfault_observation","not_started",
                           "experiment_timeout","game_stream_observed",
                           "injected_bridge_kill","server_game_complete",
                           "terminal_reconciliation_timeout",
                           "restart_requested_after_fault",
                           "unresolved_precommit_contained",
                           "rate_limit_containment_window",
                           "invalid_move_containment_window",
                           "bridge_exited","bounded_restarted_game",
                           "bounded_experiment_timeout","phase_error",
                           "launching")),
    "termination":frozenset(("CHECKMATE","STALEMATE",
                             "INSUFFICIENT_MATERIAL","SEVENTYFIVE_MOVES",
                             "FIVEFOLD_REPETITION","VARIANT_WIN",
                             "VARIANT_LOSS","VARIANT_DRAW")),
    "cause":frozenset(("http429:move","quiesce")),
    "scope":frozenset(("offline-fault-harness-only",
                      "offline-guarded-bridge-only")),
    "mode":frozenset(("OFFLINE-4B-ONLY","OFFLINE-ONLY")),
    "release_kind":frozenset(("ONLINE-3A-OFFLINE-ONLY",)),
    "event":frozenset(("move_attempt","board_commit","fault_activated",
                      "http_move_submission_attempt","http_unknown_ack",
                      "http_move_ack_observed","global_rate_limit_block",
                      "rate_limit_hold_completed","response_write_completed",
                      "connection_close_requested","stream_emit",
                      "stream_partial_chunk","cooldown","subscribe",
                      "position_suspended","authoritative_snapshot_observed",
                      "unavailable_or_illegal_uci_move","move_prepare_refused")),
}
SAFE_ENUM_FIELDS=frozenset(SAFE_ENUM_VALUES)
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
        return value if value in SAFE_ENUM_VALUES[field] else "__redacted__"
    if field == "run_id":
        return value if value in {f"r{i:02d}-{color}" for i in range(15)
                                  for color in ("white","black")} else "__redacted__"
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
