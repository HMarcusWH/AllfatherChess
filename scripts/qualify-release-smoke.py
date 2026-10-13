#!/usr/bin/env python3
"""RELEASE-1 independent host-side validation of packaged offline game evidence.

Never trust the packaged producer's ready/confirmed/cooldown flags as proofs.
Reconstruct each attempted HTTP move against the authoritative trace and
durable journal. No network, credentials, bot authorization, or public release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import unquote

import chess

CASES = {"r00-white": ("R00", "white"),
         "r00-black": ("R00", "black"),
         "r02-white": ("R02", "white"),
         "r10-white": ("R10", "white")}
GAME_ID = "off3b001"
MOVE_PREFIX = f"/api/bot/game/{GAME_ID}/move/"
ALLOWED_POSTS = {"/api/token/test", f"/api/challenge/{GAME_ID}/accept",
                 f"/api/challenge/{GAME_ID}/decline"}
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


def require(value, reason):
    if not value:
        raise ValueError(reason)


def trace_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def verify_smoke(doc: dict, key: str) -> dict:
    require(key in CASES, "unapproved offline smoke identity")
    scenario, color = CASES[key]
    require(type(doc) is dict and doc.get("schema_version") == 2 and
            doc.get("run_id") == key and doc.get("scenario") == scenario and
            doc.get("color") == color and doc.get("offline") is True and
            doc.get("public_bot_release") is False and
            doc.get("ready") is True and
            doc.get("runner_errors") == [],
            "packaged offline smoke has invalid source/disposition identity")
    game = doc.get("game")
    requests = doc.get("http")
    trace = doc.get("transport_trace")
    journal = doc.get("journal")
    obs = doc.get("observations")
    require(all(type(x) is dict for x in (game, journal)) and
            type(requests) is list and type(trace) is list and bool(trace) and
            type(obs) is list and bool(obs),
            "packaged smoke is missing authoritative evidence")
    require(game.get("game_id") == GAME_ID and game.get("color") == color and
            isinstance(game.get("moves"), list) and
            isinstance(game.get("accepted"), list) and
            game.get("forbidden_attempts") == [],
            "invalid authoritative game or forbidden endpoint evidence")
    require(doc.get("transport_trace_sha256") ==
            hashlib.sha256(trace_bytes(trace)).hexdigest(),
            "ordered transport trace digest mismatch")
    require(all(type(t) is dict and type(t.get("seq")) is int and
                type(t.get("monotonic_ns")) is int
                for t in trace) and
            [t["seq"] for t in trace] == list(range(1, len(trace) + 1)) and
            all(trace[i]["monotonic_ns"] <= trace[i+1]["monotonic_ns"]
                for i in range(len(trace)-1)),
            "transport ordering or monotonic observation invalid")
    require(type(journal.get("intents")) is list and
            type(journal.get("games")) is list and
            isinstance(journal.get("owner_epoch"), str),
            "durable journal incomplete")

    attempt_index = {}
    commits = []
    authoritative = []
    board = chess.Board()
    seen_ply = set()
    history_at_attempt = {}
    for row in trace:
        event = row.get("event")
        if event == "move_attempt":
            ply = len(board.move_stack)
            rid = f"request-{row['seq']}"
            require(row.get("game_id") == GAME_ID and
                    type(row.get("ply_at_receipt")) is int and
                    row["ply_at_receipt"] == ply and
                    board.turn == (color == "white") and ply not in seen_ply,
                    "HTTP move attempted for wrong game/turn/ply or repeated intent")
            uci = row.get("uci")
            try:
                move = chess.Move.from_uci(uci)
            except (TypeError, ValueError) as exc:
                raise ValueError("malformed attempted UCI move") from exc
            require(move != chess.Move.null() and move in board.legal_moves and
                    move.uci() == uci and row.get("path") == MOVE_PREFIX + uci,
                    "position-illegal, null or noncanonical HTTP move submission")
            seen_ply.add(ply)
            attempt_index[rid] = row
            history_at_attempt[rid] = [m.uci() for m in board.move_stack]
        elif event == "board_commit":
            require(row.get("game_id") == GAME_ID and
                    type(row.get("ply_before")) is int and
                    row["ply_before"] == len(board.move_stack) and
                    row.get("fen_before") == board.fen(),
                    "authoritative commit before-position mismatch")
            try:
                move = chess.Move.from_uci(row.get("uci"))
            except (TypeError, ValueError) as exc:
                raise ValueError("malformed authoritative chess commit") from exc
            require(move in board.legal_moves,
                    "illegal authoritative board commit")
            origin = row.get("origin")
            require(origin in ("allfather", "scripted_opponent"),
                    "unknown authoritative move origin")
            if origin == "allfather":
                rid = row.get("request_id")
                prior = attempt_index.get(rid)
                require(prior is not None and prior.get("uci") == row["uci"] and
                        prior.get("ply_at_receipt") == row["ply_before"] and
                        prior["seq"] < row["seq"],
                        "bot server commit lacks exact earlier HTTP move attempt")
            else:
                require(row.get("request_id") is None,
                        "scripted opponent has bot request identity")
            board.push(move)
            require(row.get("ply_after") == len(board.move_stack) and
                    row.get("fen_after") == board.fen(),
                    "authoritative commit after-position mismatch")
            commits.append(row)
            authoritative.append(move.uci())

    require(authoritative == game["moves"] and
            [dict(ply=x["ply_after"], uci=x["uci"]) for x in commits
             if x["origin"] == "allfather"] == game["accepted"] and
            doc.get("commits") == commits,
            "authoritative history/accepted moves/commit extract disagree")
    terminal = board.outcome(claim_draw=False)
    if scenario == "R10":
        require(game.get("complete") is False and terminal is None,
                "429 suspension fabricated a completed game")
    else:
        require(game.get("complete") is True and terminal is not None and
                isinstance(game.get("outcome"), dict) and
                game["outcome"].get("termination") == terminal.termination.name,
                "completed game lacks independent rules-based termination")

    http_attempts = {}
    for request in requests:
        require(type(request) is dict and
                request.get("method") in ("GET", "POST") and
                type(request.get("status")) is int and
                type(request.get("monotonic_ms")) is int and
                request["monotonic_ms"] >= 0 and
                isinstance(request.get("path"), str) and
                request["status"] != 403,
                "unexpected or malformed HTTP request evidence")
        if request["method"] != "POST":
            require(request.get("request_id") is None,
                    "GET request has move request identity")
            continue
        path = request["path"]
        if path.startswith(MOVE_PREFIX):
            rid = request.get("request_id")
            require(isinstance(rid, str) and rid in attempt_index and
                    rid not in http_attempts and
                    attempt_index[rid].get("path") == path and
                    request["status"] in ((200, 429) if scenario == "R10" else (200,)),
                    "unexpected, duplicate or rejected HTTP move request")
            http_attempts[rid] = request
        else:
            require(path in ALLOWED_POSTS and request["status"] == 200 and
                    request.get("request_id") is None,
                    "unsupported non-move POST or rejection")
    require(set(http_attempts) == set(attempt_index),
            "HTTP request audit omits or duplicates trace move attempts")
    require(all(type(a.get("status")) is int for a in http_attempts.values()),
            "unrecorded HTTP move status")
    for row in trace:
        if (row.get("event") not in
                ("response_write_completed", "connection_close_requested") or
                not isinstance(row.get("path"), str) or
                not row["path"].startswith(MOVE_PREFIX)):
            continue
        rid = row.get("request_id")
        require(rid in attempt_index and
                row["path"] == attempt_index[rid]["path"] and
                row["seq"] > attempt_index[rid]["seq"],
                "move response/close event lacks exact request correlation")
        if row["event"] == "response_write_completed":
            require(type(row.get("status")) is int and
                    row["status"] == http_attempts[rid]["status"],
                    "completed move response status differs from HTTP audit")

    intents = journal["intents"]
    require(all(isinstance(i, dict) for i in intents), "malformed durable intent")
    by_ply = {}
    for intent in intents:
        require(intent.get("game_id") == GAME_ID and
                type(intent.get("ply_before")) is int and
                isinstance(intent.get("move"), str) and
                isinstance(intent.get("epoch"), str),
                "durable intent has invalid identity")
        key_intent = intent["ply_before"]
        require(key_intent not in by_ply, "duplicate durable intent at same game ply")
        by_ply[key_intent] = intent
        require(0 <= key_intent <= len(authoritative) and
                json.loads(intent["history_json"]) == authoritative[:key_intent],
                "durable intent history differs from authoritative game prefix")
    guarded = doc.get("guard_events")
    require(type(guarded) is list, "missing guarded submission audit")
    guarded_submissions = [g for g in guarded
                           if isinstance(g, dict) and
                           g.get("event") == "http_move_submission_attempt"]
    require(len(guarded_submissions) == len(attempt_index),
            "guarded HTTP submission log does not cover exact requests")
    for rid, attempt in attempt_index.items():
        ply = attempt["ply_at_receipt"]
        intent = by_ply.get(ply)
        require(intent is not None and intent.get("move") == attempt["uci"] and
                json.loads(intent["history_json"]) == history_at_attempt[rid] and
                len([g for g in guarded_submissions
                     if g.get("game_id") == GAME_ID and
                     g.get("ply") == ply and g.get("uci") == attempt["uci"] and
                     g.get("owner_epoch") == intent["epoch"]]) == 1,
                "move attempt lacks unique matching journal/guard authorization")
    for commit in commits:
        if commit["origin"] != "allfather":
            continue
        intent = by_ply.get(commit["ply_before"])
        require(intent is not None and
                intent.get("move") == commit["uci"] and
                intent.get("status") == "CONFIRMED",
                "server-accepted move was not durably confirmed")
    records = [g for g in journal["games"]
               if isinstance(g, dict) and g.get("game_id") == GAME_ID]
    require(len(records) == 1, "authoritative game missing from journal")
    stored = json.loads(records[0]["history_json"])
    require(stored == authoritative if scenario != "R10"
            else stored == authoritative[:len(stored)],
            "durable game history diverges from server")
    phases = obs
    require(all(isinstance(p, dict) and isinstance(p.get("epoch"), str)
                and type(p.get("phase")) is int for p in phases) and
            journal["owner_epoch"] == phases[-1]["epoch"],
            "phase/journal ownership epoch mismatch")

    fault = doc.get("fault") or {}
    activations = fault.get("activations")
    require(isinstance(activations, list), "scenario fault activations missing")
    fault_events = [r for r in trace if r.get("event") == "fault_activated"]
    if scenario == "R00":
        require(len(phases) == 1 and len(activations) == 0
                and not fault_events and
                phases[0].get("observation") == "complete_confirmed",
                "normal image game had unexpected fault or incomplete confirmation")
    else:
        action = "commit_then_close" if scenario == "R02" else "rate_limit_429"
        require(len(activations) == len(fault_events) == 1 and
                activations[0].get("action") == action and
                fault_events[0].get("action") == action and
                activations[0].get("request_id") == fault_events[0].get("request_id"),
                "fault activation lacks exact independent server trace")
        rid = fault_events[0].get("request_id")
        require(rid in attempt_index, "fault targeted unobserved HTTP move attempt")
        if scenario == "R02":
            closed = [r for r in trace if
                      r.get("event") == "connection_close_requested"
                      and r.get("request_id") == rid and
                      r.get("path") == attempt_index[rid]["path"] and
                      r["seq"] > fault_events[0]["seq"]]
            all_closes = [r for r in trace if
                          r.get("event") == "connection_close_requested" and
                          r.get("path") == attempt_index[rid]["path"]]
            completed_responses = [r for r in trace if
                                   r.get("event") == "response_write_completed" and
                                   r.get("request_id") == rid]
            commits_for_request = [r for r in commits if
                                   r.get("request_id") == rid]
            first_epoch = phases[0]["epoch"] if phases else None
            unknown_ack = [g for g in guarded if isinstance(g, dict) and
                           g.get("event") == "http_unknown_ack" and
                           g.get("game_id") == GAME_ID and
                           g.get("owner_epoch") == first_epoch]
            acknowledged = [g for g in guarded if isinstance(g, dict) and
                            g.get("event") == "http_move_ack_observed" and
                            g.get("game_id") == GAME_ID and
                            g.get("owner_epoch") == first_epoch and
                            g.get("move") == attempt_index[rid]["uci"]]
            first_submissions = [g for g in guarded_submissions if
                                 g.get("game_id") == GAME_ID and
                                 g.get("owner_epoch") == first_epoch]
            require(len(closed) == len(all_closes) ==
                    len(commits_for_request) == len(unknown_ack) ==
                    len(first_submissions) == 1 and
                    not completed_responses and not acknowledged and
                    commits_for_request[0]["seq"] < closed[0]["seq"] and
                    attempt_index[rid]["seq"] < fault_events[0]["seq"] <
                    commits_for_request[0]["seq"] and
                    len(phases) == 2 and
                    phases[0].get("observation") == "restart_after_lost_ack" and
                    phases[1].get("observation") == "complete_confirmed" and
                    phases[0]["epoch"] != phases[1]["epoch"] and
                    any(i.get("epoch") == phases[1]["epoch"] and
                        i.get("status") == "CONFIRMED" and
                        i.get("ply_before") in {c["ply_before"] for c in commits
                            if c["origin"] == "allfather"} for i in intents),
                    "lost-ack restart lacks new-epoch authoritative confirmation")
        else:
            limited = [(ident, r) for ident, r in http_attempts.items()
                       if r["status"] == 429]
            require(len(limited) == 1 and limited[0][0] == rid and
                    not any(c.get("request_id") == rid for c in commits),
                    "429 fault does not match one uncommitted HTTP attempt")
            first = limited[0][1]["monotonic_ms"]
            require(not any(0 <= r["monotonic_ms"] - first < 60000
                            for r in requests if r is not limited[0][1]) and
                    type(journal.get("cooldown")) is list and bool(journal["cooldown"]) and
                    len(phases) == 1 and
                    phases[0].get("observation") == "rate_limited_suspension",
                    "rate-limit suspension missed account-wide 60-second cooldown")
            hold = [r for r in trace if
                    r.get("event") == "rate_limit_hold_completed" and
                    r.get("action") == "rate_limit_429"]
            require(len(hold) == 1 and
                    hold[0]["monotonic_ns"] - fault_events[0]["monotonic_ns"]
                    >= 61_000_000_000,
                    "R10 post-429 observation did not last 61 seconds")

    return {"schema_version": 1, "scenario": scenario, "color": color,
            "run_id": key, "independent_qualified": True,
            "legal_unique_attempts": len(attempt_index),
            "authoritative_commits": len(commits),
            "public_bot_release": False, "release_blocked": True}


def verify_persisted_sqlite(folder: Path, journal: dict) -> dict:
    """Independently verify committed on-disk state, including live WAL entries.

    A producer's JSON snapshot is never authoritative. This is a consistency
    check against persisted SQLite, not protection against a writer able to
    rewrite both the database and evidence.
    """
    state = folder / "state"
    db = state / "move-journal.sqlite3"
    require(folder.is_dir() and not folder.is_symlink() and
            not folder.parent.is_symlink() and
            state.is_dir() and not state.is_symlink() and
            db.is_file() and not db.is_symlink(),
            "persisted SQLite journal absent or linked")
    for suffix in ("-wal", "-shm"):
        sidecar = state / ("move-journal.sqlite3" + suffix)
        require(not sidecar.is_symlink(),
                "persisted SQLite sidecar cannot be a symlink")
    require(db.stat().st_size <= 256 * 1024 * 1024,
            "persisted SQLite exceeds bounded verification size")
    require(type(journal) is dict, "persisted SQLite snapshot absent")
    connection = sqlite3.connect(
        db.resolve(strict=True).as_uri() + "?mode=ro",
        uri=True, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")  # consistent view of the main file and WAL
        try:
            require([tuple(r) for r in connection.execute(
                "PRAGMA integrity_check")] == [("ok",)],
                "persisted SQLite integrity check failed")
            owner = connection.execute(
                "SELECT epoch FROM owner WHERE singleton=1").fetchone()
            require(owner is not None and
                    owner["epoch"] == journal.get("owner_epoch"),
                    "SQLite owner epoch differs from JSON snapshot")
            queries = {
                "games": "SELECT * FROM games ORDER BY game_id",
                "intents": "SELECT * FROM intents ORDER BY game_id,ply_before",
                "cooldown": "SELECT * FROM cooldown ORDER BY singleton",
                "events": "SELECT * FROM events ORDER BY event_id",
            }
            observed = {name: [dict(row) for row in connection.execute(query)]
                        for name, query in queries.items()}
            for name, rows in observed.items():
                require(type(journal.get(name)) is list and
                        rows == journal[name],
                        "SQLite persisted " + name + " differs from JSON snapshot")
        finally:
            connection.execute("ROLLBACK")
    finally:
        connection.close()
    return {"persisted_sqlite_verified": True,
            "durable_events": len(observed["events"]),
            "durable_intents": len(observed["intents"])}


def verify_file(path: Path, key: str) -> dict:
    require(path.is_file() and not path.is_symlink() and
            path.stat().st_size <= MAX_EVIDENCE_BYTES,
            "packaged smoke evidence absent, linked or oversized")
    doc = json.loads(path.read_text(encoding="utf-8"))
    require(type(doc) is dict and type(doc.get("journal")) is dict,
            "packaged smoke durable journal snapshot missing")
    persisted = verify_persisted_sqlite(path.parent, doc["journal"])
    verdict = verify_smoke(doc, key)
    verdict.update(persisted)
    return verdict


def qualify_directory(root: Path) -> dict:
    results, failures = {}, []
    for key in CASES:
        try:
            results[key] = verify_file(root / key / "result.json", key)
        except (OSError, ValueError, KeyError, TypeError, IndexError,
                sqlite3.DatabaseError) as exc:
            failures.append({"scenario": key,
                             "error": type(exc).__name__ + ": " + str(exc)})
    return {"schema_version": 1, "offline_smoke_qualified": not failures and
            len(results) == len(CASES),
            "verified_cases": results, "failures": failures,
            "public_bot_release": False, "release_blocked": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = qualify_directory(args.smoke_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["offline_smoke_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
