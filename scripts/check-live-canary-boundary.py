#!/usr/bin/env python3
"""Independent source-only forbiddance gate for ONLINE-PLAY-1.

Source baseline is the observed RELEASE-2B-2 merge, not the new candidate.
Missing commands, changed release/engine policy or approval states always fail.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE="ad1e582637d981199550caf389b43861ce3d2596"
IMMUTABLE=[
    "deploy/release/Dockerfile",
    "deploy/release/entrypoint.py",
    "deploy/release/runtime-contract.json",
    "qualification/release-image-v1.json",
    "qualification/release-qualification-v1.json",
    "qualification/license-compliance-v1.json",
    "qualification/online-bridge-v1.json",
    "deploy/lichess/bridge.lock.json",
    "deploy/lichess/bridge_policy.py",
    "deploy/lichess/extra_game_handlers.py",
    "deploy/lichess/offline_session.py",
    "deploy/lichess/recovery_overlay.py",
    "deploy/lichess/move_journal.py",
    "deploy/lichess/reconcile.py",
    "deploy/lichess/session_owner.py",
    "config/allfather.online-hybrid-v2.validation.json",
]
def baseline(path:str)->bytes:
    return subprocess.check_output(["git","-C",str(ROOT),"show",f"{BASE}:{path}"],
                                   stderr=subprocess.PIPE,timeout=15)
def inspect()->dict:
    checks={}
    for rel in IMMUTABLE:
        current=(ROOT/rel)
        if not current.is_file() or current.is_symlink() or current.read_bytes()!=baseline(rel):
            raise ValueError("offline authoritative source drift: "+rel)
        checks[rel]=hashlib.sha256(current.read_bytes()).hexdigest()
    policy=json.loads((ROOT/"qualification/online-live-canary-v1.json").read_text())
    schema=json.loads((ROOT/"qualification/schemas/online-live-canary-v1.schema.json").read_text())
    if not isinstance(schema.get("properties"),dict) or set(policy)!=set(schema["properties"]):
        raise ValueError("source policy schema fields changed")
    for field,value in policy.items():
        if schema["properties"][field].get("const",object())!=value and value is not None:
            raise ValueError("source policy schema not frozen: "+field)
        if value is None and schema["properties"][field].get("type")!="null":
            raise ValueError("runtime identity silently embedded")
    if schema.get("additionalProperties") is not False:
        raise ValueError("source policy admits unknown approval")
    for key in ("production_publication_authorized","public_bot_release","live_game_authorized",
                "operator_armed","legal_review_completed","destination_host_qualified",
                "dependency_lock_approved","move_retries_allowed"):
        if policy.get(key) is not False:
            raise ValueError("source approved an unreviewed promotion: "+key)
    offline=(ROOT/"deploy/release/entrypoint.py").read_text()
    if "choices=(\"verify\", \"offline-smoke\")" not in offline or 'if os.environ.get("LICHESS_BOT_TOKEN")' not in offline:
        raise ValueError("offline release entrypoint gained live behavior")
    live=(ROOT/"deploy/lichess/guarded_transport.py").read_text()
    if ('os.getenv("ALLFATHER_TRANSPORT_PROFILE", "offline")' not in live
        or 'parsed.hostname != "127.0.0.1"' not in live
        or 'ALLFATHER_LIVE_GATEWAY_URL' not in live):
        raise ValueError("explicit offline/live transport separation missing")
    upstream=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
    if upstream.get("commit")!="df7e730de58cc3ef2f1415a0dc2eeda842d39167" or upstream.get("live_api_permitted") is not False:
        raise ValueError("upstream pin or permission drift")
    for rel in ("deploy/live_canary/Dockerfile","deploy/live_canary/compose.test.yml"):
        content=(ROOT/rel).read_text()
        if any(bad in content for bad in ("docker push","gh release","LICHESS_BOT_TOKEN=","/api/bot/account/upgrade")):
            raise ValueError("canary test declared unapproved live side effects")
    return {"scope":"PR-A-source-boundary-only","baseline":BASE,
            "immutable_files":checks,"source_policy_authorizes_live":False,
            "publication_authorized":False}
def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path);a=p.parse_args()
    try: x=inspect()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        p.exit(2,"ONLINE-PLAY-1 source boundary FAILED: "+str(exc)+"\n")
    data=json.dumps(x,sort_keys=True,indent=2)+"\n"
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(data)
    else:print(data,end="")
    return 0
if __name__=="__main__":raise SystemExit(main())
