"""Networkless negative controls for exact routed gateway; fake Bot API only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deploy.lichess.live_gateway_policy import (
    CanarySession,admit_route,RouteDenied,validate_challenge,validate_game_full)
from deploy.lichess.live_gateway_state import GatewayState,GatewaySuspended
from deploy.lichess.move_journal import MoveJournal
from deploy.lichess.live_gateway import LiveGateway,VerifiedBot,GatewayFailure,_cooldown_seconds

GAME="off3b001"
TOKEN="offline-fixture-token"
HEADER={"Host":"live-gateway:8765","Authorization":"Bearer "+TOKEN}
def route(method,path,session=None,body=b"",**extra):
    s=session or CanarySession("allfatherfixture","approved_test_bot",GAME,GAME,False,True)
    return admit_route(method,path.encode(),{**HEADER,**extra},body,s)

class GatewayContractTests(unittest.TestCase):
    def test_exact_permitted_route_families(self):
        for path,method in [("/api/account","GET"),("/api/account/playing","GET"),
                            ("/api/stream/event","GET"),
                            (f"/api/bot/game/stream/{GAME}","GET"),
                            (f"/api/challenge/{GAME}/accept","POST")]:
            with self.subTest(path=path): self.assertIsNotNone(route(method,path))
        self.assertEqual(route("POST",f"/api/bot/game/{GAME}/move/e2e4?offeringDraw=false",
                               **{"X-Allfather-Ply":"0","X-Allfather-Epoch":"f"*32}).uci,"e2e4")
        self.assertEqual(route("POST","/api/token/test",body=TOKEN.encode()).action,"token_test")
        pregame=CanarySession("allfatherfixture","approved_test_bot",GAME,None,False,True)
        self.assertEqual(route("POST","/api/challenge/aabbccdd/decline",session=pregame,
                               body=b"reason=generic",
                               **{"Content-Type":"application/x-www-form-urlencoded"}).action,"decline")
    def test_forbidden_methods_paths_queries_and_headers(self):
        bad=[
            ("POST","/api/bot/account/upgrade",{}),
            ("POST",f"/api/bot/game/{GAME}/resign",{}),
            ("POST",f"/api/bot/game/{GAME}/abort",{}),
            ("POST",f"/api/bot/game/{GAME}/chat",{}),
            ("POST","/api/challenge/newaccount",{}),
            ("GET","/api/board/game/abc",{}),
            ("GET","/api/cloud-eval?fen=abc",{}),
            ("GET","https://lichess.org/api/account",{}),
            ("GET","//api/account",{}),
            ("GET","/api/%2e%2e/account",{}),
            ("GET","/api/a/../account",{}),
            ("GET","/api/account?foo=1",{}),
            ("POST",f"/api/bot/game/{GAME}/move/e2e4?offeringDraw=true",
             {"X-Allfather-Ply":"0","X-Allfather-Epoch":"f"*32}),
            ("CONNECT","/api/account",{}),
            ("GET","/api/account",{"Host":"evil.example"}),
            ("GET","/api/account",{"Forwarded":"host=lichess.org"}),
            ("GET","/api/account",{"Proxy-Authorization":"Bearer token"}),
            ("GET","/api/account",{"Authorization":"Bearer other"}),
            ("GET","/api/account",{"X-Rewrite-Url":"/api/account"}),
        ]
        for method,target,headers in bad:
            with self.subTest(target=target,headers=headers):
                with self.assertRaises(RouteDenied):
                    route(method,target,**headers)
    def test_game_id_epoch_intent_and_body(self):
        for kwargs in ({"X-Allfather-Ply":"-1","X-Allfather-Epoch":"f"*32},
                       {"X-Allfather-Ply":"0","X-Allfather-Epoch":"bad"},
                       {"X-Allfather-Ply":"0"}):
            with self.assertRaises(RouteDenied):
                route("POST",f"/api/bot/game/{GAME}/move/e2e4",**kwargs)
        with self.assertRaises(RouteDenied):
            route("POST","/api/token/test",body=b"real-token")
        with self.assertRaises(RouteDenied):
            route("GET","/api/account",body=b"evil")
        with self.assertRaises(RouteDenied):
            route("POST",f"/api/challenge/{GAME}/accept",body=b"evil")
        with self.assertRaises(RouteDenied):
            route("POST",f"/api/bot/game/aaaaaaaa/move/e2e4",
                  **{"X-Allfather-Ply":"0","X-Allfather-Epoch":"f"*32})
    def test_live_without_verified_session_denied(self):
        s=CanarySession("allfatherfixture","approved_test_bot")
        with self.assertRaises(RouteDenied):route("GET","/api/account",session=s)
        s.token_verified=True;s.stopped=True
        with self.assertRaises(RouteDenied):route("GET","/api/account",session=s)
    def test_unchanged_chess_shape_required(self):
        c={"type":"challenge","challenge":{
            "id":GAME,"rated":False,"speed":"rapid","initialFen":"startpos",
            "variant":{"key":"standard"},"timeControl":{"type":"clock","limit":600,"increment":5},
            "challenger":{"name":"approved_test_bot","title":"BOT"},
            "destUser":{"name":"allfatherfixture","title":"BOT"}}}
        self.assertEqual(validate_challenge(c,owner="allfatherfixture",opponent="approved_test_bot"),GAME)
        negatives=[
            ("rated",True),("speed","blitz"),("initialFen","fen"),
            ("challenger",{"name":"human","title":None}),
            ("timeControl",{"type":"clock","limit":300,"increment":0}),
            ("variant",{"key":"chess960"}),
        ]
        for key,value in negatives:
            copied=copy.deepcopy(c);copied["challenge"][key]=value
            with self.assertRaises(RouteDenied):
                validate_challenge(copied,owner="allfatherfixture",opponent="approved_test_bot")
        game={"type":"gameFull","id":GAME,"rated":False,"speed":"rapid",
              "initialFen":"startpos","variant":{"key":"standard"},
              "clock":{"initial":600000,"increment":5000},
              "white":{"name":"allfatherfixture","title":"BOT"},
              "black":{"name":"approved_test_bot","title":"BOT"},
              "state":{"type":"gameState","moves":"","status":"started"}}
        self.assertEqual(validate_game_full(game,owner="allfatherfixture",
                                            opponent="approved_test_bot",game_id=GAME)["moves"],"")
        game["black"]={"name":"human","title":None}
        with self.assertRaises(RouteDenied):
            validate_game_full(game,owner="allfatherfixture",opponent="approved_test_bot",game_id=GAME)
    def test_rate_limit_floor(self):
        self.assertEqual(_cooldown_seconds(None),60)
        self.assertEqual(_cooldown_seconds("5"),60)
        self.assertEqual(_cooldown_seconds("75"),75)
        self.assertEqual(_cooldown_seconds("inf"),60)
    def test_persisted_once_only_moves_and_global_cooldown(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/"gateway.sqlite3"
            journal=MoveJournal(Path(t)/"bridge.sqlite3")
            journal.initialize();journal.activate_owner("b"*32)
            journal.observe("b"*32,GAME,{"moves":"","status":"started"})
            journal.prepare("b"*32,GAME,(),"e2e4")
            journal.change_status("b"*32,GAME,"e2e4",before=(),
                                  after="SUBMITTING",allowed=("PREPARED",))
            s=GatewayState(path,"a"*48,"allfatherfixture","approved_test_bot",
                           bridge_journal=journal.path)
            s.challenge(GAME);s.accept_once(GAME);s.game_start(GAME)
            s.game_full(GAME,(),owner_color="white")
            s.game_state(GAME,(),False)
            s.move_once(GAME,"e2e4",0,"b"*32)
            with self.assertRaises(GatewaySuspended):
                s.move_once(GAME,"e2e4",0,"b"*32)
            s.cooldown(60)
            again=GatewayState(path,"a"*48,"allfatherfixture","approved_test_bot")
            with self.assertRaises(GatewaySuspended):
                again.require_active()
            with self.assertRaises(GatewaySuspended):
                again.challenge("zzzz9999")
    def test_wrong_game_start_does_not_bind_another_game(self):
        with tempfile.TemporaryDirectory() as t:
            session=GatewayState(Path(t)/"gateway.sqlite3","a"*48,
                                 "allfatherfixture","approved_test_bot")
            session.challenge(GAME)
            session.accept_once(GAME)
            with self.assertRaises(GatewaySuspended):
                session.game_start("other001")
            self.assertIsNone(session.snapshot()["game"])
            session.game_start(GAME)
            self.assertEqual(session.snapshot()["game"],GAME)

    def test_unknown_server_history_and_reconnect(self):
        with tempfile.TemporaryDirectory() as t:
            s=GatewayState(Path(t)/"state.sqlite3","a"*48,"allfatherfixture","approved_test_bot")
            s.challenge(GAME);s.accept_once(GAME);s.game_start(GAME)
            s.game_full(GAME,(),owner_color="white")
            s.game_state(GAME,("e2e4",),False)
            s.game_full(GAME,("e2e4",),owner_color="white")
            with self.assertRaises(GatewaySuspended):
                s.game_full(GAME,("d2d4",),owner_color="white")
            self.assertEqual(s.snapshot()["suspended"],1)
    def test_real_secret_never_in_synthetic_token_reply(self):
        with tempfile.TemporaryDirectory() as t:
            state=GatewayState(Path(t)/"state.sqlite3","a"*48,
                               "allfatherfixture","approved_test_bot")
            gw=LiveGateway.for_fake_test_only(state,"never-show-me",
                                              session_factory=lambda:None)
            gw.verified=VerifiedBot("allfatherfixture","allfatherfixture",("bot:play",))
            actual=gw.synthetic_token_test(TOKEN.encode())
            self.assertIn(TOKEN.encode(),actual)
            self.assertNotIn(b"never-show-me",actual)
            with self.assertRaises(GatewayFailure):
                gw.synthetic_token_test(b"never-show-me")

if __name__=="__main__":unittest.main()
