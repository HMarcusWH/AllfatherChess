"""Offline adversarial gateway protocol tests. No real token or Lichess traffic."""
from __future__ import annotations
import json
import tempfile
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
        self.assertFalse(remote.trust_env is False) # remote stub retains default
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

if __name__=="__main__":
    unittest.main()
