"""Real local HTTP socket adversarial tests of restricted gateway, no live DNS."""
from __future__ import annotations
import json
import queue
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import requests
from deploy.lichess.live_gateway import LiveGateway,BoundedServer
from deploy.lichess.live_gateway_state import GatewayState
from deploy.lichess.move_journal import MoveJournal
from deploy.lichess.live_gateway_policy import SYNTHETIC_TOKEN

TEST_REAL="THIS-IS-A-FAKE-ONLY-TOKEN-LOCAL-2026"
GAME="off3b001"
OWNER="allfatherfixture"
OPP="approved_test_bot"

class FakeRemote:
    def __init__(self):
        self.calls=[]
        self.hold_event_stream_open=False
        self.malformed_event_stream=False
        self.event_stream_ready=threading.Event()
        self.accept_seen=threading.Event()
        self.release_stream=threading.Event()
        outer=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version="HTTP/1.1"
            def log_message(self,*_):pass
            def send_chunk(self,data):
                self.wfile.write(f"{len(data):x}\r\n".encode("ascii")+data+b"\r\n")
                self.wfile.flush()
            def do_POST(self):
                size=int(self.headers.get("Content-Length","0"))
                body=self.rfile.read(size)
                outer.calls.append((self.command,self.path,body,self.headers.get("Authorization")))
                if self.path=="/api/token/test":
                    content=json.dumps({TEST_REAL:{"scopes":"bot:play","userId":OWNER}}).encode()
                elif self.path.startswith("/api/challenge/") or self.path.startswith("/api/bot/game/"):
                    content=b'{"ok":true}'
                    if self.path=="/api/challenge/"+GAME+"/accept":
                        outer.accept_seen.set()
                else:
                    self.send_error(403);return
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(content)))
                self.end_headers();self.wfile.write(content)
            def do_GET(self):
                outer.calls.append((self.command,self.path,b"",self.headers.get("Authorization")))
                headers="application/json"
                if self.path=="/api/account":
                    content=json.dumps({"id":OWNER,"username":OWNER,"title":"BOT"}).encode()
                elif self.path=="/api/account/playing":
                    content=b'{"nowPlaying":[]}'
                elif self.path=="/api/stream/event":
                    headers="application/x-ndjson"
                    c={"type":"challenge","challenge":{
                        "id":GAME,"rated":False,"speed":"rapid",
                        "variant":{"key":"standard"},"initialFen":"startpos",
                        "challenger":{"name":OPP,"title":"BOT"},
                        "destUser":{"name":OWNER,"title":"BOT"},
                        "timeControl":{"type":"clock","limit":600,"increment":5}}}
                    content=b"not-json\n" if outer.malformed_event_stream else b"\n"+json.dumps(c).encode()+b"\n"
                    if outer.hold_event_stream_open:
                        self.send_response(200)
                        self.send_header("Content-Type","application/x-ndjson")
                        self.send_header("Transfer-Encoding","chunked")
                        self.end_headers()
                        try:
                            self.send_chunk(b"\n")
                            self.send_chunk(json.dumps(c).encode()+b"\n")
                            outer.event_stream_ready.set()
                            if outer.accept_seen.wait(6):
                                game={"type":"gameStart","game":{
                                    "id":GAME,"opponent":{"username":OPP}}}
                                self.send_chunk(json.dumps(game).encode()+b"\n")
                            outer.release_stream.wait(7)
                            self.wfile.write(b"0\r\n\r\n");self.wfile.flush()
                        except (OSError,BrokenPipeError,ConnectionError):
                            pass
                        return
                else:
                    self.send_error(403);return
                self.send_response(200)
                self.send_header("Content-Type",headers)
                self.send_header("Content-Length",str(len(content)))
                self.end_headers()
                self.wfile.write(content[:1]);self.wfile.flush()
                self.wfile.write(content[1:]);self.wfile.flush()
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
    def close(self):
        self.release_stream.set()
        self.server.shutdown();self.server.server_close();self.thread.join(5)

class TestSession(requests.Session):
    def __init__(self,url):
        super().__init__()
        self.trust_env=False
        self.url=url
    def request(self,method,url,**kwargs):
        if not url.startswith("https://lichess.org/api/"):
            raise RuntimeError("real API origin was not fixed")
        return super().request(method,self.url+url[len("https://lichess.org"):],**kwargs)

class RealLocalGatewayHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote=FakeRemote();self.addCleanup(self.remote.close)
        state=GatewayState(Path(self.tmp.name)/"gateway.sqlite3","a"*48,OWNER,OPP)
        self.gateway=LiveGateway.for_fake_test_only(
            state,TEST_REAL,lambda:TestSession(self.remote.url))
        self.gateway.authenticate()
        self.http=BoundedServer(("127.0.0.1",0),self.gateway)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(lambda:(self.http.shutdown(),self.http.server_close(),self.thread.join(5)))
        self.url="http://127.0.0.1:"+str(self.http.server_address[1])
    def request(self,method,path,*,token=SYNTHETIC_TOKEN,data=None,headers=None,stream=False):
        return requests.request(method,self.url+path,headers={
            "Host":"live-gateway:8765","Authorization":"Bearer "+token,
            **(headers or {})},data=data,stream=stream,timeout=8,
            allow_redirects=False)
    def test_authenticated_synthetic_keyed_token_and_exact_GET(self):
        before=len(self.remote.calls)
        response=self.request("POST","/api/token/test",data=SYNTHETIC_TOKEN)
        self.assertEqual(response.status_code,200)
        body=response.json()
        self.assertIn(SYNTHETIC_TOKEN,body)
        self.assertNotIn(TEST_REAL,json.dumps(body))
        self.assertEqual(len(self.remote.calls),before) # synthetic mapping never forwarded
        response=self.request("GET","/api/account")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["title"],"BOT")
        self.assertEqual(self.remote.calls[-1][3],"Bearer "+TEST_REAL)
    def test_forbidden_methods_paths_headers_zero_upstream(self):
        before=len(self.remote.calls)
        for method,path in [
            ("POST","/api/bot/account/upgrade"),
            ("GET","/api/board/game/illegal"),
            ("POST","/api/bot/game/"+GAME+"/resign"),
            ("GET","/api/account?redirect=https://evil.test"),
            ("GET","/api/../account"),
            ("GET","/api/%2faccount"),
        ]:
            with self.subTest(path=path):
                response=self.request(method,path)
                self.assertIn(response.status_code,(403,405))
        response=self.request("GET","/api/account",token=TEST_REAL)
        self.assertEqual(response.status_code,403)
        self.assertEqual(before,len(self.remote.calls))
    def test_incremental_event_stream_with_blank_keepalive(self):
        response=self.request("GET","/api/stream/event",stream=True)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers["Content-Type"],"application/x-ndjson")
        lines=list(response.iter_lines(chunk_size=1,decode_unicode=False))
        self.assertEqual(lines[0],b"")
        payload=json.loads(lines[1])
        self.assertEqual(payload["type"],"challenge")
        self.assertEqual(self.gateway.state.snapshot()["challenge"],GAME)
        response.close()
    def test_pinned_default_iter_lines_observes_open_stream_and_game_start(self):
        # This is the pinned lichess-bot consumer's exact default call:
        # response.iter_lines(), NOT the previous chunk_size=1 approximation.
        # A still-open upstream stream must not buffer the challenge or
        # gameStart event until EOF, and acceptance must remain source-bound.
        self.remote.hold_event_stream_open=True
        response=self.request("GET","/api/stream/event",stream=True)
        received=queue.Queue()
        reader=None
        try:
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers.get("Transfer-Encoding"),"chunked")
            self.assertTrue(self.remote.event_stream_ready.wait(3))
            def consume():
                try:
                    source=response.iter_lines()  # default 512-byte chunks
                    for _ in range(3):
                        received.put(next(source))
                except Exception as exc:
                    received.put(exc)
            reader=threading.Thread(target=consume,daemon=True)
            reader.start()
            heartbeat=received.get(timeout=3)
            self.assertEqual(heartbeat,b"")
            challenge=received.get(timeout=3)
            self.assertIsInstance(challenge,bytes)
            self.assertEqual(json.loads(challenge)["type"],"challenge")
            self.assertEqual(self.gateway.state.snapshot()["challenge"],GAME)
            self.assertFalse(self.remote.release_stream.is_set())
            reply=self.request("POST","/api/challenge/"+GAME+"/accept")
            self.assertEqual(reply.status_code,200)
            self.assertTrue(self.remote.accept_seen.wait(2))
            started=received.get(timeout=3)
            self.assertIsInstance(started,bytes)
            self.assertEqual(json.loads(started)["type"],"gameStart")
            self.assertEqual(self.gateway.state.snapshot()["game"],GAME)
        finally:
            self.remote.release_stream.set()
            response.close()
            if reader is not None: reader.join(4)

    def test_midstream_upstream_error_has_no_second_http_status_or_clean_eof(self):
        self.remote.malformed_event_stream=True
        request=(b"GET /api/stream/event HTTP/1.1\r\n"
                 b"Host: live-gateway:8765\r\n"
                 b"Authorization: Bearer "+SYNTHETIC_TOKEN.encode("ascii")
                 +b"\r\nConnection: close\r\n\r\n")
        with socket.create_connection(self.http.server_address,timeout=3) as sock:
            sock.settimeout(5)
            sock.sendall(request)
            pieces=[]
            while True:
                chunk=sock.recv(4096)
                if not chunk: break
                pieces.append(chunk)
        wire=b"".join(pieces)
        self.assertEqual(wire.count(b"HTTP/1.1 "),1)
        self.assertIn(b"Transfer-Encoding: chunked",wire)
        self.assertNotIn(b"0\r\n\r\n",wire)
        self.assertTrue(self.gateway.state.snapshot()["suspended"])

    def test_operator_stop_does_not_emit_a_clean_chunked_eof(self):
        # A stop is an interruption, not a successful upstream close.
        # The only allowed wire outcome is a 200 stream terminated without
        # the zero-length chunk; it cannot become a synthetic completion.
        from unittest import mock
        def interrupted(route, writer, *, on_open):
            on_open()
            self.gateway.shutdown_event.set()
        request=(b"GET /api/stream/event HTTP/1.1\r\n"
                 b"Host: live-gateway:8765\r\n"
                 b"Authorization: Bearer "+SYNTHETIC_TOKEN.encode("ascii")
                 +b"\r\nConnection: close\r\n\r\n")
        with mock.patch.object(self.gateway,"stream_request",side_effect=interrupted):
            with socket.create_connection(self.http.server_address,timeout=3) as sock:
                sock.settimeout(5)
                sock.sendall(request)
                pieces=[]
                while True:
                    segment=sock.recv(4096)
                    if not segment: break
                    pieces.append(segment)
        wire=b"".join(pieces)
        self.assertEqual(wire.count(b"HTTP/1.1 "),1)
        self.assertIn(b"Transfer-Encoding: chunked",wire)
        self.assertNotIn(b"0\r\n\r\n",wire)

    def test_same_move_cannot_be_reposted_even_after_success(self):
        state=self.gateway.state
        state.challenge(GAME);state.accept_once(GAME);state.game_start(GAME)
        state.game_full(GAME,(),owner_color="white");state.game_state(GAME,(),False)
        journal=MoveJournal(Path(self.tmp.name)/"bridge.sqlite3")
        journal.initialize();journal.activate_owner("f"*32)
        journal.observe("f"*32,GAME,{"moves":"","status":"started"})
        journal.prepare("f"*32,GAME,(),"e2e4")
        journal.change_status("f"*32,GAME,"e2e4",before=(),
                              after="SUBMITTING",allowed=("PREPARED",))
        state.bridge_journal=journal.path
        self.gateway.session.challenge_id=GAME
        self.gateway.session.game_id=GAME
        headers={"X-Allfather-Ply":"0","X-Allfather-Epoch":"f"*32}
        path=f"/api/bot/game/{GAME}/move/e2e4?offeringDraw=false"
        first=self.request("POST",path,headers=headers)
        self.assertEqual(first.status_code,200)
        second=self.request("POST",path,headers=headers)
        self.assertEqual(second.status_code,403)
        remote=[x for x in self.remote.calls if "/move/" in x[1]]
        self.assertEqual(len(remote),1)
        self.assertEqual(remote[0][1],f"/api/bot/game/{GAME}/move/e2e4")
        bad=self.request("POST",f"/api/bot/game/{GAME}/move/d2d4?offeringDraw=true",
                         headers=headers)
        self.assertEqual(bad.status_code,403)
        self.assertEqual(len(remote),1)

if __name__=="__main__":unittest.main()
