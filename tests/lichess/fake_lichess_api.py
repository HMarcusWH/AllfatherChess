"""Local NDJSON/HTTP simulator used against the *real* pinned lichess-bot.

The server is deliberately not an engine. python-chess independently adjudicates
moves and finishes, while the real bridge transports Allfather's UCI choices.
"""
from __future__ import annotations
import io
import json
import queue
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.pgn

from tests.lichess.scripted_opponent import choose

TOKEN = "offline-fixture-token"
GAME_ID = "off3b001"

class FakeLichess:
    def __init__(self, color: str):
        if color not in ("white", "black"):
            raise ValueError("color must be white or black")
        self.color = color
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

            def respond(self, status: int, obj: dict):
                data = json.dumps(obj, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

            def stream(self, channel: queue.Queue, ready: threading.Event, first: dict | None = None):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                ready.set()
                try:
                    if first is not None:
                        self.chunk(first)
                    while not server.shutdown.is_set():
                        try:
                            payload = channel.get(timeout=0.5)
                        except queue.Empty:
                            payload = None
                        self.chunk(payload)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def chunk(self, payload):
                data = (json.dumps(payload, separators=(",", ":")) + "\n").encode() if payload is not None else b"\n"
                self.wfile.write(("%x\r\n" % len(data)).encode() + data + b"\r\n")
                self.wfile.flush()

            def auth(self) -> bool:
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    self.respond(401, {"error": "synthetic token required"})
                    return False
                return True

            def record(self, kind: str, path: str, status: int):
                with server.lock:
                    server.requests.append({
                        "monotonic_ms": round((time.monotonic() - server.start_time) * 1000),
                        "method": kind, "path": path, "status": status
                    })

            def do_GET(self):
                if not self.auth():
                    return
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/stream/event":
                    self.record("GET", path, 200)
                    return self.stream(server.events, server.event_stream_open)
                if path == "/api/bot/game/stream/" + GAME_ID:
                    with server.lock:
                        full = server.full_state()
                    self.record("GET", path, 200)
                    self.stream(server.states, server.game_stream_open, first=full)
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
                path = urllib.parse.urlparse(self.path).path
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
                        server.events.put({"type": "gameStart", "game": {
                            "id": GAME_ID, "gameId": GAME_ID,
                            "color": server.color,
                            "opponent": {"username": "approved_test_bot"},
                            "speed": "rapid", "rated": False,
                            "variant": {"key": "standard", "name": "Standard"},
                            "status": {"id": 20, "name": "started"}
                        }})
                    self.record("POST", path, 200)
                    return self.respond(200, {"ok": True})
                if path.startswith("/api/challenge/") and path.endswith("/decline"):
                    self.record("POST", path, 200)
                    return self.respond(200, {"ok": True})
                prefix = "/api/bot/game/" + GAME_ID + "/move/"
                if path.startswith(prefix):
                    move_text = urllib.parse.unquote(path[len(prefix):])
                    with server.lock:
                        if not server.started or server.finished.is_set():
                            self.record("POST", path, 409)
                            return self.respond(409, {"error": "game inactive"})
                        if server.board.turn != (server.color == "white"):
                            self.record("POST", path, 409)
                            return self.respond(409, {"error": "not bot turn"})
                        try:
                            move = chess.Move.from_uci(move_text)
                        except ValueError:
                            move = None
                        if move is None or move not in server.board.legal_moves:
                            self.record("POST", path, 400)
                            return self.respond(400, {"error": "illegal move"})
                        server.apply(move, "allfather")
                        server.accepted.append({"ply": len(server.board.move_stack), "uci": move.uci()})
                        self.record("POST", path, 200)
                        if not server.finished.is_set():
                            threading.Thread(target=server.opponent_move, daemon=True).start()
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
        self.events.put({"type": "challenge", "challenge": {
            "id": GAME_ID, "rated": False, "speed": "rapid",
            "variant": {"key": "standard", "name": "Standard"},
            "perf": {"name": "Rapid"}, "challenger": challenger,
            "destUser": target, "initialFen": "startpos",
            "color": "random", "finalColor": self.color,
            "timeControl": {"type": "clock", "limit": 600, "increment": 5}
        }})

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

    def apply(self, move: chess.Move, origin: str):
        assert move in self.board.legal_moves
        now = time.monotonic()
        side = "white" if self.board.turn == chess.WHITE else "black"
        self.clock[side] = max(0, self.clock[side] - round((now - self.turn_since) * 1000))
        self.board.push(move)
        self.clock[side] += 5000
        self.turn_since = now
        outcome = self.board.outcome(claim_draw=False)
        if outcome:
            self.outcome = {
                "status": "mate" if outcome.termination == chess.Termination.CHECKMATE else "draw",
                "winner": ("white" if outcome.winner else "black") if outcome.winner is not None else None,
                "termination": outcome.termination.name
            }
        self.states.put(self.state())
        if self.outcome:
            self.finished.set()
            self.events.put({"type": "gameFinish", "game": {"id": GAME_ID, "gameId": GAME_ID}})

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
                "clock": self.clock,
            }
