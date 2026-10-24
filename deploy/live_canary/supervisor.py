"""One lifetime, source-bound live bridge supervisor (no Lichess credential).

Only the signed lease and independently measured host/source can admit this
process. Gateway owns HTTPS and token; bridge receives synthetic token only.
Existing MoveJournal remains the authority for prepared moves and unknown ACK.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from deploy.live_canary.admission import (
    SourceBinding,LeaseLedger,AdmissionDenied,verify_admission,policy_is_denied)
from deploy.lichess.session_owner import SessionOwner
from deploy.lichess.live_session import stage_live,bridge_command
from deploy.lichess.canary_staging import (
    STAGE_SESSION, require_private_stage_mount,
)
from deploy.lichess.reconcile import canonical_history

# Runtime code is installed under /opt/live-canary; verified source lives at
# /work in the dedicated read-only mount. Refuse unsourced execution later.
ROOT=Path("/work") if (Path("/work/.git").is_dir()) else Path(__file__).resolve().parents[2]
GATEWAY="http://live-gateway:8765/"

class SupervisorRefused(RuntimeError):
    pass

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _git(*args):
    return subprocess.check_output(["git","-C",str(ROOT),*args],
                                   text=True,stderr=subprocess.PIPE,timeout=12).strip()

def verify_host_receipt(host: dict, binding: SourceBinding) -> None:
    """Check exact source-bound host facts, not just the receipt's claimed hash."""
    if not isinstance(host,dict) or host.get("scope") not in (
            "synthetic-ci-fixture-only","host-observation-only"):
        raise SupervisorRefused("host receipt format unrecognized")
    required={"schema_version","scope","host_id","capacity",
        "operator_envelope_met","durable_volume_separate",
        "bridge_egress_test_pass","gateway_private_port_test_pass",
        "destination_host_qualified","public_bot_release",
        "live_game_authorized","observation_digest"}
    if host["scope"]=="synthetic-ci-fixture-only":
        required|={"network_tests","storage_tests",
                   "image_identity_verified","source_mounts_read_only"}
    else:
        required|={"platform","machine","filesystem_observations",
            "bridge_only_internal","gateway_dual_homed",
            "separate_network_namespaces","no_host_ports",
            "gateway_only_secret_mount","bridge_external_tcp_denied"}
    if set(host)!=required or type(host.get("schema_version")) is not int or host["schema_version"]!=1:
        raise SupervisorRefused("host observation missing, extra or invalid fields")
    claimed=host.get("observation_digest")
    if not isinstance(claimed,str) or len(claimed)!=64:
        raise SupervisorRefused("host observation digest missing")
    raw=json.dumps({k:v for k,v in host.items() if k!="observation_digest"},
                   sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    actual=hashlib.sha256(raw).hexdigest()
    if (not hmac.compare_digest(actual,claimed)
            or not hmac.compare_digest(actual,binding.host_digest)
            or host["host_id"]!=binding.host_id):
        raise SupervisorRefused("host observation content differs from signed identity")
    capacity=host.get("capacity")
    if (not isinstance(capacity,dict)
        or capacity.get("operator_minimums_met") is not True
        or capacity.get("capacity_complete") is not True
        or host.get("operator_envelope_met") is not True
        or host.get("durable_volume_separate") is not True
        or any(host.get(k) is not True for k in (
            "bridge_egress_test_pass","gateway_private_port_test_pass"))
        or any(host.get(k) is not False for k in (
            "destination_host_qualified","public_bot_release","live_game_authorized"))):
        raise SupervisorRefused("effective host or network prerequisites not met")
    if host["scope"]=="synthetic-ci-fixture-only":
        net=host.get("network_tests")
        storage=host.get("storage_tests")
        if (host.get("image_identity_verified") is not True
                or host.get("source_mounts_read_only") is not True):
            raise SupervisorRefused("live test image or source mount identity unverified")
        if not isinstance(net,dict) or not isinstance(storage,dict) or any(
            net.get(k) is not True for k in (
                "bridge_egress_test_pass","gateway_private_port_test_pass",
                "separate_network_namespaces","bridge_only_internal",
                "gateway_dual_homed","no_host_ports","gateway_only_secret_mount",
                "bridge_external_tcp_denied")) or any(
            storage.get(k) is not True for k in (
                "persistent_separate","scratch_separate",
                "exec_stage_mount_verified")):
            raise SupervisorRefused("synthetic host topology or storage not independently verified")
    elif any(host.get(k) is not True for k in (
            "bridge_only_internal","gateway_dual_homed","separate_network_namespaces",
            "no_host_ports","gateway_only_secret_mount","bridge_external_tcp_denied")):
        raise SupervisorRefused("live host topology not independently verified")


def validate_runtime_source(binding: SourceBinding,*,policy: Path,
                            release: Path,host_receipt: Path):
    policy_digest=policy_is_denied(policy)
    if binding.policy_sha256!=policy_digest:
        raise SupervisorRefused("source-controlled live policy digest drift")
    if _git("rev-parse","HEAD")!=binding.source_commit:
        raise SupervisorRefused("live canary source HEAD drift")
    if _git("rev-parse","HEAD^{tree}")!=binding.source_tree:
        raise SupervisorRefused("live canary source tree drift")
    # HEAD identity alone is insufficient: a dirty checkout can execute
    # altered Python while still reporting the signed commit and tree.
    dirty=_git("status","--porcelain","--untracked-files=all")
    if dirty:
        raise SupervisorRefused("live canary source worktree differs from exact HEAD")
    _git("diff","--exit-code")
    _git("diff","--cached","--exit-code")
    from deploy.lichess import fetch_bridge
    lock=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
    if lock.get("commit")!="df7e730de58cc3ef2f1415a0dc2eeda842d39167" or lock.get("live_api_permitted") is not False:
        raise SupervisorRefused("upstream lock changed or gained permission")
    seal=json.loads((release/"build/online-release/release-manifest.json").read_text())
    if seal.get("source_commit")!=binding.source_commit:
        raise SupervisorRefused("sealed engine source is not exact canary HEAD")
    if host_receipt.is_symlink() or not host_receipt.is_file():
        raise SupervisorRefused("host receipt missing")
    verify_host_receipt(json.loads(host_receipt.read_text()),binding)

def reap_namespace(*,except_pids: tuple[int,...]=(),deadline_s:float=8.0)->dict:
    """Reap all descendants in the dedicated PID namespace, not by shared PID name."""
    if os.getpid()!=1:
        raise SupervisorRefused("supervisor must be PID1 of its isolated container")
    excluded={1,*except_pids}
    killed=[]
    def remaining():
        result=[]
        for p in Path("/proc").iterdir():
            if not p.name.isdigit() or int(p.name) in excluded: continue
            try:
                body=(p/"stat").read_text()
                if body.split(") ",1)[1][:1]!="Z":result.append(int(p.name))
            except (FileNotFoundError,ProcessLookupError,PermissionError):
                continue
        return result
    for pid in remaining():
        try: os.kill(pid,signal.SIGTERM);killed.append(pid)
        except ProcessLookupError: pass
    soft=time.monotonic()+min(2.,deadline_s/2)
    while time.monotonic()<soft and remaining():
        _reap_zombies()
        time.sleep(.05)
    hard=[]
    for pid in remaining():
        try: os.kill(pid,signal.SIGKILL);hard.append(pid)
        except ProcessLookupError: pass
    limit=time.monotonic()+deadline_s
    while time.monotonic()<limit and remaining():
        _reap_zombies()
        time.sleep(.05)
    left=remaining()
    if left:
        raise SupervisorRefused("owned process tree failed to drain")
    _reap_zombies()
    return {"pid_namespace_isolated":True,"sigterm_sent":len(killed),
            "sigkill_sent":len(hard),"live_remaining":0}

def _reap_zombies():
    while True:
        try:
            pid,_=os.waitpid(-1,os.WNOHANG)
            if not pid: return
        except ChildProcessError:return

def persist_stop_marker(flag:Path)->None:
    """Atomically stop all further gateway POSTs before killing the engine.

    A second invocation is idempotent; linked paths and missing volumes fail
    closed. Only the STOP marker is stored, never game IDs or token data.
    """
    flag=Path(flag)
    parent=flag.parent
    if parent.is_symlink() or not parent.is_dir() or not os.access(parent,os.W_OK|os.X_OK):
        raise SupervisorRefused("shared durable stop-control volume unavailable")
    try:
        fd=os.open(flag,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        st=os.lstat(flag)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:
            raise SupervisorRefused("unsafe existing stop-control marker")
    else:
        try:
            written=os.write(fd,b"STOP\n")
            if written!=5:
                raise SupervisorRefused("incomplete stop-control marker")
            os.fsync(fd)
        finally:
            os.close(fd)
    directory_fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(directory_fd)
    finally:os.close(directory_fd)

@dataclass
class SupervisedResult:
    status: str
    source_sha: str
    game_id: str | None
    accepted_moves: int
    terminal: bool
    processes: dict
    failure: str | None

class LiveSupervisor:
    def __init__(self,*,binding: SourceBinding,lease:dict,key:Path,
                 policy:Path,host_receipt:Path,release:Path,
                 bridge:Path,work:Path,state:Path):
        if os.getpid()!=1:
            raise SupervisorRefused("live supervisor requires private PID namespace PID1")
        self.binding,self.lease,self.key=binding,lease,Path(key)
        self.policy,self.host_receipt,self.release=Path(policy),Path(host_receipt),Path(release)
        self.bridge,self.work,self.state=Path(bridge),Path(work),Path(state)
        if (self.work != STAGE_SESSION or self.work.is_symlink()
            or self.work.is_relative_to(self.state)):
            raise SupervisorRefused("staged bridge must occupy exact private disposable mount")
        self.stopping=False
        self.phase="preflight"
        self.cleanup_error_type=None
        self.process=None
        self.owner=None
        self.ledger=LeaseLedger(self.state/"game-admission.sqlite3")
    def preflight(self):
        # Confirm a distinct executable tmpfs before the lease is claimed.
        # General /tmp remains noexec and must never hold a copied engine.
        require_private_stage_mount()
        directory=Path("/run/canary-control")
        if directory.is_symlink() or not directory.is_dir() or not os.access(directory,os.W_OK|os.X_OK):
            raise SupervisorRefused("durable shared kill switch volume missing or unwritable")
        validate_runtime_source(self.binding,policy=self.policy,release=self.release,
                                host_receipt=self.host_receipt)
        verify_admission(self.lease,self.binding,self.key)
        self.ledger.initialize()
        self.ledger.claim(self.lease["session_nonce"])
        if self.ledger.snapshot().get("terminal"):
            raise SupervisorRefused("one-game lease already completed")
        if os.getenv("LICHESS_BOT_TOKEN") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"):
            raise SupervisorRefused("real token/proxy must not enter bridge namespace")
        if os.environ.get("ALLFATHER_BRIDGE_EGRESS_ISOLATED")!="1":
            raise SupervisorRefused("separate network namespace not attested")
    def _environment(self,owner:SessionOwner,binding_file:Path,lease_file:Path):
        from deploy.lichess.bridge_policy import sanitized_env
        env=sanitized_env()
        env.update({
            "ALLFATHER_RECOVERY_DB":str(owner.journal.path),
            "ALLFATHER_RECOVERY_EPOCH":owner.epoch,
            "ALLFATHER_RECOVERY_AUDIT":str(owner.directory/"recovery-events.ndjson"),
            "ALLFATHER_TRANSPORT_PROFILE":"live-canary",
            "ALLFATHER_LIVE_GATEWAY_URL":GATEWAY,
            "ALLFATHER_LIVE_LEASE_FILE":str(lease_file),
            "ALLFATHER_LIVE_BINDING_FILE":str(binding_file),
            "ALLFATHER_LIVE_OPERATOR_KEY_FILE":str(self.key),
            "ALLFATHER_APPROVED_OPPONENT":self.binding.opponent_bot,
            "ALLFATHER_BRIDGE_EGRESS_ISOLATED":"1",
            "PYTHONPATH":str(ROOT)+os.pathsep+str(self.work/"bridge"),
        })
        env.pop("ONLINE4B_ISOLATED_CONTAINER",None)
        env.pop("LICHESS_BOT_TOKEN",None)
        return env
    def _progress(self,owner:SessionOwner):
        snap=owner.journal.snapshot()
        games=snap["games"]
        if len(games)>1:raise SupervisorRefused("second game appeared in durable bridge journal")
        if not games:return False,None,0
        gid=games[0]["game_id"]
        self.ledger.bind_game(self.lease["session_nonce"],gid)
        if not games[0]["terminal"]:return False,gid,len(json.loads(games[0]["history_json"]))
        history=canonical_history(json.loads(games[0]["history_json"]))
        intents=[r for r in snap["intents"] if r["game_id"]==gid]
        if any(r["status"]!="CONFIRMED" for r in intents):
            raise SupervisorRefused("terminal history has unresolved move-intent ACK")
        # Terminal is observed; final server-reconciled result/PGN requires the
        # independently written gateway-authoritative witness before SUCCESS.
        final=self.state/"gateway-evidence"/"authoritative-final.json"
        if final.is_symlink():
            raise SupervisorRefused("terminal witness is a symlink")
        if not final.is_file():
            # The server terminal and bridge journal are independent writers.
            # Give the bounded owner loop time to observe the other party's
            # fsynced evidence; a missing witness can never yield SUCCESS.
            return False,gid,len(history)
        record=json.loads(final.read_text())
        if (record.get("game_id")!=gid or record.get("terminal") is not True
            or record.get("moves")!=list(history)
            or record.get("result") not in ("1-0","0-1","1/2-1/2")
            or record.get("initial")!=600000 or record.get("increment")!=5000):
            raise SupervisorRefused("terminal authoritative history/result/clock mismatch")
        # The PGN must be derived from verified UCI board history, not a
        # producer assertion of correctness.
        import chess,chess.pgn
        board=chess.Board()
        for m in history: board.push_uci(m)
        if sorted((str(record.get("white","")).casefold(),
                   str(record.get("black","")).casefold())) != sorted((
                   self.binding.owner_bot.casefold(),self.binding.opponent_bot.casefold())):
            raise SupervisorRefused("terminal account identities mismatch")
        pgn=chess.pgn.Game.from_board(board)
        pgn.headers["Result"]=record["result"]
        pgn.headers["White"]=record["white"]
        pgn.headers["Black"]=record["black"]
        pgn.headers["TimeControl"]="600+5"
        pgn.headers["Variant"]="Standard"
        dest=self.state/"game.pgn"
        with dest.open("x",encoding="utf-8") as f:
            f.write(str(pgn)+"\n");f.flush();os.fsync(f.fileno())
        self.ledger.finish(self.lease["session_nonce"])
        return True,gid,len(intents)
    def stop_and_reap(self,reason="operator_stop"):
        self.stopping=True
        flag=Path("/run/canary-control/stop.flag")
        persist_stop_marker(flag)
        if self.process and self.process.poll() is None:
            try: os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait(timeout=5)
        evidence=reap_namespace()
        evidence["kill_switch_activated"]=flag.is_file() and not flag.is_symlink()
        if not evidence["kill_switch_activated"]:
            raise SupervisorRefused("gateway kill switch was not durably activated")
        return evidence
    def run(self,*,lease_file:Path,binding_file:Path,timeout_s=1800):
        self.phase="preflight"
        self.preflight()
        self.state.mkdir(parents=True,exist_ok=True)
        finished_result=None
        primary=None
        cleanup_error=None
        cleanup=None
        drained=False
        failed_phase=self.phase
        try:
            self.phase="workspace_stage"
            work=stage_live(release=self.release,bridge=self.bridge,
                            workspace=self.work,opponent=self.binding.opponent_bot)
            if work["release_source"]!=self.binding.source_commit:
                raise SupervisorRefused("sealed release source drift")
            self.phase="journal_initialize"
            journal_file=self.state/"recovery/move-journal.sqlite3"
            # CRITICAL: all children must be stopped while SessionOwner still
            # holds its exclusive flock. No new epoch may start during cleanup.
            with SessionOwner(self.state/"recovery") as owner, closing(
                 sqlite3.connect(str(journal_file),timeout=5)) as journal_keeper:
                try:
                    journal_keeper.execute("PRAGMA journal_mode=WAL")
                    self.owner=owner
                    log_path=self.state/"bridge-private.log"
                    with log_path.open("ab") as log:
                        self.phase="bridge_spawn"
                        self.process=subprocess.Popen(
                            bridge_command(work),cwd=work["bridge"],
                            env=self._environment(owner,binding_file,lease_file),
                            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                            start_new_session=True)
                        until=time.monotonic()+timeout_s
                        self.phase="await_terminal"
                        while not self.stopping and time.monotonic()<until:
                            stop=Path("/run/canary-control/stop.flag")
                            if stop.exists() or stop.is_symlink():
                                raise SupervisorRefused("operator kill switch requested shutdown")
                            self.phase="durable_progress"
                            complete,gid,ply=self._progress(owner)
                            if not complete and self.process.poll() is not None:
                                self.phase="bridge_early_exit"
                                raise SupervisorRefused("bridge exited before durable terminal")
                            if complete:
                                self.phase="terminal_reconciled"
                                finished_result=SupervisedResult(
                                    "TERMINAL_DURABLE",self.binding.source_commit,
                                    gid,ply,True,{},None)
                                break
                            self.phase="await_terminal"
                            time.sleep(.25)
                        if finished_result is None:
                            self.phase="bounded_timeout"
                            raise SupervisorRefused("operator stop or bounded game timeout")
                except BaseException as exc:
                    primary=exc
                    failed_phase=self.phase
                finally:
                    # This finally is INSIDE the exclusive SessionOwner scope.
                    drained=True
                    try:
                        self.phase="drain"
                        cleanup=self.stop_and_reap(
                            "terminal" if finished_result else "fail_closed_shutdown")
                    except BaseException as exc:
                        cleanup_error=exc
                        self.cleanup_error_type=type(exc).__name__
        except BaseException as exc:
            if primary is None:
                primary=exc
                failed_phase=self.phase
        if not drained:
            # If staging or owner acquisition fails before the guarded scope
            # starts, no game may have been authorized; still activate the
            # independent durable stop marker and reap any possible children.
            try:
                self.phase="drain"
                cleanup=self.stop_and_reap("fail_closed_shutdown")
            except BaseException as exc:
                cleanup_error=exc
                self.cleanup_error_type=type(exc).__name__
        self.phase=failed_phase if primary is not None else "drained"
        if primary is not None:
            if cleanup_error is not None:
                raise primary from cleanup_error
            raise primary
        if cleanup_error is not None:
            raise cleanup_error
        if finished_result is None:
            raise SupervisorRefused("no durable terminal after bounded run")
        finished_result.processes=cleanup
        return finished_result
