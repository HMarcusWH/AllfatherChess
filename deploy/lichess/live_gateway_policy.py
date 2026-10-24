"""Exact ingress grammar of ONLINE-PLAY-1; this module has no network access.

Every GET and POST is checked, including long-lived upstream client GETs.
It never parses arbitrary remote URLs or applies path normalization.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Mapping

SYNTHETIC_TOKEN = "offline-fixture-token"
GATEWAY_HOST = "live-gateway:8765"
GATEWAY_URL = "http://" + GATEWAY_HOST + "/"
LICHESS_ORIGIN = "https://lichess.org"
ID = re.compile(r"[A-Za-z0-9]{8}")
UCI = re.compile(r"(?:[a-h][1-8]){2}[qrbn]?")
FORBIDDEN_HEADERS = {"forwarded","x-forwarded-for","x-forwarded-host","x-forwarded-proto",
                     "proxy-authorization","proxy-connection","cookie","upgrade",
                     "transfer-encoding","te","trailer","x-original-url","x-rewrite-url",
                     "x-forwarded-url"}
SAFE_HEADERS = {"host","authorization","accept","accept-encoding","user-agent",
                "connection","content-type","content-length","x-allfather-ply",
                "x-allfather-epoch"}
FIXED_ROUTES = {
    ("POST","/api/token/test"):"token_test",
    ("GET","/api/account"):"profile",
    ("GET","/api/account/playing"):"playing",
    ("GET","/api/stream/event"):"events",
}
class RouteDenied(ValueError):
    pass

@dataclass(frozen=True)
class AdmittedRoute:
    method: str
    action: str
    path: str
    game_id: str | None = None
    challenge_id: str | None = None
    uci: str | None = None
    ply: int | None = None
    epoch: str | None = None

@dataclass
class CanarySession:
    owner_bot: str
    opponent_bot: str
    challenge_id: str | None = None
    game_id: str | None = None
    stopped: bool = False
    token_verified: bool = False

def _headers(raw: Mapping[str,str]) -> dict[str,str]:
    result={}
    for key,value in raw.items():
        if not isinstance(key,str) or not re.fullmatch("[A-Za-z0-9-]+",key):
            raise RouteDenied("invalid HTTP header")
        norm=key.lower()
        if norm in result or norm in FORBIDDEN_HEADERS or norm not in SAFE_HEADERS:
            raise RouteDenied("unexpected/duplicate HTTP header")
        if not isinstance(value,str) or any(c in value for c in "\r\n\x00") or len(value)>512:
            raise RouteDenied("unsafe header value")
        result[norm]=value
    if result.get("host")!=GATEWAY_HOST:
        raise RouteDenied("host mismatch")
    if result.get("authorization")!="Bearer "+SYNTHETIC_TOKEN:
        raise RouteDenied("bridge credential mismatch")
    if result.get("connection","").lower() not in ("","keep-alive","close"):
        raise RouteDenied("hop-by-hop request forbidden")
    return result

def admit_route(method: str, raw_target: bytes, headers: Mapping[str,str],
                body: bytes, session: CanarySession) -> AdmittedRoute:
    if session.stopped or not session.token_verified:
        raise RouteDenied("session not admitted")
    if method not in ("GET","POST"):
        raise RouteDenied("method not permitted")
    if not isinstance(raw_target,bytes) or len(raw_target)>256:
        raise RouteDenied("target size")
    # No query is required by this controlled client; this also denies offeringDraw.
    if not re.fullmatch(rb"/[A-Za-z0-9/_-]+(?:\?offeringDraw=false)?",raw_target):
        raise RouteDenied("noncanonical or ambiguous target")
    original_target=raw_target.decode("ascii")
    path,separator,query=original_target.partition("?")
    if separator and (query!="offeringDraw=false" or method!="POST"
                      or re.fullmatch(r"/api/bot/game/[A-Za-z0-9]{8}/move/[a-h][1-8][a-h][1-8][qrbn]?",path) is None):
        raise RouteDenied("unexpected query or draw offer")
    if "//" in path or "/../" in path or "/./" in path or path.endswith("/"):
        raise RouteDenied("normalized path ambiguity")
    h=_headers(headers)
    if not isinstance(body,bytes) or len(body)>128:
        raise RouteDenied("oversized request body")
    action=FIXED_ROUTES.get((method,path))
    game_id=challenge_id=uci=None
    if action is None and method=="GET":
        match=re.fullmatch(r"/api/bot/game/stream/([A-Za-z0-9]{8})",path)
        if match:
            action="game_stream";game_id=match[1]
    if action is None and method=="POST":
        match=re.fullmatch(r"/api/challenge/([A-Za-z0-9]{8})/(accept|decline)",path)
        if match:
            action=match[2];challenge_id=match[1]
        else:
            match=re.fullmatch(r"/api/bot/game/([A-Za-z0-9]{8})/move/([a-h][1-8][a-h][1-8][qrbn]?)",path)
            if match:
                action="move";game_id=match[1];uci=match[2]
    if action is None:
        raise RouteDenied("endpoint forbidden")
    if action=="token_test":
        if body!=SYNTHETIC_TOKEN.encode("ascii"):
            raise RouteDenied("synthetic token mismatch")
        if h.get("content-type","").split(";")[0].strip() not in ("","text/plain"):
            raise RouteDenied("token test content type")
    elif action=="decline":
        # No runtime allowlist for negative challenge IDs exists in PR-A.
        # Local refusal is safer than allowing arbitrary account-side declines.
        raise RouteDenied("outbound decline disabled until explicitly source-bound")
    elif body:
        raise RouteDenied("unexpected request body")
    if action in ("events","game_stream") and method!="GET":
        raise RouteDenied("stream must be GET")
    if action=="game_stream" and (session.game_id is None or game_id!=session.game_id):
        raise RouteDenied("game stream not admitted")
    if action=="accept" and (session.challenge_id is None or challenge_id!=session.challenge_id):
        raise RouteDenied("challenge not admitted")
    if action=="move" and (session.game_id is None or game_id!=session.game_id):
        raise RouteDenied("move for unbound game")
    if action=="decline" and (session.challenge_id==challenge_id or session.game_id is not None):
        raise RouteDenied("cannot decline bound challenge or game")
    if action not in ("token_test","decline") and "content-type" in h and action not in ("accept","move"):
        raise RouteDenied("unneeded request content type")
    ply=epoch=None
    if action=="move":
        if set(h).intersection(("x-allfather-ply","x-allfather-epoch"))!={"x-allfather-ply","x-allfather-epoch"}:
            raise RouteDenied("missing durable move-intent header")
        raw_ply=h["x-allfather-ply"]
        epoch=h["x-allfather-epoch"]
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,3})",raw_ply) or not re.fullmatch(r"[0-9a-f]{32}",epoch):
            raise RouteDenied("invalid prepared move identity")
        ply=int(raw_ply)
    elif "x-allfather-ply" in h or "x-allfather-epoch" in h:
        raise RouteDenied("unneeded move-intent headers")
    return AdmittedRoute(method,action,path,game_id,challenge_id,uci,ply,epoch)

def validate_challenge(event: dict, *, owner: str, opponent: str) -> str:
    if not isinstance(event,dict) or event.get("type")!="challenge":
        raise RouteDenied("event is not a challenge")
    c=event.get("challenge")
    if not isinstance(c,dict):
        raise RouteDenied("challenge body missing")
    who=c.get("challenger",{})
    dest=c.get("destUser",{})
    t=c.get("timeControl",{})
    v=c.get("variant",{})
    cid=c.get("id")
    if (not isinstance(cid,str) or not ID.fullmatch(cid)
        or c.get("rated") is not False or c.get("speed")!="rapid"
        or not isinstance(v,dict) or v.get("key")!="standard"
        or c.get("initialFen")!="startpos"
        or not isinstance(t,dict) or t.get("type")!="clock"
        or type(t.get("limit")) is not int or t["limit"]!=600
        or type(t.get("increment")) is not int or t["increment"]!=5
        or not isinstance(who,dict) or not isinstance(dest,dict)
        or str(who.get("name","")).casefold()!=opponent.casefold()
        or str(who.get("title","")).upper()!="BOT"
        or str(dest.get("name","")).casefold()!=owner.casefold()
        or str(dest.get("title","")).upper()!="BOT"):
        raise RouteDenied("incoming challenge does not match BOT 10+5 policy")
    return cid

def validate_game_full(data: dict, *, owner: str, opponent: str, game_id: str) -> dict:
    if not isinstance(data,dict) or data.get("type")!="gameFull" or data.get("id")!=game_id:
        raise RouteDenied("unbound gameFull")
    if (data.get("rated") is not False or data.get("speed")!="rapid"
        or data.get("initialFen")!="startpos" or
        not isinstance(data.get("variant"),dict) or data["variant"].get("key")!="standard"):
        raise RouteDenied("game rule mismatch")
    clock=data.get("clock")
    if (not isinstance(clock,dict) or type(clock.get("initial")) is not int
        or clock["initial"]!=600000 or type(clock.get("increment")) is not int
        or clock["increment"]!=5000):
        raise RouteDenied("game clock mismatch")
    players=[]
    for side in ("white","black"):
        p=data.get(side)
        if not isinstance(p,dict) or str(p.get("title","")).upper()!="BOT":
            raise RouteDenied("non-BOT participant")
        players.append(str(p.get("name","")).casefold())
    if sorted(players)!=sorted((owner.casefold(),opponent.casefold())):
        raise RouteDenied("game BOT identities mismatch")
    state=data.get("state")
    if not isinstance(state,dict) or state.get("type")!="gameState" or state.get("status") is None:
        raise RouteDenied("gameFull lacks state")
    return state
