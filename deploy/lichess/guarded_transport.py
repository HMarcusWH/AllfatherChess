"""Source-locked OFFLINE-4B outbound API gate and move-intent discipline.

This module is copied into a verified temporary lichess-bot execution tree.
All upstream network calls go through guarded_post / before_request. No public
Lichess endpoint, account upgrade, external credential or live bot permission.
"""
from __future__ import annotations
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import chess
import requests
from deploy.lichess.move_journal import MoveJournal, JournalError, MoveUnresolved
from deploy.lichess.reconcile import canonical_history


class RecoverySuspended(RuntimeError):
    pass


DENIED_ENDPOINTS = {"abort", "resign", "chat", "takeback", "upgrade",
                    "challenge", "cancel"}
ALLOWED_POSTS = {"token_test", "accept", "decline", "move"}


def _context():
    if os.getenv("ONLINE4B_ISOLATED_CONTAINER") != "1":
        raise RecoverySuspended("recovery adapter refuses non-isolated operation")
    db = os.getenv("ALLFATHER_RECOVERY_DB")
    epoch = os.getenv("ALLFATHER_RECOVERY_EPOCH")
    if not db or not epoch:
        raise RecoverySuspended("missing durable recovery journal or session epoch")
    journal = MoveJournal(Path(db))
    return journal, epoch


def _audit(event: str, **fields):
    destination = os.getenv("ALLFATHER_RECOVERY_AUDIT")
    if not destination:
        raise RecoverySuspended("missing durable recovery audit destination")
    payload = json.dumps({"timestamp": time.time(), "event": event,
                          "owner_epoch": os.getenv("ALLFATHER_RECOVERY_EPOCH"),
                          **fields}, sort_keys=True, separators=(",", ":")) + "\n"
    data = payload.encode()
    if len(data) > 4096:
        raise RecoverySuspended("recovery audit record oversized")
    fd = os.open(destination, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise RecoverySuspended("incomplete durable recovery audit write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _require_unfenced(journal: MoveJournal, epoch: str):
    try:
        remaining = journal.cooldown_left(epoch)
    except JournalError as exc:
        raise RecoverySuspended(str(exc)) from exc
    if remaining > 0:
        raise RecoverySuspended("account API requests blocked by global cooldown")


def before_request(endpoint_name: str):
    journal, epoch = _context()
    _require_unfenced(journal, epoch)
    if endpoint_name in DENIED_ENDPOINTS:
        _audit("prohibited_endpoint_suppressed", endpoint=endpoint_name)
        raise RecoverySuspended("upstream forbidden endpoint suppressed: " + endpoint_name)
    if not isinstance(endpoint_name, str) or not endpoint_name:
        raise RecoverySuspended("invalid upstream API operation")


def observe_state(game_id: str, state: dict):
    journal, epoch = _context()
    try:
        disposition = journal.observe(epoch, game_id, state)
        _audit("authoritative_snapshot_observed", game_id=game_id,
               disposition=disposition, ply=len(canonical_history(state["moves"])))
        return disposition
    except JournalError as exc:
        _audit("authoritative_snapshot_rejected", game_id=game_id, reason=str(exc))
        raise RecoverySuspended(str(exc)) from exc


def require_ready(game_id: str, board: chess.Board):
    journal, epoch = _context()
    try:
        journal.assert_ready(epoch, game_id, tuple(m.uci() for m in board.move_stack))
    except JournalError as exc:
        _audit("position_suspended", game_id=game_id, reason=str(exc))
        raise RecoverySuspended(str(exc)) from exc


def stage_move(board: chess.Board, result: chess.engine.PlayResult, game_id: str):
    journal, epoch = _context()
    move = result.move
    if (move is None or move == chess.Move.null() or
        move not in board.legal_moves or getattr(result, "resigned", False)):
        _audit("unavailable_or_illegal_uci_move", game_id=game_id,
               uci="0000" if move is None or move == chess.Move.null() else str(move))
        raise RecoverySuspended("engine did not provide a legal non-null move")
    history = tuple(m.uci() for m in board.move_stack)
    try:
        journal.assert_ready(epoch, game_id, history)
        pos_id = journal.prepare(epoch, game_id, history, move.uci())
    except JournalError as exc:
        _audit("move_prepare_refused", game_id=game_id, reason=str(exc))
        raise RecoverySuspended(str(exc)) from exc
    _audit("move_intent_prepared", game_id=game_id, ply=len(history),
           uci=move.uci(), position_id=pos_id)


def _cooldown_duration(response: requests.Response) -> float:
    # Lichess general 429 guidance is >=60 seconds. A longer Retry-After wins.
    value = response.headers.get("Retry-After")
    if value is None:
        return 60.0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 60.0
    if not math.isfinite(seconds) or seconds < 0:
        return 60.0
    return max(60.0, min(seconds, 86400.0))


def guarded_post(session, endpoint_name: str, template_args: tuple[str, ...],
                 url: str, *, data=None, headers=None, params=None, payload=None):
    journal, epoch = _context()
    before_request(endpoint_name)
    if endpoint_name not in ALLOWED_POSTS:
        raise RecoverySuspended("upstream POST endpoint not authorized")
    parsed = urlparse(url)
    if parsed.hostname != "127.0.0.1" or parsed.scheme != "http":
        raise RecoverySuspended("recovery transport permits loopback HTTP only")
    move_context = None
    if endpoint_name == "move":
        if len(template_args) != 2:
            raise RecoverySuspended("move POST requires game id and UCI")
        game_id, uci = template_args
        intent = journal.intent(epoch, game_id)
        if intent is None or intent["move"] != uci:
            raise RecoverySuspended("move POST has no identical prepared intent")
        before = tuple(json.loads(intent["history_json"]))
        try:
            journal.change_status(epoch, game_id, uci, before=before,
                                  after="SUBMITTING", allowed=("PREPARED",))
        except JournalError as exc:
            _audit("duplicate_submission_blocked", game_id=game_id, move=uci)
            raise RecoverySuspended(str(exc)) from exc
        move_context = (game_id, uci, before)
        _audit("http_move_submission_attempt", game_id=game_id,
               uci=uci, ply=len(before))
    try:
        response = session.post(url, data=data, headers=headers,
                                params=params, json=payload, timeout=2)
    except requests.exceptions.RequestException as exc:
        if move_context:
            game_id, uci, before = move_context
            journal.change_status(epoch, game_id, uci, before=before,
                                  after="UNKNOWN_ACK", allowed=("SUBMITTING",))
        _audit("http_unknown_ack", endpoint=endpoint_name,
               game_id=move_context[0] if move_context else None)
        raise RecoverySuspended("HTTP submission outcome uncertain") from exc
    status = response.status_code
    if status == 429:
        journal.establish_cooldown(epoch, _cooldown_duration(response),
                                   cause="http429:"+endpoint_name)
        if move_context:
            game_id, uci, before = move_context
            journal.change_status(epoch, game_id, uci, before=before,
                                  after="UNKNOWN_ACK", allowed=("SUBMITTING",))
        _audit("global_rate_limit_block", endpoint=endpoint_name, status=429)
        raise RecoverySuspended("server rate-limited account; entire API suspended")
    if move_context:
        game_id, uci, before = move_context
        if status == 200:
            journal.change_status(epoch, game_id, uci, before=before,
                                  after="ACK_OBSERVED", allowed=("SUBMITTING",))
            _audit("http_move_ack_observed", game_id=game_id, move=uci)
        else:
            journal.change_status(epoch, game_id, uci, before=before,
                                  after="UNKNOWN_ACK", allowed=("SUBMITTING",))
            _audit("http_move_unresolved", game_id=game_id, move=uci, status=status)
            raise RecoverySuspended("non-successful HTTP move response unresolved")
    return response
