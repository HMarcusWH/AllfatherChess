"""Offline adversarial gateway protocol tests. No real token or Lichess traffic."""
from __future__ import annotations
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import requests

from deploy.lichess.live_gateway import (
    LiveGateway,GatewayFailure,VerifiedBot,_cooldown_seconds)
from deploy.lichess.live_gateway_state import GatewayState,GatewaySuspended
from deploy.lichess.live_gateway_policy import AdmittedRoute,CanarySession
from deploy.lichess.move_journal import MoveJournal,MoveUnresolved

GAME="off3b001"
EPOCH="3"*32
FAKE_TOKEN="synthetic-fixture-real-token-not-secret"

class Response:
    def __init__(self,status=200,ctype="application/json",body=b'{"ok":true}',headers=None):
        self.status_code=status
        self.is_redirect=status in (301,302,303,307,308)
        self.headers={"Content-Type":ctype,**(headers or {})}
        self.content=body
        self.closed=False
    def close(self):self.closed=True
    def iter_content(self,chunk_size,decode_unicode=False):
        for offset in range(0,len(self.content),max(1,chunk_size)):
            yield self.content[offset:offset+chunk_size]

class Remote:
    def __init__(self,result):
        self.result=result
        self.calls=[]
        self.trust_env=True
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if isinstance(self.result,Exception):raise self.result
        return self.result
    def close(self):pass

class CanaryProtocolFaults(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db=Path(temporary.name)/"gateway.sqlite3"
        self.state=GatewayState(self.db,"a"*48,"allfatherfixture","approved_test_bot")
        self.route=AdmittedRoute("GET","profile","/api/account")
    def gateway(self,remote):
        g=LiveGateway.for_fake_test_only(self.state,FAKE_TOKEN,lambda:remote)
        g.verified=VerifiedBot("allfatherfixture","allfatherfixture",("bot:play",))
        g.session.token_verified=True
        return g
    def test_redirect_never_followed_and_origin_cannot_be_rewritten(self):
        remote=Remote(Response(302,headers={"Location":"http://169.254.169.254/latest"}))
        gw=self.gateway(remote)
        with self.assertRaises(GatewayFailure):
            gw.nonstream_request(self.route,b"")
        self.assertEqual(len(remote.calls),1)
        method,url,kwargs=remote.calls[0]
        self.assertEqual((method,url),("GET","https://lichess.org/api/account"))
        self.assertIs(kwargs["allow_redirects"],False)
        self.assertIs(kwargs["verify"],True)
        self.assertFalse(remote.trust_env) # the gateway explicitly disables proxy environment inheritance
        self.assertTrue(remote.result.closed)
    def test_gateway_401_403_503_suspends_and_cannot_retry_write(self):
        for status in (401,403,503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                state=GatewayState(Path(root)/"state.sqlite3","a"*48,
                                   "allfatherfixture","approved_test_bot")
                remote=Remote(Response(status))
                gw=LiveGateway.for_fake_test_only(state,FAKE_TOKEN,lambda:remote)
                gw.verified=VerifiedBot("allfatherfixture","allfatherfixture",("bot:play",))
                gw.session.token_verified=True
                with self.assertRaises(GatewaySuspended):
                    gw.nonstream_request(self.route,b"")
                self.assertEqual(len(remote.calls),1)
                self.assertTrue(state.snapshot()["suspended"])
    def test_global_429_longer_retry_after_durable_restart(self):
        remote=Remote(Response(429,headers={"Retry-After":"121"}))
        gw=self.gateway(remote)
        with self.assertRaises(GatewaySuspended):
            gw.nonstream_request(self.route,b"")
        original=self.state.snapshot()
        self.assertGreater(original["cooldown_until"],0)
        restarted=GatewayState(self.db,"a"*48,"allfatherfixture","approved_test_bot")
        with self.assertRaises(GatewaySuspended):
            restarted.require_active()
        with self.assertRaises(GatewaySuspended):
            restarted.accept_once(GAME)
        self.assertEqual(len(remote.calls),1)
    def test_tls_failure_never_retries_with_verify_false(self):
        remote=Remote(requests.exceptions.SSLError("bad test CA"))
        gw=self.gateway(remote)
        with self.assertRaises(GatewayFailure):
            gw.nonstream_request(self.route,b"")
        self.assertEqual(len(remote.calls),1)
        self.assertEqual(remote.calls[0][2]["verify"],True)
        self.assertEqual(remote.calls[0][2]["allow_redirects"],False)
    def test_malformed_json_content_type_and_token_in_response_suspended(self):
        for ctype,data in [
            ("text/html",b'{"ok":true}'),
            ("application/json",b'{broken-json'),
            ("application/json",FAKE_TOKEN.encode())]:
            with self.subTest(ctype=ctype,data=data[:9]), tempfile.TemporaryDirectory() as root:
                state=GatewayState(Path(root)/"db.sqlite3","a"*48,
                                   "allfatherfixture","approved_test_bot")
                remote=Remote(Response(200,ctype=ctype,body=data))
                gw=LiveGateway.for_fake_test_only(state,FAKE_TOKEN,lambda:remote)
                gw.verified=VerifiedBot("allfatherfixture","allfatherfixture",("bot:play",))
                gw.session.token_verified=True
                with self.assertRaises(GatewayFailure):
                    gw.nonstream_request(self.route,b"")
                self.assertTrue(state.snapshot()["suspended"])
    def test_chunked_ndjson_keepalive_crlf_and_bounded_memory(self):
        response=Response(200,ctype="application/x-ndjson",
                          body=b'\r\n{"type":"gameState","moves":"e2e4"}\r\n\n')
        lines=list(LiveGateway.bounded_ndjson(response))
        self.assertEqual(lines,[
            b"",b'{"type":"gameState","moves":"e2e4"}',b""])
        too_large=Response(body=b"a"*65537+b"\n")
        with self.assertRaises(GatewayFailure):
            list(LiveGateway.bounded_ndjson(too_large))
        partial=Response(body=b'{"type":"gameState"}')
        with self.assertRaises(GatewayFailure):
            list(LiveGateway.bounded_ndjson(partial))
    def test_simultaneous_duplicate_account_stream_fenced(self):
        gw=self.gateway(Remote(Response(200,ctype="application/x-ndjson",body=b"\n")))
        gw.stream_locks["events"].acquire()
        try:
            with self.assertRaises(GatewaySuspended):
                gw.stream_request(AdmittedRoute("GET","events","/api/stream/event"),
                                  lambda data:None)
        finally:gw.stream_locks["events"].release()
    def test_stop_closes_stream_and_releases_stream_ownership(self):
        class WaitStream(Response):
            def __init__(self):
                super().__init__(200,ctype="application/x-ndjson")
                self.entered=threading.Event()
                self.release=threading.Event()
            def iter_content(self,chunk_size,decode_unicode=False):
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError("test stream was never stopped")
                yield b"\\n"
            def close(self):
                self.release.set()
                super().close()
        response=WaitStream()
        gateway=self.gateway(Remote(response))
        results=[]
        def pump():
            try:
                gateway.stream_request(
                    AdmittedRoute("GET","events","/api/stream/event"),
                    lambda data:results.append(data))
            except Exception as exc:
                results.append(type(exc).__name__)
        thread=threading.Thread(target=pump,daemon=True)
        thread.start()
        try:
            self.assertTrue(response.entered.wait(5),"stream did not open")
            gateway.stop()
            thread.join(5)
            self.assertFalse(thread.is_alive(),"stop left an active response reader")
            self.assertTrue(response.closed)
            self.assertEqual(results,[])
            self.assertEqual(gateway.active_streams["events"],0)
            self.assertIsNone(gateway.streams["events"])
            self.assertEqual(self.state.snapshot()["suspended"],1)
        finally:
            response.release.set()
            thread.join(1)

    def test_stop_racing_response_open_does_not_publish_stream(self):
        class SlowRemote(Remote):
            def __init__(self):
                super().__init__(Response(200,ctype="application/x-ndjson",
                                          body=b"\\n"))
                self.entered=threading.Event()
                self.release=threading.Event()
            def request(self,method,url,**kwargs):
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError("test upstream open hung")
                return super().request(method,url,**kwargs)
        remote=SlowRemote()
        gateway=self.gateway(remote)
        published=[]
        failures=[]
        def run_stream():
            try:
                gateway.stream_request(
                    AdmittedRoute("GET","events","/api/stream/event"),
                    lambda data:published.append(data),
                    on_open=lambda:published.append(b"opened"))
            except Exception as exc:
                failures.append(type(exc).__name__)
        thread=threading.Thread(target=run_stream,daemon=True)
        thread.start()
        try:
            self.assertTrue(remote.entered.wait(5))
            gateway.stop()
            remote.release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(published,[],"stopped stream reported HTTP 200")
            self.assertEqual(failures,["GatewaySuspended"])
            self.assertTrue(remote.result.closed)
            self.assertEqual(gateway.active_streams["events"],0)
            self.assertIsNone(gateway.streams["events"])
        finally:
            remote.release.set()
            thread.join(1)

    def test_move_requires_journal_exact_epoch_legality_and_once(self):
        with tempfile.TemporaryDirectory() as root:
            db=Path(root)/"bridge.sqlite3"
            journal=MoveJournal(db);journal.initialize();journal.activate_owner(EPOCH)
            journal.observe(EPOCH,GAME,{"moves":"","status":"started"})
            journal.prepare(EPOCH,GAME,(),"e2e4")
            journal.change_status(EPOCH,GAME,"e2e4",before=(),after="SUBMITTING",
                                  allowed=("PREPARED",))
            self.state.bridge_journal=db
            self.state.challenge(GAME);self.state.accept_once(GAME)
            self.state.game_start(GAME);self.state.game_full(GAME,(),owner_color="white")
            self.state.game_state(GAME,(),False)
            with self.assertRaises(GatewaySuspended):
                self.state.move_once(GAME,"d2d4",0,EPOCH)
            with self.assertRaises(GatewaySuspended):
                self.state.move_once(GAME,"e2e4",0,"4"*32)
            self.state.move_once(GAME,"e2e4",0,EPOCH)
            with self.assertRaises(GatewaySuspended):
                self.state.move_once(GAME,"e2e4",0,EPOCH)
    def test_move_does_not_work_without_real_journal_even_if_header_looks_valid(self):
        self.state.challenge(GAME);self.state.accept_once(GAME)
        self.state.game_start(GAME);self.state.game_full(GAME,(),owner_color="white")
        self.state.game_state(GAME,(),False)
        with self.assertRaises(GatewaySuspended):
            self.state.move_once(GAME,"e2e4",0,EPOCH)
        self.assertEqual(self.state.snapshot()["posts"],[])

    def test_operator_stop_switch_denies_new_api_or_move(self):
        with tempfile.TemporaryDirectory() as root:
            stop=Path(root)/"stop.flag"
            state=GatewayState(Path(root)/"db.sqlite3","b"*48,
                               "allfatherfixture","approved_test_bot",
                               stop_flag=stop)
            state.challenge(GAME)
            stop.write_text("operator-requested-stop")
            with self.assertRaises(GatewaySuspended):
                state.accept_once(GAME)
            with self.assertRaises(GatewaySuspended):
                state.require_active()
            self.assertEqual(state.snapshot()["suspended"],1)

    def test_profile_drift_and_unbound_active_games_are_never_forwarded(self):
        scenarios=[
            (AdmittedRoute("GET","profile","/api/account"),
             {"id":"otherbot","username":"otherbot","title":"BOT"}),
            (AdmittedRoute("GET","playing","/api/account/playing"),
             {"nowPlaying":[{"gameId":"other001"}]}),
        ]
        for route,body in scenarios:
            with self.subTest(action=route.action), tempfile.TemporaryDirectory() as t:
                state=GatewayState(Path(t)/"session.sqlite3","a"*48,
                                   "allfatherfixture","approved_test_bot")
                response=Response(200,body=json.dumps(body).encode())
                remote=Remote(response)
                gateway=LiveGateway.for_fake_test_only(state,FAKE_TOKEN,lambda:remote)
                gateway.verified=VerifiedBot("allfatherfixture","allfatherfixture",("bot:play",))
                gateway.session.token_verified=True
                with self.assertRaises(GatewayFailure):
                    gateway.nonstream_request(route,b"")
                self.assertEqual(state.snapshot()["suspended"],1)
                self.assertEqual(len(remote.calls),1)

    def test_oversized_upstream_body_rejected_during_streaming(self):
        body=b"x"*(1048576+1)
        remote=Remote(Response(200,body=body))
        gateway=self.gateway(remote)
        with self.assertRaises(GatewayFailure):
            gateway.nonstream_request(self.route,b"")
        self.assertEqual(self.state.snapshot()["suspended"],1)
        self.assertEqual(len(remote.calls),1)

    def test_official_comma_separated_scope_and_identity_authentication(self):
        class TokenRemote(Remote):
            def __init__(self, *, scope="bot:play", uid="allfatherfixture",
                         title="BOT", expired=False, revoked=False):
                super().__init__(None)
                self.scope,self.uid,self.title=scope,uid,title
                self.expired,self.revoked=expired,revoked
            def request(self,method,url,**kwargs):
                self.calls.append((method,url,kwargs))
                if url.endswith("/api/token/test"):
                    info=(None if self.revoked else
                          {"scopes":self.scope,"userId":self.uid,
                           "expires":int(time.time()*1000)-1 if self.expired else None})
                    payload={FAKE_TOKEN:info}
                elif url.endswith("/api/account"):
                    payload={"id":"allfatherfixture","username":"allfatherfixture",
                             "title":self.title}
                elif url.endswith("/api/account/playing"):
                    payload={"nowPlaying":[]}
                else:
                    raise AssertionError("unexpected token-test remote path")
                return Response(200,body=json.dumps(payload).encode())
        for data,admit in [
            ({"scope":"challenge:read,bot:play"},True),
            ({"scope":"bot:play"},True),
            ({"scope":"bot:player"},False),
            ({"scope":""},False),
            ({"uid":"differentaccount"},False),
            ({"title":"HUMAN"},False),
            ({"expired":True},False),
            ({"revoked":True},False),
        ]:
            with self.subTest(data=data),tempfile.TemporaryDirectory() as tmp:
                state=GatewayState(Path(tmp)/"db.sqlite3","a"*48,
                                   "allfatherfixture","approved_test_bot")
                remote=TokenRemote(**data)
                gw=LiveGateway.for_fake_test_only(state,FAKE_TOKEN,lambda:remote)
                if admit:
                    self.assertEqual(gw.authenticate().username,"allfatherfixture")
                    self.assertIn("bot:play",gw.verified.scopes)
                    self.assertFalse(remote.trust_env)
                    self.assertEqual(len(remote.calls),3)
                    self.assertEqual(state.snapshot()["suspended"],0)
                else:
                    with self.assertRaises(GatewayFailure):
                        gw.authenticate()
                    self.assertTrue(state.snapshot()["suspended"])
                    self.assertLessEqual(len(remote.calls),3)

    def test_unknown_game_status_and_unsolicited_gamefinish_are_denied(self):
        gateway=self.gateway(Remote(Response()))
        self.assertFalse(gateway._game_terminal("started"))
        self.assertTrue(gateway._game_terminal("mate"))
        for status in ("futureStatus",None,"",42,True):
            with self.subTest(status=status),self.assertRaises(GatewayFailure):
                gateway._game_terminal(status)
        gateway.session.game_id=GAME
        with self.assertRaises(GatewayFailure):
            gateway._on_ndjson("events",{"type":"gameFinish",
                            "game":{"id":"other001"}},first=False)

    def test_unapproved_incoming_challenge_filtered_before_bridge(self):
        gateway=self.gateway(Remote(Response()))
        event={"type":"challenge","challenge":{
            "id":GAME,"rated":True,"speed":"rapid","initialFen":"startpos",
            "variant":{"key":"standard"},
            "challenger":{"name":"approved_test_bot","title":"BOT"},
            "destUser":{"name":"allfatherfixture","title":"BOT"},
            "timeControl":{"type":"clock","limit":600,"increment":5}}}
        self.assertIs(gateway._on_ndjson("events",event,first=False),False)
        self.assertIsNone(self.state.snapshot()["challenge"])

if __name__=="__main__":
    unittest.main()
