#!/usr/bin/env python3
"""PR-A only: stage the exact pinned bridge and sealed UCI without any network.

A PASS validates source/overlay/configuration construction, not a game or live
transport. This test needs the PR's ephemeral Docker image and read-only source.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path("/work")
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from deploy.lichess.live_session import LiveStageError,stage_live
from deploy.lichess.live_bridge_policy import assert_safety

def check(source_sha: str) -> dict:
    current=subprocess.check_output(
        ["git","-C",str(ROOT),"rev-parse","HEAD"],
        text=True,stderr=subprocess.DEVNULL,timeout=12).strip()
    if current!=source_sha:
        raise ValueError("unapproved source SHA")
    lock=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
    if (lock["commit"]!="df7e730de58cc3ef2f1415a0dc2eeda842d39167"
        or lock["live_api_permitted"] is not False):
        raise ValueError("unapproved pinned upstream")
    with tempfile.TemporaryDirectory(prefix="allfather-stage-",dir="/tmp") as directory:
        stage=Path(directory)/"session"
        result=stage_live(
            release=ROOT/"dist/online3a-stage",
            bridge=ROOT/"build/vendor/lichess-bot",
            workspace=stage,opponent="approved_test_bot")
        effective=result["effective_live_config"]
        assert_safety(effective,release=result["release"],opponent="approved_test_bot")
        config=result["config"]
        if (not config.is_file() or config.is_symlink()
            or hashlib.sha256(config.read_bytes()).hexdigest()!=result["live_config_sha256"]):
            raise ValueError("effective live bridge config is not source-sealed")
        overlay=result["overlay_manifest"]
        guards=overlay.get("guard_modules",{})
        modules=overlay.get("live_modules",{})
        paths={**guards,**modules}
        for name,digest in paths.items():
            file=result["bridge"]/"deploy"/"lichess"/name
            if (not file.is_file() or file.is_symlink() or
                hashlib.sha256(file.read_bytes()).hexdigest()!=digest):
                raise ValueError("source-bound guard overlay differs from staged bytes")
        engine=result["release"]/"deploy/bin/allfather-online"
        verified=subprocess.run([str(engine),"--check"],cwd=result["release"],
                                capture_output=True,timeout=45,check=False)
        if verified.returncode or b"offline seal verified" not in verified.stdout:
            raise ValueError("sealed Allfather UCI verification failed")
        return {
            "scope":"synthetic-stage-preflight-only",
            "status":"PASS","source_sha":source_sha,
            "upstream_commit":lock["commit"],
            "live_config_sha256":result["live_config_sha256"],
            "overlay_guard_count":len(paths),
            "live_game_authorized":False,"public_bot_release":False,
        }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-sha",required=True)
    options=parser.parse_args()
    try:
        verdict=check(options.source_sha)
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.SubprocessError) as exc:
        code=exc.code if isinstance(exc,LiveStageError) else "SOURCE_OR_PACKAGE_PREFLIGHT"
        print(json.dumps({"scope":"synthetic-stage-preflight-only",
            "status":"FAIL","stage_code":code,
            "exception_type":type(exc).__name__,
            "live_game_authorized":False,"public_bot_release":False},
            sort_keys=True),file=sys.stderr)
        return 2
    print(json.dumps(verdict,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
