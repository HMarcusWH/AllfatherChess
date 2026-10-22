#!/usr/bin/env python3
"""Separate PRIVATE canary entrypoint. No default live action is available.

A PR-A checkout has no operator lease, named host or token and refuses startup.
This entrypoint is never the RELEASE-1 verify/offline-smoke executable.
"""
from __future__ import annotations
import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

ROOT=Path("/work")  # immutable exact-source tree, mounted read-only and HEAD-attested
sys.path.insert(0,str(ROOT))
from deploy.live_canary.admission import SourceBinding,verify_admission
from deploy.live_canary.supervisor import LiveSupervisor,validate_runtime_source
from deploy.lichess.live_gateway import LiveGateway,read_gateway_token,serve_gateway
from deploy.lichess.live_gateway_state import GatewayState

def _load(path:Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16384:
        raise ValueError("operator input missing, linked or oversized")
    return json.loads(path.read_text())

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=("verify-only","gateway","bridge"),nargs="?",default="verify-only")
    p.add_argument("--lease",type=Path,default=Path("/run/operator/admission.json"))
    p.add_argument("--public-key",type=Path,default=Path("/run/operator/operator-ed25519.pub.pem"))
    p.add_argument("--binding",type=Path,default=Path("/run/operator/source-binding.json"))
    p.add_argument("--host-report",type=Path,default=Path("/run/operator/host-report.json"))
    p.add_argument("--state",type=Path,default=Path("/var/lib/allfather-canary"))
    p.add_argument("--release",type=Path,default=Path("/opt/allfather"))
    p.add_argument("--bridge-source",type=Path,default=Path("/work/build/vendor/lichess-bot"))
    p.add_argument("--secret",type=Path,default=Path("/run/secrets/lichess-bot-token"))
    a=p.parse_args(argv)
    try:
        if a.mode=="verify-only":
            print(json.dumps({"status":"NOT_AUTHORIZED","live_game_authorized":False,
                              "public_bot_release":False}))
            return 0
        bind=_load(a.binding)
        if set(bind)!={f.name for f in dataclasses.fields(SourceBinding)}:
            raise ValueError("source binding fields inconsistent")
        source=SourceBinding(**bind)
        lease=_load(a.lease)
        validate_runtime_source(source,policy=ROOT/"qualification/online-live-canary-v1.json",
                                release=a.release,host_receipt=a.host_report)
        verify_admission(lease,source,a.public_key)
        if a.mode=="bridge":
            if os.getenv("LICHESS_BOT_TOKEN") or a.secret.exists():
                raise ValueError("real token forbidden in bridge")
            supervisor=LiveSupervisor(binding=source,lease=lease,key=a.public_key,
               policy=ROOT/"qualification/online-live-canary-v1.json",host_receipt=a.host_report,
               release=a.release,bridge=a.bridge_source,work=Path("/tmp/allfather-live-bridge"),
               state=a.state)
            outcome=supervisor.run(lease_file=a.lease,binding_file=a.binding)
            print(json.dumps(dataclasses.asdict(outcome),sort_keys=True))
            return 0 if outcome.status=="TERMINAL_DURABLE" else 2
        token=read_gateway_token(a.secret)
        state=GatewayState(a.state/"gateway.sqlite3",lease["session_nonce"],
                           source.owner_bot,source.opponent_bot)
        gateway=LiveGateway(state,token)
        gateway.authenticate()
        serve_gateway(gateway)
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        # NEVER print exception text: an underlying HTTP library may quote
        # real Authorization or token-test request bytes.
        print("ONLINE-PLAY-1 operation refused: "+type(exc).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
