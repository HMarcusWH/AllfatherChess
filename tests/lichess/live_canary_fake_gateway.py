"""Real HTTP/NDJSON gateway in a dual-homed test container; fake upstream only.

The production gateway origin remains constant https://lichess.org. The only
test override is a local Session factory injected by this DIFFERENT test entrypoint.
No production executable imports this test fixture.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import requests

ROOT=Path("/work")
sys.path.insert(0,str(ROOT))
import tests.lichess.fake_lichess_api as fixture
from deploy.live_canary.admission import SourceBinding,verify_admission,policy_is_denied
from deploy.lichess.live_gateway import LiveGateway,BoundedServer,read_gateway_token
from deploy.lichess.live_gateway_state import GatewayState

class FixtureSession(requests.Session):
    def __init__(self,local_url: str):
        super().__init__()
        self.trust_env=False
        self.local_url=local_url
    def request(self,method,url,**kwargs):
        if not isinstance(url,str) or not url.startswith("https://lichess.org/api/"):
            raise RuntimeError("test session got nonconstant upstream origin")
        kwargs["verify"]=True
        kwargs["allow_redirects"]=False
        return super().request(method,self.local_url.rstrip("/")+
                               url[len("https://lichess.org"):],**kwargs)

def _read(name):
    path=Path("/run/operator")/name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("synthetic operator fixture missing")
    return json.loads(path.read_text())

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--color",required=True,choices=("white","black"))
    args=p.parse_args()
    try:
        source=SourceBinding(**_read("source-binding.json"))
        lease=_read("admission.json")
        verify_admission(lease,source,Path("/run/operator/operator-ed25519.pub.pem"))
        if source.owner_bot!="allfatherfixture" or source.opponent_bot!="approved_test_bot":
            raise RuntimeError("unknown fake BOT identities")
        if policy_is_denied(ROOT/"qualification/online-live-canary-v1.json")!=source.policy_sha256:
            raise RuntimeError("source candidate policy mismatch")
        token=read_gateway_token(Path("/run/secrets/test-only-fake-token"))
        # Local server accepts only this CI synthetic token; never Lichess.
        fixture.TOKEN=token
        server=fixture.FakeLichess(args.color)
        server.start()
        state=GatewayState(Path("/var/lib/allfather-canary/gateway.sqlite3"),
                           lease["session_nonce"],source.owner_bot,source.opponent_bot,
                           bridge_journal=Path("/bridge-state/recovery/move-journal.sqlite3"),
                           stop_flag=Path("/run/canary-control/stop.flag"))
        gateway=LiveGateway.for_fake_test_only(state,token,lambda: FixtureSession(server.url))
        gateway.authenticate()
        http=BoundedServer(("0.0.0.0",8765),gateway)
        thread=threading.Thread(target=http.serve_forever,
                                kwargs={"poll_interval":.1},daemon=False)
        thread.start()
        if not server.event_stream_open.wait(90):
            raise RuntimeError("real pinned bridge did not connect to gateway event stream")
        server.issue_challenge()
        if not server.game_stream_open.wait(90):
            raise RuntimeError("real pinned bridge did not accept fake challenge")
        if args.color=="black":
            server.opponent_move()
        if not server.finished.wait(750):
            raise RuntimeError("fake game did not finish naturally")
        # Let actual bridge observe terminal gameFull/gameState and commit PGN.
        deadline=time.monotonic()+30
        final=Path("/var/lib/allfather-canary/gateway-evidence/authoritative-final.json")
        while time.monotonic()<deadline and not final.is_file():
            time.sleep(.1)
        if not final.is_file():
            raise RuntimeError("gateway did not durably publish terminal state")
        complete=server.evidence()
        if not complete.get("complete") or not complete.get("challenge_accepted"):
            raise RuntimeError("fake chess completion missing")
        output=Path("/results/gateway")
        output.mkdir(parents=True,exist_ok=True)
        safe={key:complete.get(key) for key in (
            "game_id","color","challenge_accepted","challenge","game_full","complete","outcome","pgn",
            "moves","accepted","requests","forbidden_attempts","clock",
            "transport_trace","stream_sessions")}
        with (output/"server.json").open("x",encoding="utf-8") as file:
            json.dump(safe,file,sort_keys=True,indent=2)
        with (output/"gateway-state.json").open("x",encoding="utf-8") as file:
            data=state.snapshot()
            data.pop("nonce",None) # no portable runtime admission
            json.dump(data,file,sort_keys=True,indent=2)
        # Never persist raw token, upstream authorization or token-test body.
        if token in json.dumps(safe) or token in json.dumps(state.snapshot()):
            raise RuntimeError("synthetic token in unredacted gateway evidence")
        deadline=time.monotonic()+30
        while time.monotonic()<deadline and not Path("/results/bridge/report.json").is_file():
            time.sleep(.1)
        if not Path("/results/bridge/report.json").is_file():
            raise RuntimeError("bridge did not finish durable lifecycle")
        gateway.stop()
        http.shutdown();http.server_close();thread.join(timeout=10)
        server.close()
        return 0
    except Exception as exc:
        # no raw token or HTTP library diagnostics go into logs
        print("synthetic gateway integration failed: "+type(exc).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
