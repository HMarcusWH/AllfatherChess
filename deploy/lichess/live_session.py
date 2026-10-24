"""Source-sealed disposable Lichess bridge staging for the separate live canary.

The offline staging path remains authoritative for upstream source verification and
sealed 4-process UCI selection. Only the copied upstream is patched.
"""
from __future__ import annotations
import hashlib
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

from deploy.lichess import offline_session, recovery_overlay, fetch_bridge
from deploy.lichess import live_bridge_policy

class LiveStageError(RuntimeError):
    """A source-independent, non-secret diagnosis for a staged operation."""
    def __init__(self, code: str, *, operation_code: str = "NOT_APPLICABLE"):
        self.code = code
        self.operation_code = operation_code
        super().__init__(code)


def _stage(code, call):
    try:
        return call()
    except Exception as exc:
        operation = (
            _frozen_offline_operation(exc)
            if code == "OFFLINE_STAGE_COPY_OR_CONFIG" else "NOT_APPLICABLE"
        )
        raise LiveStageError(code, operation_code=operation) from exc


def _frozen_offline_operation(exc: BaseException) -> str:
    """Classify the executed frozen stage frame; never serialize exception data.

    The offline stage function is never altered or skipped. Its bytecode frame
    and uniquely located source call sites are used only to identify which
    immutable operation threw. A missing/altered mapping returns UNKNOWN.
    """
    stage_code = getattr(offline_session.stage, "__code__", None)
    if stage_code is None:
        return "OFFLINE_STAGE_UNKNOWN"
    failed_line = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code is stage_code:
            failed_line = tb.tb_lineno
        tb = tb.tb_next
    if failed_line is None:
        return "OFFLINE_STAGE_UNKNOWN"
    try:
        source, first_line = inspect.getsourcelines(offline_session.stage)
    except (OSError, TypeError):
        return "OFFLINE_STAGE_UNKNOWN"
    anchors = (
        ("OFFLINE_STAGE_SOURCE_READ", "lock = json.loads("),
        ("OFFLINE_STAGE_UPSTREAM_RECHECK", "fetch_bridge.verify(bridge, lock)"),
        ("OFFLINE_STAGE_ENGINE_RECHECK", "seal = subprocess.run("),
        ("OFFLINE_STAGE_RELEASE_COPY", "shutil.copytree(release, runtime)"),
        ("OFFLINE_STAGE_BRIDGE_EXPORT", "fetch_bridge.copy_tracked(bridge, upstream)"),
        ("OFFLINE_STAGE_OVERLAY_VERIFY", "frozen = subprocess.check_output(["),
        ("OFFLINE_STAGE_OVERLAY_COPY", 'shutil.copyfile(extra, upstream / "extra_game_handlers.py")'),
        ("OFFLINE_STAGE_CONFIG_GENERATE", "effective = bridge_policy.generate("),
        ("OFFLINE_STAGE_RELEASE_IDENTITY", 'return {"release": runtime, "bridge": upstream, "config": config_path,'),
    )
    positions = []
    for code, marker in anchors:
        matches = [first_line + i for i, line in enumerate(source) if marker in line]
        if len(matches) != 1:
            return "OFFLINE_STAGE_UNKNOWN"
        positions.append((code, matches[0]))
    if any(a[1] >= b[1] for a, b in zip(positions, positions[1:])):
        return "OFFLINE_STAGE_UNKNOWN"
    operation = "OFFLINE_STAGE_INITIALIZATION"
    for code, lineno in positions:
        if failed_line < lineno:
            break
        operation = code
    return operation


# In a private OCI image these modules live under /opt/live-canary, while
# the immutable, Git-verified source checkout is mounted at /work.
# Only the source checkout may supply the signed challenge overlay.
ROOT = Path("/work") if (Path("/work/.git").is_dir()) else Path(__file__).resolve().parents[2]

def _committed_blob(path: str) -> bytes:
    return subprocess.check_output(["git","-C",str(ROOT),"show","HEAD:"+path],
                                   stderr=subprocess.PIPE,timeout=10)

def _pinned_upstream_precheck(bridge: Path) -> None:
    """Independent diagnostic check; frozen offline_session.stage re-verifies."""
    lock=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
    fetch_bridge.verify(bridge,lock)


def _sealed_engine_precheck(release: Path) -> None:
    """No stdout/stderr is released; frozen offline_session.stage re-verifies."""
    result=subprocess.run(
        [sys.executable,str(release/"deploy/bin/allfather-online"),"--check"],
        cwd=release,capture_output=True,text=True,timeout=40)
    if result.returncode or "offline seal verified" not in result.stdout:
        raise ValueError("sealed engine identity or verification refused")


def _stage_live_guards(staged_bridge: Path, overlay: dict) -> None:
    """Replace ONLY disposable recovery-guard bytes with exact current HEAD.

    The immutable upstream Git checkout and offline release staging are never
    modified. This is essential when a private live image inherits previously
    packaged offline recovery tools rather than the PR-A source checkout.
    """
    module_dir=staged_bridge/"deploy"/"lichess"
    if not module_dir.is_dir() or module_dir.is_symlink():
        raise ValueError("verified temporary recovery overlay missing")
    for name in ("guarded_transport.py","live_gateway_policy.py"):
        rel="deploy/lichess/"+name
        source=ROOT/rel
        if source.is_symlink() or not source.is_file():
            raise ValueError("live guard source missing: "+rel)
        raw=source.read_bytes()
        if raw!=_committed_blob(rel):
            raise ValueError("live guard differs from exact source HEAD: "+rel)
        compile(raw,str(source),"exec")
        target=module_dir/name
        if name=="guarded_transport.py":
            if target.is_symlink() or not target.is_file() or hashlib.sha256(
                    target.read_bytes()).hexdigest()!=overlay["guard_modules"]["guarded_transport.py"]:
                raise ValueError("staged offline recovery module tampered")
            target.write_bytes(raw)
            overlay["guard_modules"]["guarded_transport.py"]=hashlib.sha256(raw).hexdigest()
        else:
            if target.exists() or target.is_symlink():
                raise ValueError("live gateway policy would overwrite a staged module")
            target.write_bytes(raw)
            overlay["live_modules"]={name:hashlib.sha256(raw).hexdigest()}
        if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(raw).digest():
            raise ValueError("copied live guard byte identity drift: "+rel)
    overlay["live_profile"]="source-locked-live-canary-v1"
    (staged_bridge/"recovery-overlay-manifest.json").write_text(
        json.dumps(overlay,sort_keys=True,indent=2)+"\n",encoding="utf-8")


def stage_live(*,release: Path,bridge: Path,workspace: Path,opponent: str) -> dict:
    if workspace.exists() or workspace.is_symlink():
        raise ValueError("live workspace must be an empty new directory")
    workspace.mkdir(parents=True)
    # Source-locked diagnostics are intentionally redundant, not authorization
    # substitutes. The original frozen offline stage must still pass again.
    _stage("UPSTREAM_LOCK_CHECK",lambda: _pinned_upstream_precheck(bridge))
    _stage("ENGINE_SEAL_CHECK",lambda: _sealed_engine_precheck(release))
    result=_stage("OFFLINE_STAGE_COPY_OR_CONFIG",lambda: offline_session.stage(
        release=release,bridge=bridge,workspace=workspace,
        server_url="http://127.0.0.1:17832/"))
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
    effective=_stage("BRIDGE_CONFIG_VALIDATE",lambda: live_bridge_policy.generate(
        result["bridge"],result["release"],result["config"],opponent=opponent))
    overlay=_stage("UPSTREAM_RECOVERY_PATCH",lambda: recovery_overlay.apply(result["bridge"]))
    _stage("LIVE_GUARD_SEAL",lambda: _stage_live_guards(result["bridge"],overlay))
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
