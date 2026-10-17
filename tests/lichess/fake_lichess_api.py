"""Local NDJSON/HTTP simulator used against the *real* pinned lichess-bot.

The server is deliberately not an engine. python-chess independently adjudicates
moves and finishes, while the real bridge transports Allfather's UCI choices.
"""
from __future__ import annotations
import copy
import io
import json
import queue
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.pgn

from tests.lichess.scripted_opponent import choose
from tests.lichess.fault_plan import FaultScheduler
from tests.lichess.stream_sessions import StreamSessions
from tests.lichess.transport_trace import TransportTrace, state_digest
from tests.lichess.http_target_contract import (
    canonical_target, path_of, safe_audit_fields)

TOKEN = "offline-fixture-token"
GAME_ID = "off3b001"

class FakeLichess:
    def __init__(self, color: str, scenario: dict | None = None):
        if color not in ("white", "black"):
            raise ValueError("color must be white or black")
        self.color = color
        self.fault = FaultScheduler(scenario) if scenario is not None else None
        self.trace = TransportTrace() if self.fault is not None else None
        self.sessions = StreamSessions() if self.fault is not None else None
        self.previous_state = None
        self.state_predecessors: dict[str, dict] = {}
        self.board = chess.Board()
        self.lock = threading.RLock()
        self.events: queue.Queue[dict] = queue.Queue()
        self.states: queue.Queue[dict] = queue.Queue()
        self.event_stream_open = threading.Event()
        self.game_stream_open = threading.Event()
        self.finished = threading.Event()
        self.shutdown = threading.Event()
        self.requests: list[dict] = []
        self.accepted: list[dict] = []
        self.attempted_forbidden: list[str] = []
        self.challenge_accepted = False
        self.started = False
        self.start_time = time.monotonic()
        self.clock = {"white": 600000, "black": 600000}
        self.turn_since = self.start_time
        self.outcome = None
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def parse_request(self):
                # CPython normalizes a leading // in BaseHTTPRequestHandler
                # *before* do_GET/do_POST. Capture the original wire target
                # from raw_requestline to prevent normalization-based bypass.
                self._raw_http_target = ""
                try:
                    line = self.raw_requestline.decode("iso-8859-1").rstrip("\r\n")
                    parts = line.split(" ")
                    if (len(parts) == 3 and all(parts) and
                            parts[0] in ("GET", "POST") and
                            parts[2] in ("HTTP/1.0", "HTTP/1.1")):
                        self._raw_http_target = parts[1]
                except UnicodeError:
                    pass
                return super().parse_request()

            def raw_target(self) -> str:
                return getattr(self, "_raw_http_target", "")

            def respond(self, status: int, obj: dict):
                data = json.dumps(obj, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                if server.trace is not None:
                    identity = getattr(self, "_move_request_id", None)
                    server.trace.record("response_write_completed", status=status,
                                        path=urllib.parse.urlparse(self.path).path,
                                        **({"request_id": identity} if identity else {}))

            def drop_connection(self):
                """An intentional server-side close, not proof of what the client read."""
                if server.trace is not None:
                    identity = getattr(self, "_move_request_id", None)
                    server.trace.record("connection_close_requested",
                                        path=urllib.parse.urlparse(self.path).path,
                                        **({"request_id": identity} if identity else {}))
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self.connection.close()
                except OSError:
                    pass

            def emit_faultable(self, payload: dict, subscriber) -> bool:
                """Returns False if a deliberate partial chunk ends this connection."""
                typ = payload.get("type")
                endpoint = ("terminal_state" if typ == "gameState" and
                            payload.get("status") not in (None, "started")
                            else "game_state" if typ == "gameState" else None)
                action = None
                if endpoint is not None:
                    with server.lock:
                        ply = len(server.board.move_stack)
                        action = server.fault.hit(endpoint, ply=ply,
                                                  request_id=f"stream-{subscriber.sid}")
                        if action:
                            server.trace.record("fault_activated", action=action,
                                                endpoint=endpoint, sid=subscriber.sid, ply=ply)
                if action == "partial_chunk":
                    # Header deliberately promises more bytes than we send.
                    self.wfile.write(b"80\r\n{\\\"type\\\":\\\"gameState\\\"")
                    self.wfile.flush()
                    server.trace.record("stream_partial_chunk", sid=subscriber.sid)
                    self.drop_connection()
                    return False
                if action in ("delay_state", "stale_state"):
                    predecessor = (server.state_predecessors.get(state_digest(payload))
                                   if action == "stale_state" else None)
                    if action == "stale_state" and predecessor is None:
                        raise RuntimeError("fault state has no authoritative predecessor")
                    server.trace.record(
                        "stream_fault_source", sid=subscriber.sid,
                        action=action, original_sha256=state_digest(payload),
                        original_moves=payload.get("moves"),
                        predecessor_sha256=(state_digest(predecessor)
                                            if predecessor is not None else None),
                        predecessor_moves=(predecessor.get("moves")
                                           if predecessor is not None else None))
                if action == "delay_state":
                    time.sleep(0.4)  # real monotonic delay, not a game-clock mutation
                if action == "stale_state":
                    payload = copy.deepcopy(predecessor)
                if action == "wrong_game":
                    payload = dict(payload, gameId="untrusted-other-game")
                self.chunk(payload)
                if typ is not None:
                    server.trace.record("stream_emit", sid=subscriber.sid, kind=subscriber.kind,
                                        event_type=payload.get("type"),
                                        moves=payload.get("moves"),
                                        payload_sha256=state_digest(payload),
                                        fault_action=action)
                if action in ("duplicate_state", "duplicate_terminal"):
                    self.chunk(payload)
                    server.trace.record("stream_emit", sid=subscriber.sid, kind=subscriber.kind,
                                        event_type=payload.get("type"),
                                        moves=payload.get("moves"),
                                        payload_sha256=state_digest(payload),
                                        fault_action="duplicate")
                return True

            def stream(self, channel: queue.Queue | None, ready: threading.Event,
                       first: dict | None = None, subscriber=None):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                ready.set()
                partial_chunk_broken = False
                try:
                    if subscriber is None:
                        if first is not None:
                            self.chunk(first)
                        while not server.shutdown.is_set():
                            try:
                                payload = channel.get(timeout=0.5)
                            except queue.Empty:
                                payload = None
                            self.chunk(payload)
                    else:
                        while not server.shutdown.is_set() and not subscriber.ended.is_set():
                            payload = subscriber.get(timeout=0.5)
                            if payload is None:
                                self.chunk(None)
                                continue
                            if not self.emit_faultable(payload, subscriber):
                                partial_chunk_broken = True
                                break
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                finally:
                    if subscriber is not None:
                        if not partial_chunk_broken:
                            try:
                                self.wfile.write(b"0\r\n\r\n")
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                pass
                        self.close_connection = True
                        server.sessions.unsubscribe(subscriber)

            def chunk(self, payload):
                data = (json.dumps(payload, separators=(",", ":")) + "\n").encode() if payload is not None else b"\n"
                self.wfile.write(("%x\r\n" % len(data)).encode() + data + b"\r\n")
                self.wfile.flush()

            def auth(self) -> bool:
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    # Even an early rejected request remains an HTTP witness.
                    self.record(self.command, path_of(self.raw_target()), 401)
                    self.respond(401, {"error": "synthetic token required"})
                    return False
                return True

            def record(self, kind: str, path: str, status: int,
                       request_id: str | None = None):
                with server.lock:
                    row = {
                        "monotonic_ms": round((time.monotonic() - server.start_time) * 1000),
                        "method": kind, "status": status,
                        **safe_audit_fields(kind, self.raw_target()),
                    }
                    if request_id is not None:
                        row["request_id"] = request_id
                    server.requests.append(row)

            def do_GET(self):
                self._move_request_id = None  # never reuse an HTTP/1.1 request identity
                if not self.auth():
                    return
                path = path_of(self.raw_target())
                if not canonical_target("GET", self.raw_target()):
                    safe = safe_audit_fields("GET", self.raw_target())["path"]
                    self.record("GET", safe, 403)
                    server.attempted_forbidden.append("GET " + safe)
                    return self.respond(403, {"error": "offline request-target prohibited"})
                if path == "/api/stream/event":
                    self.record("GET", path, 200)
                    if server.sessions is not None:
                        sub = server.sessions.subscribe("event")
                        action = server.fault.hit("event_stream",
                                                  ply=len(server.board.move_stack),
                                                  request_id=f"stream-{sub.sid}")
                        if action:
                            server.trace.record("fault_activated", action=action,
                                                endpoint="event_stream", sid=sub.sid)
                        return self.stream(None, server.event_stream_open, subscriber=sub)
                    return self.stream(server.events, server.event_stream_open)
                if path == "/api/bot/game/stream/" + GAME_ID:
                    with server.lock:
                        full = server.full_state()
                        sub = (server.sessions.subscribe("game", initial=full)
                               if server.sessions is not None else None)
                    self.record("GET", path, 200)
                    self.stream(server.states if sub is None else None,
                                server.game_stream_open, first=full if sub is None else None,
                                subscriber=sub)
                    return
                if path == "/api/account":
                    self.record("GET", path, 200)
                    return self.respond(200, {"id": "allfatherfixture", "username": "allfatherfixture",
                                              "title": "BOT", "perfs": {"rapid": {"rating": 1500}}})
                if path == "/api/account/playing":
                    with server.lock:
                        active = [{"gameId": GAME_ID, "id": GAME_ID, "speed": "rapid",
                                   "opponent": {"username": "approved_test_bot"}}] if server.started and not server.finished.is_set() else []
                    self.record("GET", path, 200)
                    return self.respond(200, {"nowPlaying": active})
                if path == "/api/users/status":
                    self.record("GET", path, 200)
                    return self.respond(200, [{"id": "allfatherfixture", "online": True}])
                if path == "/game/export/" + GAME_ID:
                    with server.lock:
                        pgn = str(chess.pgn.Game.from_board(server.board))
                    content = pgn.encode()
                    self.record("GET", path, 200)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-chess-pgn")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                self.record("GET", path, 403)
                server.attempted_forbidden.append("GET " + path)
                return self.respond(403, {"error": "offline route prohibited"})

            def do_POST(self):
                self._move_request_id = None  # reset on every request, even on reused sockets
                if not self.auth():
                    return
                # requests.Session reuses HTTP/1.1 connections. POST bodies
                # must be consumed or the next request line is corrupted.
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.respond(400, {"error": "bad content length"})
                if length < 0 or length > 65536:
                    return self.respond(413, {"error": "body too large"})
                if length:
                    self.rfile.read(length)
                path = path_of(self.raw_target())
                if not canonical_target("POST", self.raw_target()):
                    safe = safe_audit_fields("POST", self.raw_target())["path"]
                    with server.lock:
                        server.attempted_forbidden.append("POST " + safe)
                    self.record("POST", safe, 403)
                    return self.respond(403, {"error": "offline request-target prohibited"})
                if path == "/api/token/test":
                    self.record("POST", path, 200)
                    return self.respond(200, {TOKEN: {"scopes": "bot:play"}})
                if path == "/api/challenge/" + GAME_ID + "/accept":
                    with server.lock:
                        if server.challenge_accepted:
                            self.record("POST", path, 409)
                            return self.respond(409, {"error": "duplicate accept"})
                        server.challenge_accepted = True
                        server.started = True
                        server.turn_since = time.monotonic()
                        start_event = {"type": "gameStart", "game": {
                            "id": GAME_ID, "gameId": GAME_ID,
                            "color": server.color,
                            "opponent": {"username": "approved_test_bot"},
                            "speed": "rapid", "rated": False,
                            "variant": {"key": "standard", "name": "Standard"},
                            "status": {"id": 20, "name": "started"}
                        }}
                        server.events.put(start_event)
                        if server.sessions is not None:
                            server.sessions.publish("event", start_event)
                    self.record("POST", path, 200)
                    return self.respond(200, {"ok": True})
                if path.startswith("/api/challenge/") and path.endswith("/decline"):
                    self.record("POST", path, 200)
                    return self.respond(200, {"ok": True})
                prefix = "/api/bot/game/" + GAME_ID + "/move/"
                if path.startswith(prefix):
                    move_text = urllib.parse.unquote(path[len(prefix):])
                    with server.lock:
                        ply = len(server.board.move_stack)
                        request_id = None
                        if server.trace is not None:
                            seq = server.trace.record("move_attempt", path=path, uci=move_text,
                                                      game_id=GAME_ID, ply_at_receipt=ply)
                            request_id = f"request-{seq}"
                            self._move_request_id = request_id
                        action = (server.fault.hit("move_post", ply=ply, request_id=request_id)
                                  if server.fault is not None else None)
                        if action:
                            server.trace.record("fault_activated", action=action,
                                                endpoint="move_post", request_id=request_id,
                                                ply=ply)
                        if action == "drop_before_commit":
                            self.record("POST", path, 0, request_id=request_id)
                            return self.drop_connection()
                        if action == "rate_limit_429":
                            self.record("POST", path, 429, request_id=request_id)
                            return self.respond(429, {"error": "fault-injected rate limit"})
                        if not server.started or server.finished.is_set():
                            self.record("POST", path, 409, request_id=request_id)
                            return self.respond(409, {"error": "game inactive"})
                        if server.board.turn != (server.color == "white"):
                            self.record("POST", path, 409, request_id=request_id)
                            return self.respond(409, {"error": "not bot turn"})
                        try:
                            move = chess.Move.from_uci(move_text)
                        except ValueError:
                            move = None
                        if move is None or move not in server.board.legal_moves:
                            self.record("POST", path, 400, request_id=request_id)
                            return self.respond(400, {"error": "illegal move"})
                        if action == "low_clock_close":
                            side = "white" if server.board.turn else "black"
                            server.trace.record("synthetic_clock_override", side=side,
                                                milliseconds=1500)
                        server.apply(move, "allfather", request_id=request_id,
                                     forced_clock_after_ms=(1500 if action == "low_clock_close"
                                                            else None))
                        server.accepted.append({"ply": len(server.board.move_stack), "uci": move.uci()})
                        self.record("POST", path, 200 if action != "commit_then_504" else 504,
                                    request_id=request_id)
                        if not server.finished.is_set():
                            threading.Thread(target=server.opponent_move, daemon=True).start()
                    if action in ("commit_then_close", "low_clock_close", "terminate_bridge"):
                        return self.drop_connection()
                    if action == "commit_then_504":
                        return self.respond(504, {"error": "fault-injected gateway timeout"})
                    return self.respond(200, {"ok": True})
                # A prohibited operation must be observed but never accepted.
                with server.lock:
                    server.attempted_forbidden.append("POST " + path)
                self.record("POST", path, 403)
                return self.respond(403, {"error": "offline operation prohibited"})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.url = "http://127.0.0.1:%d/" % self.http.server_address[1]

    def start(self):
        self.thread.start()

    def issue_challenge(self):
        challenger = {"id": "approved_test_bot", "name": "approved_test_bot",
                      "rating": 400, "title": "BOT"}
        target = {"id": "allfatherfixture", "name": "allfatherfixture",
                  "title": "BOT", "rating": 1500}
        challenge = {"type": "challenge", "challenge": {
            "id": GAME_ID, "rated": False, "speed": "rapid",
            "variant": {"key": "standard", "name": "Standard"},
            "perf": {"name": "Rapid"}, "challenger": challenger,
            "destUser": target, "initialFen": "startpos",
            "color": "random", "finalColor": self.color,
            "timeControl": {"type": "clock", "limit": 600, "increment": 5}
        }}
        self.events.put(challenge)
        if self.sessions is not None:
            self.sessions.publish("event", challenge)

    def full_state(self):
        white = {"id": "allfatherfixture", "name": "allfatherfixture", "title": "BOT", "rating": 1500}
        black = {"id": "approved_test_bot", "name": "approved_test_bot", "title": "BOT", "rating": 400}
        if self.color == "black":
            white, black = black, white
        return {
            "type": "gameFull", "id": GAME_ID,
            "rated": False, "variant": {"key": "standard", "name": "Standard"},
            "speed": "rapid", "perf": {"name": "Rapid"},
            "createdAt": int(time.time() * 1000),
            "white": white, "black": black, "initialFen": "startpos",
            "clock": {"initial": 600000, "increment": 5000},
            "state": self.state()
        }

    def state(self):
        result = {
            "type": "gameState", "moves": " ".join(m.uci() for m in self.board.move_stack),
            "wtime": max(0, self.clock["white"]),
            "btime": max(0, self.clock["black"]),
            "winc": 5000, "binc": 5000,
            "status": "started" if self.outcome is None else self.outcome["status"]
        }
        if self.outcome and self.outcome["winner"]:
            result["winner"] = self.outcome["winner"]
        return result

    def apply(self, move: chess.Move, origin: str, request_id: str | None = None,
              forced_clock_after_ms: int | None = None):
        assert move in self.board.legal_moves
        before_fen = self.board.fen()
        self.previous_state = copy.deepcopy(self.state())
        now = time.monotonic()
        side = "white" if self.board.turn == chess.WHITE else "black"
        self.clock[side] = max(0, self.clock[side] - round((now - self.turn_since) * 1000))
        self.board.push(move)
        if self.trace is not None:
            self.trace.record("board_commit", game_id=GAME_ID, origin=origin,
                              request_id=request_id, uci=move.uci(),
                              ply_before=len(self.board.move_stack)-1,
                              ply_after=len(self.board.move_stack),
                              fen_before=before_fen, fen_after=self.board.fen())
        self.clock[side] += 5000
        if forced_clock_after_ms is not None:
            self.clock[side] = forced_clock_after_ms
        self.turn_since = now
        outcome = self.board.outcome(claim_draw=False)
        if outcome:
            self.outcome = {
                "status": "mate" if outcome.termination == chess.Termination.CHECKMATE else "draw",
                "winner": ("white" if outcome.winner else "black") if outcome.winner is not None else None,
                "termination": outcome.termination.name
            }
        state = self.state()
        if self.trace is not None and self.previous_state is not None:
            self.state_predecessors[state_digest(state)] = copy.deepcopy(self.previous_state)
        self.states.put(state)
        if self.sessions is not None:
            self.sessions.publish("game", state)
        if self.outcome:
            self.finished.set()
            event = {"type": "gameFinish", "game": {"id": GAME_ID, "gameId": GAME_ID}}
            self.events.put(event)
            if self.sessions is not None:
                self.sessions.publish("event", event)

    def opponent_move(self):
        time.sleep(0.02)
        with self.lock:
            if not self.started or self.finished.is_set():
                return
            if self.board.turn == (self.color == "white"):
                return
            self.apply(choose(self.board), "scripted_opponent")

    def close(self):
        self.shutdown.set()
        if self.sessions is not None:
            self.sessions.stop()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def evidence(self):
        with self.lock:
            return {
                "schema_version": 1, "color": self.color,
                "game_id": GAME_ID, "challenge_accepted": self.challenge_accepted,
                "complete": self.finished.is_set(),
                "outcome": self.outcome,
                "pgn": str(chess.pgn.Game.from_board(self.board)),
                "moves": [m.uci() for m in self.board.move_stack],
                "accepted": list(self.accepted),
                "requests": list(self.requests),
                "forbidden_attempts": list(self.attempted_forbidden),
                "clock": dict(self.clock),
                "fault": None if self.fault is None else self.fault.evidence(),
                "transport_trace": [] if self.trace is None else self.trace.snapshot(),
                "stream_sessions": [] if self.sessions is None else self.sessions.snapshot(),
            }
