"""Source-sealed disposable Lichess bridge staging for the separate live canary.

The offline staging path remains authoritative for upstream source verification and
sealed 4-process UCI selection. Only the copied upstream is patched.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from deploy.lichess import offline_session, recovery_overlay
from deploy.lichess import live_bridge_policy

ROOT=Path(__file__).resolve().parents[2]

def _committed_blob(path: str) -> bytes:
    return subprocess.check_output(["git","-C",str(ROOT),"show","HEAD:"+path],
                                   stderr=subprocess.PIPE,timeout=10)

def stage_live(*,release: Path,bridge: Path,workspace: Path,opponent: str) -> dict:
    if workspace.exists() or workspace.is_symlink():
        raise ValueError("live workspace must be an empty new directory")
    workspace.mkdir(parents=True)
    result=offline_session.stage(
        release=release,bridge=bridge,workspace=workspace,
        server_url="http://127.0.0.1:17832/")
    extra=ROOT/"deploy/lichess/live_extra_game_handlers.py"
    rel="deploy/lichess/live_extra_game_handlers.py"
    if not extra.is_file() or extra.is_symlink() or extra.read_bytes()!=_committed_blob(rel):
        raise ValueError("live challenge overlay is not exactly HEAD-sealed")
    # The offline handler was staged into the disposable copy only. Replace it
    # after upstream verification, without touching pinned source checkout.
    copy=result["bridge"]/"extra_game_handlers.py"
    shutil.copyfile(extra,copy)
    if hashlib.sha256(copy.read_bytes()).digest()!=hashlib.sha256(extra.read_bytes()).digest():
        raise ValueError("challenge overlay copy mismatch")
    effective=live_bridge_policy.generate(result["bridge"],result["release"],
                                            result["config"],opponent=opponent)
    overlay=recovery_overlay.apply(result["bridge"])
    result.update(overlay_manifest=overlay,live_config_sha256=hashlib.sha256(
        result["config"].read_bytes()).hexdigest(),
        live_handler_sha256=hashlib.sha256(extra.read_bytes()).hexdigest(),
        effective_live_config=effective)
    if overlay["mode"]!="OFFLINE-4B-ONLY" or overlay["upstream_commit"]!=result["bridge_commit"]:
        raise ValueError("recovery overlay source mismatch")
    # The recovery adapter remains intact. A distinct source-verified
    # live-canary transport profile is chosen only by the supervisor.
    return result

def bridge_command(work: dict) -> list[str]:
    return offline_session.bridge_command(work)
