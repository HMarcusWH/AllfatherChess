"""Durable account-wide gateway admission. SQLite is not an approval source.

The gateway and bridge retain separate journals. Every irreversible API request is
spent BEFORE network transmission, so an uncertain response can never be retried.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import chess
from pathlib import Path

class GatewaySuspended(RuntimeError):
    pass

class GatewayState:
    def __init__(self, path: Path, nonce: str, owner: str, opponent: str,
                 *, bridge_journal: Path | None = None):
        self.path=Path(path)
        # A configured read-only mount is required for *every* live move.
        # The synthetic bearer and caller-supplied epoch are not move authority.
        self.bridge_journal=Path(bridge_journal) if bridge_journal else None
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise GatewaySuspended("gateway persistence path linked")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with self._db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""CREATE TABLE IF NOT EXISTS binding (
                id INTEGER PRIMARY KEY CHECK(id=1), nonce TEXT NOT NULL,
                owner TEXT NOT NULL, opponent TEXT NOT NULL,
                challenge TEXT, accepted INTEGER NOT NULL DEFAULT 0,
                game TEXT, game_full INTEGER NOT NULL DEFAULT 0,
                terminal INTEGER NOT NULL DEFAULT 0,
                suspended INTEGER NOT NULL DEFAULT 0,
                cooldown_until REAL NOT NULL DEFAULT 0,
                history TEXT NOT NULL DEFAULT '[]',
                owner_color TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS posts (
                game TEXT NOT NULL, ply INTEGER NOT NULL, epoch TEXT NOT NULL,
                move TEXT NOT NULL, submitted REAL NOT NULL,
                PRIMARY KEY(game,ply))""")
            c.execute("INSERT OR IGNORE INTO binding(id,nonce,owner,opponent) VALUES(1,?,?,?)",
                      (nonce,owner.casefold(),opponent.casefold()))
            row=c.execute("SELECT nonce,owner,opponent FROM binding WHERE id=1").fetchone()
            if row != (nonce,owner.casefold(),opponent.casefold()):
                raise GatewaySuspended("already owned by another lease or identity")
    def _db(self):
        c=sqlite3.connect(self.path,timeout=5)
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA busy_timeout=5000")
        return c
    def snapshot(self):
        with self.lock,self._db() as c:
            r=c.execute("SELECT nonce,owner,opponent,challenge,accepted,game,game_full,terminal,suspended,cooldown_until,history,owner_color FROM binding WHERE id=1").fetchone()
            posts=c.execute("SELECT game,ply,epoch,move FROM posts ORDER BY ply").fetchall()
        return dict(zip(("nonce","owner","opponent","challenge","accepted","game","game_full","terminal","suspended","cooldown_until","history","owner_color"),r),posts=[dict(zip(("game","ply","epoch","move"),p)) for p in posts])
    def _mutate(self,checker,statement,args=()):
        with self.lock,self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT challenge,accepted,game,game_full,terminal,suspended,cooldown_until FROM binding WHERE id=1").fetchone()
            if row is None or row[5] or row[4]:
                raise GatewaySuspended("session suspended or consumed")
            if row[6] > time.time():
                raise GatewaySuspended("account cooldown active")
            checker(row,c)
            c.execute(statement,args)
            c.commit()
    def require_active(self):
        data=self.snapshot()
        if data["suspended"] or data["terminal"]:
            raise GatewaySuspended("gateway is fenced")
        if data["cooldown_until"]>time.time():
            raise GatewaySuspended("global account cooldown active")
        return data
    def challenge(self,cid):
        def check(row,c):
            if row[0] not in (None,cid) or row[2] is not None:
                raise GatewaySuspended("another challenge consumed the lease")
        self._mutate(check,"UPDATE binding SET challenge=? WHERE id=1",(cid,))
    def accept_once(self,cid):
        def check(row,c):
            if row[0]!=cid or row[1] or row[2] is not None:
                raise GatewaySuspended("accept already submitted or challenge unbound")
        self._mutate(check,"UPDATE binding SET accepted=1 WHERE id=1")
    def game_start(self,gid):
        def check(row,c):
            if not row[1] or row[0] is None or (row[2] is not None and row[2]!=gid):
                raise GatewaySuspended("unexpected gameStart")
        self._mutate(check,"UPDATE binding SET game=? WHERE id=1",(gid,))
    def game_full(self,gid,history: tuple[str,...], *, owner_color:str):
        """Verified authoritative gameFull binds color, including on reconnect."""
        if owner_color not in ("white","black"):
            raise GatewaySuspended("operator BOT color absent")
        with self.lock,self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT game,game_full,terminal,suspended,history,owner_color FROM binding WHERE id=1").fetchone()
            if row[0]!=gid or row[2] or row[3] or (row[5] is not None and row[5]!=owner_color):
                raise GatewaySuspended("unexpected initial gameFull identity")
            old=tuple(json.loads(row[4]))
            if len(history)<len(old) or history[:len(old)]!=old:
                c.execute("UPDATE binding SET suspended=1 WHERE id=1")
                c.commit()
                raise GatewaySuspended("reconnected gameFull history conflict")
            c.execute("UPDATE binding SET game_full=1,history=?,owner_color=? WHERE id=1",
                      (json.dumps(history,separators=(",",":")),owner_color))
            c.commit()
    def game_state(self,gid,history: tuple[str,...],terminal: bool):
        import json
        with self.lock,self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT game,game_full,terminal,suspended,history FROM binding WHERE id=1").fetchone()
            if row[0]!=gid or not row[1] or row[2] or row[3]:
                raise GatewaySuspended("gameState without verified gameFull")
            old=tuple(json.loads(row[4]))
            if len(history)<len(old) or history[:len(old)]!=old:
                c.execute("UPDATE binding SET suspended=1 WHERE id=1")
                c.commit()
                raise GatewaySuspended("authoritative moves conflict/regress")
            c.execute("UPDATE binding SET history=?,terminal=? WHERE id=1",
                      (json.dumps(history,separators=(",",":")),int(terminal)))
            c.commit()
    def _verify_bridge_journal(self,gid,uci,ply,epoch,history):
        """Independently verify the *real* SUBMITTING intent and owner epoch.

        The bridge journal is on a separate volume, mounted read-only into
        the gateway. Missing, corrupt, replaced or lagging data denies a move.
        """
        path=self.bridge_journal
        if path is None or path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise GatewaySuspended("read-only bridge move journal unavailable")
        try:
            with sqlite3.connect("file:"+str(path.resolve())+"?mode=ro",
                                 uri=True,timeout=2) as c:
                c.execute("PRAGMA query_only=ON")
                owner=c.execute("SELECT epoch FROM owner WHERE singleton=1").fetchone()
                state=c.execute("SELECT history_json,terminal FROM games WHERE game_id=?",(gid,)).fetchone()
                intent=c.execute("""SELECT history_json,move,status,epoch FROM intents
                                    WHERE game_id=? AND ply_before=?""",(gid,ply)).fetchone()
        except (sqlite3.Error,OSError,ValueError) as exc:
            raise GatewaySuspended("read-only bridge journal verification failed") from exc
        if (owner!=(epoch,) or state is None or state[1]!=0 or
            intent is None or intent[1]!=uci or intent[2]!="SUBMITTING" or intent[3]!=epoch):
            raise GatewaySuspended("no matching source-bound SUBMITTING UCI intent")
        try:
            if (tuple(json.loads(state[0]))!=history or
                tuple(json.loads(intent[0]))!=history):
                raise GatewaySuspended("bridge intent/server history mismatch")
        except (ValueError,TypeError) as exc:
            raise GatewaySuspended("invalid bridge journal history") from exc

    def move_once(self,gid,uci,ply,epoch):
        def check(row,c):
            if row[2]!=gid or not row[3] or row[4]:
                raise GatewaySuspended("game not verified or already terminal")
            record=c.execute("SELECT history,owner_color FROM binding WHERE id=1").fetchone()
            hist=tuple(json.loads(record[0]))
            if len(hist)!=ply:
                raise GatewaySuspended("prepared move ply differs from authoritative history")
            if record[1] not in ("white","black"):
                raise GatewaySuspended("bound game owner color missing")
            board=chess.Board()
            try:
                for m in hist: board.push_uci(m)
                move=chess.Move.from_uci(uci)
            except (ValueError,chess.IllegalMoveError) as exc:
                raise GatewaySuspended("malformed UCI or authoritative game state") from exc
            if move==chess.Move.null() or move not in board.legal_moves or (
                    board.turn != (record[1]=="white")):
                raise GatewaySuspended("UCI not legal for approved BOT side")
            if c.execute("SELECT 1 FROM posts WHERE game=? AND ply=?",(gid,ply)).fetchone():
                raise GatewaySuspended("same-ply POST already spent; no retries")
            self._verify_bridge_journal(gid,uci,ply,epoch,hist)
            c.execute("INSERT INTO posts(game,ply,epoch,move,submitted) VALUES(?,?,?,?,?)",
                      (gid,ply,epoch,uci,time.time()))
        self._mutate(check,"UPDATE binding SET game=game WHERE id=1")
    def cooldown(self,seconds,reason="http429"):
        if not 60<=float(seconds)<=86400:
            raise GatewaySuspended("invalid cooldown")
        with self.lock,self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE binding SET cooldown_until=MAX(cooldown_until,?) WHERE id=1",
                      (time.time()+float(seconds),))
            c.commit()
    def suspend(self):
        with self.lock,self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE binding SET suspended=1 WHERE id=1")
            c.commit()
