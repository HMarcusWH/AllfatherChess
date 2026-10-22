"""Real local HTTP socket adversarial tests of restricted gateway, no live DNS."""
from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import requests
from deploy.lichess.live_gateway import LiveGateway,BoundedServer
from deploy.lichess.live_gateway_state import GatewayState
from deploy.lichess.live_gateway_policy import SYNTHETIC_TOKEN

TEST_REAL="THIS-IS-A-FAKE-ONLY-TOKEN-LOCAL-2026"
GAME="off3b001"
OWNER="allfatherfixture"
OPP="approved_test_bot"

class FakeRemote:
    def __init__(self):
        self.calls=[]
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                size=int(self.headers.get("Content-Length","0"))
                body=self.rfile.read(size)
                outer.calls.append((self.command,self.path,body,self.headers.get("Authorization")))
                if self.path=="/api/token/test":
                    content=json.dumps({TEST_REAL:{"scopes":"bot:play","userId":OWNER}}).encode()
                elif self.path.startswith("/api/challenge/") or self.path.startswith("/api/bot/game/"):
                    content=b'{"ok":true}'
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
                    content=b"\n"+json.dumps(c).encode()+b"\n"
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
    def test_same_move_cannot_be_reposted_even_after_success(self):
        state=self.gateway.state
        state.challenge(GAME);state.accept_once(GAME);state.game_start(GAME)
        state.game_full(GAME,());state.game_state(GAME,(),False)
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
