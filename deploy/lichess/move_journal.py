"""Transactional per-game move intent for source-locked OFFLINE-4B recovery.

SQLite FULL synchronous + WAL and BEGIN IMMEDIATE order all submission intents.
A persisted SUBMITTING/UNKNOWN_ACK is never retried without server confirmation.
No data may be stored inside the transient per-game release directory.
"""
from __future__ import annotations
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from deploy.lichess.reconcile import (
    ReconciliationError, canonical_history, classify_snapshot, position_id,
)


class JournalError(RuntimeError):
    pass


class MoveUnresolved(JournalError):
    pass


class SessionFenced(JournalError):
    pass


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS owner (
         singleton INTEGER PRIMARY KEY CHECK(singleton=1),
         epoch TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS games (
         game_id TEXT PRIMARY KEY, history_json TEXT NOT NULL,
         terminal INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS intents (
         game_id TEXT NOT NULL, ply_before INTEGER NOT NULL,
         history_json TEXT NOT NULL, position_id TEXT NOT NULL,
         move TEXT NOT NULL, status TEXT NOT NULL, epoch TEXT NOT NULL,
         PRIMARY KEY(game_id,ply_before))""",
    """CREATE TABLE IF NOT EXISTS cooldown (
         singleton INTEGER PRIMARY KEY CHECK(singleton=1),
         until_wall REAL NOT NULL, cause TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS events (
         event_id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
         epoch TEXT NOT NULL, event TEXT NOT NULL, game_id TEXT,
         details TEXT NOT NULL)""",
)
UNRESOLVED = ("PREPARED", "SUBMITTING", "ACK_OBSERVED", "UNKNOWN_ACK")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class MoveJournal:
    def __init__(self, path: Path):
        raw = Path(path)
        if raw.is_symlink() or raw.parent.is_symlink():
            raise JournalError("persistent journal path must not be symlinked")
        self.path = raw.resolve()

    def _connect(self):
        if not self.path.is_file():
            raise JournalError("persistent recovery database missing")
        c = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=FULL")
        return c

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise JournalError("linked recovery database refused")
        c = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            for query in SCHEMA:
                c.execute(query)
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise JournalError("recovery database integrity check failed")
        finally:
            c.close()

    @contextmanager
    def transaction(self, *, epoch: str | None = None, write: bool = True):
        c = self._connect()
        try:
            c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise JournalError("database corrupted")
            if epoch is not None:
                self._check_epoch(c, epoch)
            yield c
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    @staticmethod
    def _check_epoch(c, epoch: str):
        row = c.execute("SELECT epoch FROM owner WHERE singleton=1").fetchone()
        if row is None or row["epoch"] != epoch:
            raise SessionFenced("active recovery owner epoch mismatch")

    def activate_owner(self, epoch: str):
        if not isinstance(epoch, str) or len(epoch) < 16:
            raise JournalError("invalid owner epoch")
        with self.transaction() as c:
            c.execute("INSERT INTO owner(singleton,epoch) VALUES(1,?) "
                      "ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch",
                      (epoch,))
            self._event(c, epoch, "owner_fenced_and_activated", None, {})

    @staticmethod
    def _event(c, epoch, kind, game_id, details):
        c.execute("INSERT INTO events(ts,epoch,event,game_id,details) VALUES(?,?,?,?,?)",
                  (time.time(), epoch, kind, game_id, _json(details)))

    def cooldown_left(self, epoch: str, *, now: float | None = None) -> float:
        with self.transaction(epoch=epoch, write=False) as c:
            row = c.execute("SELECT until_wall FROM cooldown WHERE singleton=1").fetchone()
        if row is None:
            return 0.0
        return max(0.0, float(row["until_wall"]) - (time.time() if now is None else now))

    def establish_cooldown(self, epoch: str, seconds: float, *, cause: str,
                           now: float | None = None):
        if not isinstance(seconds, (int, float)) or not 60 <= float(seconds) <= 86400:
            raise JournalError("cooldown must last at least 60 seconds")
        reference = time.time() if now is None else now
        with self.transaction(epoch=epoch) as c:
            previous = c.execute("SELECT until_wall FROM cooldown WHERE singleton=1").fetchone()
            until = max(reference + float(seconds),
                        float(previous["until_wall"]) if previous else 0.0)
            c.execute("INSERT INTO cooldown(singleton,until_wall,cause) VALUES(1,?,?) "
                      "ON CONFLICT(singleton) DO UPDATE SET "
                      "until_wall=excluded.until_wall,cause=excluded.cause", (until, cause))
            self._event(c, epoch, "cooldown", None, {"until_wall": until, "cause": cause})

    def observe(self, epoch: str, game_id: str, state: dict) -> str:
        """Accept a server snapshot only if it preserves known history."""
        if not isinstance(state, dict) or "moves" not in state:
            raise JournalError("authoritative snapshot missing moves")
        try:
            latest = canonical_history(state["moves"])
        except ReconciliationError as exc:
            raise JournalError(str(exc)) from exc
        if not game_id or not isinstance(game_id, str):
            raise JournalError("game id missing")
        terminal = state.get("status") not in (None, "started")
        with self.transaction(epoch=epoch) as c:
            previous = c.execute(
                "SELECT history_json,terminal FROM games WHERE game_id=?", (game_id,)
            ).fetchone()
            prior = tuple(json.loads(previous["history_json"])) if previous else ()
            if previous and previous["terminal"] and not terminal:
                raise JournalError("terminal game cannot become active again")
            pending = c.execute(
                "SELECT * FROM intents WHERE game_id=? AND status!= 'CONFIRMED' "
                "ORDER BY ply_before LIMIT 1", (game_id,)
            ).fetchone()
            try:
                decision = classify_snapshot(
                    previous_history=prior,
                    incoming_history=latest,
                    intent_history=tuple(json.loads(pending["history_json"])) if pending else None,
                    intent_move=pending["move"] if pending else None,
                )
            except ReconciliationError as exc:
                raise JournalError(str(exc)) from exc
            c.execute("INSERT INTO games(game_id,history_json,terminal) VALUES(?,?,?) "
                      "ON CONFLICT(game_id) DO UPDATE SET "
                      "history_json=excluded.history_json,terminal=excluded.terminal",
                      (game_id, _json(latest), int(terminal)))
            if pending is not None and decision.outcome == "SERVER_CONFIRMED":
                if pending["status"] == "PREPARED":
                    raise JournalError("server move cannot confirm intent never submitted")
                c.execute("UPDATE intents SET status='CONFIRMED' "
                          "WHERE game_id=? AND ply_before=?",
                          (game_id, pending["ply_before"]))
                self._event(c, epoch, "server_confirmed", game_id,
                            {"ply_before": pending["ply_before"], "move": pending["move"]})
            self._event(c, epoch, "server_snapshot", game_id,
                        {"moves": list(latest), "outcome": decision.outcome, "terminal": terminal})
            return decision.outcome

    def assert_ready(self, epoch: str, game_id: str, history: tuple[str, ...]):
        normalized = canonical_history(history)
        with self.transaction(epoch=epoch, write=False) as c:
            row = c.execute(
                "SELECT history_json,terminal FROM games WHERE game_id=?", (game_id,)
            ).fetchone()
            if row is None or tuple(json.loads(row["history_json"])) != normalized:
                raise MoveUnresolved("no current authoritative history for move")
            if row["terminal"]:
                raise MoveUnresolved("game is terminal")
            pending = c.execute(
                "SELECT 1 FROM intents WHERE game_id=? AND status!='CONFIRMED' LIMIT 1",
                (game_id,)
            ).fetchone()
            if pending is not None:
                raise MoveUnresolved("unresolved submission prevents another move")

    def prepare(self, epoch: str, game_id: str, history, move: str) -> str:
        before = canonical_history(history)
        pos = position_id(before)
        with self.transaction(epoch=epoch) as c:
            observed = c.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            if observed is None or tuple(json.loads(observed["history_json"])) != before or observed["terminal"]:
                raise MoveUnresolved("move does not match current authoritative position")
            if c.execute("SELECT 1 FROM intents WHERE game_id=? AND status!='CONFIRMED'",
                         (game_id,)).fetchone():
                raise MoveUnresolved("an earlier move submission is not server-confirmed")
            if c.execute("SELECT 1 FROM intents WHERE game_id=? AND ply_before=?",
                         (game_id, len(before))).fetchone():
                raise MoveUnresolved("position already has a recorded move intent")
            c.execute("INSERT INTO intents(game_id,ply_before,history_json,position_id,move,status,epoch) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (game_id, len(before), _json(before), pos, move, "PREPARED", epoch))
            self._event(c, epoch, "move_prepared", game_id,
                        {"ply_before": len(before), "position_id": pos, "move": move})
        return pos

    def change_status(self, epoch: str, game_id: str, uci: str,
                      *, before: tuple[str, ...], after: str, allowed: tuple[str, ...]):
        with self.transaction(epoch=epoch) as c:
            row = c.execute(
                "SELECT status,move,history_json FROM intents "
                "WHERE game_id=? AND ply_before=?",
                (game_id, len(before))
            ).fetchone()
            if row is None or row["move"] != uci or tuple(json.loads(row["history_json"])) != tuple(before):
                raise MoveUnresolved("no matching durable intent for attempted move")
            if row["status"] == "CONFIRMED" and after in ("ACK_OBSERVED", "UNKNOWN_ACK"):
                return
            if row["status"] not in allowed:
                raise MoveUnresolved("unsafe move state transition: " + row["status"] + "->" + after)
            c.execute("UPDATE intents SET status=? WHERE game_id=? AND ply_before=?",
                      (after, game_id, len(before)))
            self._event(c, epoch, "intent_"+after.lower(), game_id,
                        {"move": uci, "ply_before": len(before)})

    def intent(self, epoch: str, game_id: str, *, before=None):
        with self.transaction(epoch=epoch, write=False) as c:
            if before is None:
                row = c.execute("SELECT * FROM intents WHERE game_id=? "
                                "ORDER BY ply_before DESC LIMIT 1", (game_id,)).fetchone()
            else:
                row = c.execute("SELECT * FROM intents WHERE game_id=? AND ply_before=?",
                                (game_id, len(before))).fetchone()
            return dict(row) if row else None

    def snapshot(self) -> dict:
        with self.transaction(write=False) as c:
            return {
                "owner_epoch": (c.execute("SELECT epoch FROM owner WHERE singleton=1").fetchone() or [None])[0],
                "games": [dict(r) for r in c.execute("SELECT * FROM games ORDER BY game_id")],
                "intents": [dict(r) for r in c.execute("SELECT * FROM intents ORDER BY game_id,ply_before")],
                "cooldown": [dict(r) for r in c.execute("SELECT * FROM cooldown")],
                "events": [dict(r) for r in c.execute("SELECT * FROM events ORDER BY event_id")],
            }
