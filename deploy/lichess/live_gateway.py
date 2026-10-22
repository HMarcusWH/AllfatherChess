"""One-token, one-game Lichess HTTPS gateway with strict synthetic HTTP ingress.

Production origin is ALWAYS https://lichess.org. Fake-server dependency injection
is only performed by an explicit test harness, never by the production entrypoint.
All upstream GET/POST paths are validated locally before any network operation.
"""
from __future__ import annotations
import json
import math
import os
import re
import socket
import stat
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import requests
from deploy.lichess.live_gateway_policy import (
    SYNTHETIC_TOKEN, GATEWAY_HOST, LICHESS_ORIGIN, CanarySession,
    AdmittedRoute, admit_route, validate_challenge, validate_game_full,
    RouteDenied,
)
from deploy.lichess.live_gateway_state import GatewayState, GatewaySuspended
from deploy.lichess.reconcile import canonical_history

MAX_JSON=1048576
MAX_NDJSON_LINE=65536
MAX_HEADERS=24
MAX_CONCURRENT=4
STREAM_READ_TIMEOUT=45
TERMINAL={"mate","resign","stalemate","timeout","draw","outoftime","cheat","noStart","aborted","variantEnd","unknownFinish"}

class GatewayFailure(RuntimeError):
    pass

@dataclass(frozen=True)
class VerifiedBot:
    username: str
    user_id: str
    scopes: tuple[str,...]
    token_expires: int | None = None

def read_gateway_token(path: Path) -> str:
    """O_NOFOLLOW/owner-only file: the bridge never receives this value."""
    path=Path(path)
    if path.is_symlink():
        raise GatewayFailure("linked gateway credential denied")
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode & 0o077 or st.st_size>4096 or st.st_size<1:
                raise GatewayFailure("gateway credential owner/mode/size invalid")
            raw=os.read(fd,4097)
            if len(raw)>4096:
                raise GatewayFailure("gateway credential too large")
        finally:
            os.close(fd)
    except OSError as exc:
        raise GatewayFailure("gateway credential unavailable") from exc
    if raw.endswith(b"\n"):
        raw=raw[:-1]
    if not 1<=len(raw)<=4096 or any(b<33 or b>126 for b in raw):
        raise GatewayFailure("gateway credential format invalid")
    return raw.decode("ascii")

def _refuse_proxy_env() -> None:
    for name in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy",
                 "all_proxy","REQUESTS_CA_BUNDLE","CURL_CA_BUNDLE"):
        if os.environ.get(name):
            raise GatewayFailure("unexpected proxy/TLS environment")

def _json_response(response, *, max_bytes=MAX_JSON) -> dict:
    if not 200<=response.status_code<300 or response.is_redirect or (
            not response.headers.get("Content-Type","").lower().startswith("application/json")):
        raise GatewayFailure("unexpected HTTPS response status/content type")
    content=response.content
    if len(content)>max_bytes:
        raise GatewayFailure("oversized HTTPS JSON response")
    try:
        data=json.loads(content)
    except (ValueError,UnicodeError) as exc:
        raise GatewayFailure("invalid upstream JSON") from exc
    if not isinstance(data,dict):
        raise GatewayFailure("unexpected upstream JSON type")
    return data

def _cooldown_seconds(value: str | None) -> float:
    try:
        f=float(value)
        if not math.isfinite(f) or f<0:
            return 60.0
        return max(60.0,min(f,86400.0))
    except (ValueError,TypeError):
        return 60.0

def _status_code(response) -> int:
    try: return int(response.status_code)
    except (ValueError,AttributeError,TypeError):
        raise GatewayFailure("invalid upstream response status")

class LiveGateway:
    def __init__(self, state: GatewayState, token: str, *, fixture_factory: Callable | None=None):
        self.state=state
        self.token=token
        self._fixture_factory=fixture_factory
        info=state.snapshot()
        self.session=CanarySession(info["owner"],info["opponent"],info["challenge"],info["game"],
                                   bool(info["suspended"] or info["terminal"]),False)
        self.verified: VerifiedBot | None=None
        self.nonstream=threading.RLock()
        self.stream_locks={"events":threading.Lock(),"game_stream":threading.Lock()}
        self.streams={"events":None,"game_stream":None}
        self.active_streams={"events":0,"game_stream":0}
        self.shutdown_event=threading.Event()
        self.final_dir=state.path.parent/"gateway-evidence"
        self.bound_players=None
    @classmethod
    def for_fake_test_only(cls,state: GatewayState,fake_token: str,session_factory: Callable):
        """Unit/integration dependency injection. Never called from host entrypoint."""
        return cls(state,fake_token,fixture_factory=session_factory)
    def _session(self):
        session=self._fixture_factory() if self._fixture_factory else requests.Session()
        session.trust_env=False
        return session
    def _outbound(self,session,method: str,path: str,*,data=None,stream=False,form=False):
        if not path.startswith("/api/") or "?" in path or "#" in path:
            raise GatewayFailure("upstream target not canonical")
        if self._fixture_factory is None:
            _refuse_proxy_env()
        # Exact origin is a compile-time literal; no Host/proxy/cookie from ingress.
        headers={"Authorization":"Bearer "+self.token,"Accept":"application/x-ndjson" if stream else "application/json",
                 "User-Agent":"AllfatherChess-ONLINE-PLAY-1/1"}
        if method=="POST" and data is not None:
            headers["Content-Type"]="application/x-www-form-urlencoded" if form else "text/plain"
        kwargs={"headers":headers,"allow_redirects":False,"verify":True,
                "timeout":(4,STREAM_READ_TIMEOUT if stream else 5),"stream":stream}
        if data is not None: kwargs["data"]=data
        try:
            result=session.request(method,LICHESS_ORIGIN+path,**kwargs)
        except requests.RequestException as exc:
            raise GatewayFailure("upstream TLS/transport rejected") from exc
        status=_status_code(result)
        if result.is_redirect or status in (301,302,303,307,308):
            result.close()
            raise GatewayFailure("upstream redirect forbidden")
        if status==429:
            self.state.cooldown(_cooldown_seconds(result.headers.get("Retry-After")))
            result.close()
            raise GatewaySuspended("account-wide 429 cooldown active")
        if status==401 or status==403 or status>=400:
            result.close()
            self.state.suspend()
            raise GatewaySuspended("upstream account or API operation rejected")
        if not 200<=status<300:
            result.close()
            raise GatewayFailure("unexpected upstream response status")
        return result
    def authenticate(self) -> VerifiedBot:
        """Only real credentials live here, never in bridge token-test replies."""
        if self.verified is not None: return self.verified
        self.state.require_active()
        with self.nonstream:
            session=self._session()
            try:
                tested=self._outbound(session,"POST","/api/token/test",data=self.token)
                try: info=_json_response(tested)
                finally: tested.close()
                token=info.get(self.token)
                if not isinstance(token,dict):
                    raise GatewayFailure("real token test response not keyed by tested token")
                scopes=token.get("scopes")
                if isinstance(scopes,str):
                    scopes=set(scopes.split())
                elif isinstance(scopes,list) and all(isinstance(s,str) for s in scopes):
                    scopes=set(scopes)
                else:
                    raise GatewayFailure("real token scopes invalid")
                if "bot:play" not in scopes:
                    raise GatewayFailure("real token missing Bot API permission")
                user_id=token.get("userId")
                if not isinstance(user_id,str) or user_id.casefold()!=self.session.owner_bot.casefold():
                    raise GatewayFailure("real token owner mismatch")
                response=self._outbound(session,"GET","/api/account")
                try: profile=_json_response(response)
                finally: response.close()
                if (str(profile.get("title","")).upper()!="BOT" or
                    str(profile.get("username","")).casefold()!=self.session.owner_bot.casefold() or
                    str(profile.get("id","")).casefold()!=self.session.owner_bot.casefold()):
                    raise GatewayFailure("account is not approved BOT identity")
                response=self._outbound(session,"GET","/api/account/playing")
                try: playing=_json_response(response)
                finally: response.close()
                active=playing.get("nowPlaying",[])
                if not isinstance(active,list):
                    raise GatewayFailure("active game response invalid")
                previous=self.state.snapshot()["game"]
                if len(active)>(1 if previous else 0) or any(
                        not isinstance(p,dict) or p.get("gameId")!=previous for p in active):
                    raise GatewayFailure("unexpected active games on account")
                self.verified=VerifiedBot(profile["username"],user_id,tuple(sorted(scopes)),
                                          token.get("expires") if type(token.get("expires")) is int else None)
                self.session.token_verified=True
                return self.verified
            except (GatewayFailure,GatewaySuspended):
                self.state.suspend()
                raise
            finally:
                session.close()
    def synthetic_token_test(self,body: bytes) -> bytes:
        if body!=SYNTHETIC_TOKEN.encode("ascii") or self.verified is None:
            raise GatewayFailure("synthetic token-test refused")
        minimal={SYNTHETIC_TOKEN:{"scopes":"bot:play","userId":self.verified.user_id}}
        return json.dumps(minimal,separators=(",",":")).encode()
    def nonstream_request(self,route: AdmittedRoute,body: bytes) -> tuple[int,bytes,dict]:
        if route.action=="token_test":
            return 200,self.synthetic_token_test(body),{}
        self.state.require_active()
        if route.action not in ("profile","playing","accept","decline","move"):
            raise GatewayFailure("non-stream operation not allowed")
        if route.action=="accept":
            self.state.accept_once(route.challenge_id)
        if route.action=="move":
            self.state.move_once(route.game_id,route.uci,route.ply,route.epoch)
        # Plain-text decline limited to generic; fake body translated only to
        # an explicitly fixed form and never copied as attacker-controlled URL.
        with self.nonstream:
            up=self._session()
            try:
                response=self._outbound(up,route.method,route.path,
                                        data=(body if route.action=="decline" and body else None),
                                        form=route.action=="decline")
                try:
                    response_body=response.content
                    if len(response_body)>MAX_JSON or self.token.encode() in response_body:
                        raise GatewayFailure("upstream response unbounded or contains credential")
                    if (not response.headers.get("Content-Type","").lower().startswith("application/json")
                        or not isinstance(json.loads(response_body),dict)):
                        raise GatewayFailure("unrecognized non-stream response")
                    return response.status_code,response_body,{}
                finally: response.close()
            except (ValueError,UnicodeError):
                self.state.suspend()
                raise GatewayFailure("malformed upstream response")
            finally: up.close()

    def _persist_terminal(self, payload: dict, history: tuple[str,...]) -> None:
        """Durable observed terminal witness; engine journal verifies independently."""
        if self.bound_players is None or payload.get("status") not in TERMINAL:
            raise GatewayFailure("terminal game identity/status not independently bound")
        winner=payload.get("winner")
        if winner in ("white","black"):
            result="1-0" if winner=="white" else "0-1"
        elif payload.get("status") in ("draw","stalemate","variantEnd"):
            result="1/2-1/2"
        else:
            raise GatewayFailure("terminal outcome lacks trusted winner")
        dest=self.final_dir
        if dest.is_symlink(): raise GatewayFailure("terminal evidence path linked")
        dest.mkdir(parents=True,exist_ok=True)
        witness={
            "schema_version":1,"game_id":self.session.game_id,"terminal":True,
            "moves":list(history),"result":result,"status":payload["status"],
            "initial":600000,"increment":5000,
            "white":self.bound_players["white"],"black":self.bound_players["black"]}
        data=(json.dumps(witness,sort_keys=True,separators=(",",":"))+"\n").encode()
        tmp=dest/("terminal-"+str(os.getpid())+".tmp")
        try:
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                if os.write(fd,data)!=len(data): raise GatewayFailure("short terminal evidence write")
                os.fsync(fd)
            finally: os.close(fd)
            final=dest/"authoritative-final.json"
            if final.exists() or final.is_symlink():
                raise GatewayFailure("second terminal evidence publication")
            os.replace(tmp,final)
            dirfd=os.open(dest,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
        finally:
            tmp.unlink(missing_ok=True)

    def _on_ndjson(self,kind: str,payload: dict,*,first: bool) -> None:
        t=payload.get("type")
        if kind=="events":
            if t=="challenge":
                try:
                    cid=validate_challenge(payload,owner=self.session.owner_bot,
                                           opponent=self.session.opponent_bot)
                except RouteDenied:
                    return # bridge may reject, gateway will never accept it
                self.state.challenge(cid)
                self.session.challenge_id=cid
            elif t=="gameStart":
                game=payload.get("game",{})
                if not isinstance(game,dict) or not re.fullmatch(r"[A-Za-z0-9]{8}",str(game.get("id",""))):
                    raise GatewayFailure("malformed gameStart")
                if str(game.get("opponent",{}).get("username","")).casefold()!=self.session.opponent_bot.casefold():
                    raise GatewayFailure("unapproved gameStart opponent")
                self.state.game_start(game["id"])
                self.session.game_id=game["id"]
            elif t in ("challengeCanceled","challengeDeclined","gameFinish"):
                pass
            else:
                raise GatewayFailure("unrecognized account stream event")
        else:
            if first:
                if t!="gameFull": raise GatewayFailure("game stream must begin with gameFull")
                state=validate_game_full(payload,owner=self.session.owner_bot,
                                         opponent=self.session.opponent_bot,
                                         game_id=self.session.game_id)
                self.bound_players={
                    side:payload[side]["name"] for side in ("white","black")}
                history=canonical_history(state["moves"])
                self.state.game_full(self.session.game_id,history)
                self.state.game_state(self.session.game_id,history,state["status"] in TERMINAL)
                if state["status"] in TERMINAL:
                    self._persist_terminal(state,history)
            elif t=="gameState":
                history=canonical_history(payload["moves"])
                terminal=payload.get("status") in TERMINAL
                if terminal: self._persist_terminal(payload,history)
                self.state.game_state(self.session.game_id,history,terminal)
            elif t in ("chatLine","opponentGone"):
                pass # read-only events; never send chat
            else:
                raise GatewayFailure("unrecognized game stream event")
    @staticmethod
    def bounded_ndjson(response):
        """Incremental framing: bound memory BEFORE a newline arrives.

        requests.iter_lines() alone can buffer an attacker-controlled unlimited
        unterminated line. At most 4 KiB enter the buffer at a time.
        """
        pending=bytearray()
        for chunk in response.iter_content(chunk_size=4096,decode_unicode=False):
            if not isinstance(chunk,bytes):
                raise GatewayFailure("upstream stream returned non-byte content")
            pending.extend(chunk)
            while True:
                newline=pending.find(b"\\n")
                if newline<0: break
                if newline>MAX_NDJSON_LINE:
                    raise GatewayFailure("NDJSON line exceeds safety bound")
                raw=bytes(pending[:newline])
                del pending[:newline+1]
                if raw.endswith(b"\\r"): raw=raw[:-1]
                yield raw
            if len(pending)>MAX_NDJSON_LINE:
                raise GatewayFailure("oversized unterminated NDJSON line")
        if pending:
            raise GatewayFailure("truncated NDJSON line at stream EOF")

    def stream_request(self,route: AdmittedRoute,writer,*,on_open=lambda:None) -> None:
        kind=route.action
        if kind not in self.stream_locks:
            raise GatewayFailure("unknown NDJSON stream")
        self.state.require_active()
        lock=self.stream_locks[kind]
        if not lock.acquire(blocking=False):
            raise GatewaySuspended("duplicate stream refused; must fence previous owner")
        up=self._session()
        response=None
        try:
            if self.active_streams[kind]:
                raise GatewaySuspended("duplicate account/game stream")
            self.active_streams[kind]=1
            response=self._outbound(up,"GET",route.path,stream=True)
            if not response.headers.get("Content-Type","").lower().startswith("application/x-ndjson"):
                raise GatewayFailure("unexpected stream content type")
            on_open()
            first=True
            for line in self.bounded_ndjson(response):
                if self.shutdown_event.is_set(): break
                if not isinstance(line,bytes) or len(line)>MAX_NDJSON_LINE:
                    raise GatewayFailure("NDJSON frame exceeds bound")
                if not line:
                    writer(b"\n")
                    continue
                try:
                    frame=json.loads(line.decode("utf-8"))
                except (ValueError,UnicodeError) as exc:
                    raise GatewayFailure("malformed NDJSON frame") from exc
                if not isinstance(frame,dict): raise GatewayFailure("NDJSON object required")
                self._on_ndjson(kind,frame,first=first)
                first=False
                writer(line+b"\n")
        except (OSError,requests.RequestException,GatewayFailure,GatewaySuspended,RouteDenied,
                ValueError,KeyError) as exc:
            # Suspend on ambiguous protocol, not on ordinary controlled disconnect.
            if isinstance(exc,(GatewayFailure,RouteDenied,ValueError,KeyError)):
                self.state.suspend()
            raise
        finally:
            if response is not None: response.close()
            up.close()
            self.active_streams[kind]=0
            lock.release()
    def stop(self):
        self.shutdown_event.set()
        self.state.suspend()

class BoundedServer(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True
    request_queue_size=4
    allow_reuse_address=False
    def __init__(self,address,gateway: LiveGateway):
        self.gateway=gateway
        self.capacity=threading.BoundedSemaphore(MAX_CONCURRENT)
        super().__init__(address,GatewayHandler)
    def process_request(self,request,client_address):
        if not self.capacity.acquire(blocking=False):
            request.close();return
        try: super().process_request(request,client_address)
        except BaseException:
            self.capacity.release()
            raise
    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.capacity.release()

class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    server_version="AllfatherGateway/1"
    sys_version=""
    def log_message(self,*_): pass
    def parse_request(self):
        self._raw_target=b""
        raw=self.raw_requestline.rstrip(b"\r\n").split(b" ")
        if len(raw)==3 and raw[2] in (b"HTTP/1.0",b"HTTP/1.1"):
            self._raw_target=raw[1]
        return super().parse_request()
    def _reply(self,status: int,body: bytes=b"",ctype: str="application/json"):
        self.send_response_only(status)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Content-Type",ctype)
        self.send_header("Cache-Control","no-store")
        self.send_header("Connection","close")
        self.end_headers()
        if body: self.wfile.write(body)
        self.wfile.flush()
        self.close_connection=True
    def _handle(self):
        try:
            if len(self.headers)>MAX_HEADERS or any(len(self.headers.get_all(h))!=1 for h in self.headers):
                raise RouteDenied("duplicate or excessive headers")
            if "Transfer-Encoding" in self.headers:
                raise RouteDenied("chunked request forbidden")
            size=self.headers.get("Content-Length","0")
            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,3})",size) or int(size)>128:
                raise RouteDenied("request size refused")
            body=self.rfile.read(int(size)) if size!="0" else b""
            gw=self.server.gateway
            if gw.verified is None:
                raise RouteDenied("gateway not authenticated")
            route=admit_route(self.command,self._raw_target,dict(self.headers.items()),
                             body,gw.session)
            if route.action in ("events","game_stream"):
                def open_stream():
                    # Never report HTTP 200 until the upstream stream has been
                    # verified and the one-stream lease has been acquired.
                    self.send_response_only(200)
                    self.send_header("Content-Type","application/x-ndjson")
                    self.send_header("Cache-Control","no-store")
                    self.send_header("Connection","close")
                    self.end_headers()
                    self.wfile.flush()
                def write(data):
                    self.wfile.write(data);self.wfile.flush()
                gw.stream_request(route,write,on_open=open_stream)
                self.close_connection=True
            else:
                code,data,_=gw.nonstream_request(route,body)
                self._reply(code,data)
        except (RouteDenied,GatewayFailure,GatewaySuspended,ValueError,KeyError,
                OSError,requests.RequestException):
            # Generic error only: never send token-test real key, HTTP URL or body.
            if not self.wfile.closed and not self.headers_sent():
                try:
                    cooldown=self.server.gateway.state.snapshot()["cooldown_until"]>time.time()
                    self._reply(429 if cooldown else 403,
                                b'{"error":"canary request denied"}')
                except (OSError,BrokenPipeError): pass
            self.close_connection=True
    def headers_sent(self):
        return getattr(self,"_headers_buffer",None)==[]
    def do_GET(self): self._handle()
    def do_POST(self): self._handle()
    def do_CONNECT(self): self._reply(405,b'{"error":"method denied"}')
    def do_PUT(self): self._reply(405,b'{"error":"method denied"}')
    def do_DELETE(self): self._reply(405,b'{"error":"method denied"}')

def serve_gateway(gateway: LiveGateway,bind_host="0.0.0.0",port=8765):
    if gateway.verified is None or port!=8765:
        raise GatewayFailure("gateway start without verified BOT or canonical port")
    server=BoundedServer((bind_host,port),gateway)
    try: server.serve_forever(poll_interval=0.2)
    finally:
        gateway.shutdown_event.set()
        server.server_close()
