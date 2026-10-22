#!/usr/bin/env python3
"""PR-A Docker orchestration: generate EPHEMERAL FAKE approvals; no real Lichess.

Created fixtures are under build/ only, and are destroyed after each run.
A simulated operator signature is never deployment authority. The bridge must
be confined to an actual internal-only Docker network, with gateway in a
different namespace and the sole holder of the synthetic test token.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import sys
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# Direct python3 scripts/xxx.py execution puts scripts/ (not repository root)
# first on sys.path. The runtime admission modules are source-controlled at
# deploy/, and must resolve from the exact checkout, never from site-packages.
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
COMPOSE=ROOT/"deploy/live_canary/compose.test.yml"

def cmd(args,*,env=None,timeout=30):
    """Invoke a local tool with bounded, credential-redacted failure evidence.

    During offline PR-A no real credentials exist. Do not leak even a seeded
    test token or arbitrary stderr content into GitHub Actions.
    """
    result=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          cwd=ROOT,env=env,timeout=timeout,check=False)
    if result.returncode:
        # Compose errors are commonly stderr-only. Retain the *cause*, but
        # never propagate Authorization or synthetic credential bytes.
        message=" ".join(result.stderr.splitlines()[-6:])[-1100:]
        message=re.sub(r"(?i)bearer\s+[^\s]+", "Bearer <REDACTED>", message)
        message=re.sub(r"synthetic-only-[A-Za-z0-9_\-]+", "<SYNTHETIC_REDACTED>", message)
        message=re.sub(r"(?i)offline-fixture-token", "<SYNTHETIC_REDACTED>", message)
        message=re.sub(r"(?i)(?:token|authorization)\s*[:=]\s*[^\s]+", "<REDACTED>", message)
        raise RuntimeError(f"test command {args[0]} {args[1] if len(args)>1 else ''} "
                           f"failed with exit={result.returncode}: {message}")
    return result.stdout.strip()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,obj):
    p=Path(path)
    p.write_text(json.dumps(obj,sort_keys=True,indent=2)+"\n")
def imported(path):
    spec=importlib.util.spec_from_file_location("live_canary_host_inspection",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
def within(path:Path,base:Path)->bool:
    return path.resolve().is_relative_to(base.resolve())

def host_facts(bridge,gateway,image_id):
    inspector=imported(ROOT/"scripts/qualify-live-canary-host.py")
    net=inspector.inspect_namespaces(bridge,gateway)
    observation=json.loads(cmd([
        "docker","exec",bridge,"/opt/online3b/bin/python",
        "/work/scripts/online-host-preflight.py",
        "--require-capacity","--min-usable-cpus","4",
        "--min-memory-mib","6144"],timeout=20))
    if not observation.get("operator_minimums_met"):
        raise RuntimeError("synthetic test-container CPU/memory capacity not met")
    storage=json.loads(cmd(["docker","exec",bridge,"/opt/online3b/bin/python",
        "-c","import os,json; a=os.stat('/var/lib/allfather-canary');"
             "b=os.stat('/'); c=os.stat('/tmp');"
             "print(json.dumps({'persistent_separate':a.st_dev!=b.st_dev,"
             "'scratch_separate':a.st_dev!=c.st_dev}))"],timeout=15))
    if not (net["bridge_egress_test_pass"] and net["gateway_private_port_test_pass"]
            and storage["persistent_separate"] and storage["scratch_separate"]):
        raise RuntimeError("real Docker namespace or storage negative control failed")
    # A configured image/tag is not proof of what Compose actually ran.
    # Check both independent Docker runtime identities and read-only source mounts.
    containers=json.loads(cmd(["docker","inspect",bridge,gateway],timeout=15))
    if len(containers)!=2 or any(c.get("Image")!=image_id for c in containers):
        raise RuntimeError("private test image changed between resolution and execution")
    def exact_readonly_worktree(c):
        mounts=[m for m in c.get("Mounts",[]) if m.get("Destination")=="/work"]
        return (len(mounts)==1 and mounts[0].get("RW") is False
                and Path(mounts[0].get("Source","")).resolve()==ROOT.resolve())
    if not all(exact_readonly_worktree(c) for c in containers):
        raise RuntimeError("actual canary process source mount is not exact and read-only")
    host={
        "schema_version":1,
        "scope":"synthetic-ci-fixture-only",
        "host_id":"ci-synthetic-dual-container",
        "image_identity_verified":True,
        "source_mounts_read_only":True,
        "capacity":observation,
        "operator_envelope_met":True,
        "durable_volume_separate":True,
        "bridge_egress_test_pass":net["bridge_egress_test_pass"],
        "gateway_private_port_test_pass":net["gateway_private_port_test_pass"],
        "network_tests":net,
        "storage_tests":storage,
        "destination_host_qualified":False,
        "public_bot_release":False,
        "live_game_authorized":False,
    }
    canonical=json.dumps(host,sort_keys=True,separators=(",",":")).encode()
    host["observation_digest"]=hashlib.sha256(canonical).hexdigest()
    return host

def fabricate_runtime_test_lease(fixture:Path,host:dict,image_id:str,source_sha:str,
                                 token_path:Path,*,color:str):
    import dataclasses
    from deploy.live_canary.admission import SourceBinding,policy_is_denied
    if not image_id.startswith("sha256:") or len(image_id)!=71:
        raise RuntimeError("not a local image sha256 identity")
    source_tree=cmd(["git","rev-parse","HEAD^{tree}"])
    policy_sha=policy_is_denied(ROOT/"qualification/online-live-canary-v1.json")
    bound=SourceBinding(source_commit=source_sha,source_tree=source_tree,
                        image_digest=image_id,policy_sha256=policy_sha,
                        host_id=host["host_id"],host_digest=host["observation_digest"],
                        legal_review_digest="0"*64,
                        owner_bot="allfatherfixture",opponent_bot="approved_test_bot")
    # Test-only approval generated by harness, NOT a human approval. This
    # never changes SOURCE flags and is never valid for an actual Bot API call.
    now=dt.datetime.now(dt.timezone.utc)
    before=(now-dt.timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    after=(now+dt.timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lease={
        **dataclasses.asdict(bound),
        "schema_version":1,"session_nonce":secrets.token_hex(24),
        "not_before_utc":before,"expires_at_utc":after,
        "max_games":1,"operator_approval_id":"ci-fake-"+secrets.token_hex(10),
    }
    # The synthetic signer private key is never inside the mounted operator
    # directory, even transiently. No container receives or reads it.
    key=fixture.parent/"synthetic-private-key.unmounted.pem"
    pub=fixture/"operator-ed25519.pub.pem"
    cmd(["openssl","genpkey","-algorithm","Ed25519","-out",str(key)],timeout=10)
    os.chmod(key,0o600)
    cmd(["openssl","pkey","-in",str(key),"-pubout","-out",str(pub)],timeout=10)
    payload=(json.dumps(lease,sort_keys=True,separators=(",",":"),
                        ensure_ascii=True,allow_nan=False)+"\n").encode()
    data=fixture/"signed-input.tmp"
    sig=fixture/"signed-output.tmp"
    data.write_bytes(payload)
    cmd(["openssl","pkeyutl","-sign","-inkey",str(key),
         "-rawin","-in",str(data),"-out",str(sig)],timeout=10)
    lease["signature_b64"]=base64.b64encode(sig.read_bytes()).decode("ascii")
    data.unlink();sig.unlink()
    write(fixture/"source-binding.json",dataclasses.asdict(bound))
    write(fixture/"host-report.json",host)
    write(fixture/"admission.json",lease)
    # The test-private-key never enters a container or a CI artifact.
    key.unlink()
    for p in (fixture/"source-binding.json",fixture/"host-report.json",
              fixture/"admission.json",pub):
        os.chmod(p,0o644)

def run(color:str,image:str,source_sha:str,out:Path):
    if color not in ("white","black"):raise RuntimeError("invalid color")
    if cmd(["git","rev-parse","HEAD"])!=source_sha:
        raise RuntimeError("CI exact source SHA drift")
    if not (ROOT/"dist/online3a-stage/deploy/bin/allfather-online").is_file():
        raise RuntimeError("actual sealed engine package not built")
    if not (ROOT/"build/vendor/lichess-bot/.git").exists():
        raise RuntimeError("pinned upstream checkout missing")
    out=out.resolve()
    if not within(out,ROOT/"build"):
        raise RuntimeError("test artifact must remain under source build directory")
    if out.exists() or out.is_symlink():
        raise RuntimeError("fresh test output directory required")
    out.mkdir(parents=True)
    # Synthetic outputs only; test container UID 10001 writes its own files.
    for name in ("gateway","bridge"):
        target=out/name
        target.mkdir()
        target.chmod(0o777)
    fixture=out/"operator-fixture";fixture.mkdir()
    token=out/"synthetic-gateway-secret.private"
    token.write_text("synthetic-only-"+secrets.token_urlsafe(28)+"\n")
    token.chmod(0o600)
    subprocess.run(["sudo","chown","10001:10001",str(token)],
                   check=True,capture_output=True,timeout=10)
    project="afc-canary-"+color+"-"+source_sha[:8].lower()
    env=os.environ.copy()
    env.update(CANARY_WORKTREE=str(ROOT),CANARY_OPERATOR_FIXTURE=str(fixture),
               CANARY_TEST_TOKEN_FILE=str(token),CANARY_OUTPUT_DIR=str(out),
               CANARY_TEST_IMAGE=image,CANARY_COLOR=color)
    image_id=cmd(["docker","image","inspect",image,"--format","{{.Id}}"])
    def compose(*args,timeout=40):
        return cmd(["docker","compose","-p",project,"-f",str(COMPOSE),*args],
                   env=env,timeout=timeout)
    gateway=bridge=None
    try:
        compose("config","--quiet")
        try:
            compose("up","-d",timeout=100)
        except RuntimeError as exc:
            # cmd() already redacts upstream diagnostic output. Preserve
            # the private cause, not an unrestricted Docker stderr transcript.
            raise RuntimeError("synthetic Compose topology failed") from exc
        gateway=compose("ps","-q","live-gateway")
        bridge=compose("ps","-q","live-engine-bridge")
        if not gateway or not bridge or gateway==bridge:
            raise RuntimeError("distinct test namespaces not running")
        host=host_facts(bridge,gateway,image_id)
        fabricate_runtime_test_lease(fixture,host,image_id,source_sha,
                                     token,color=color)
        # Note: wait for BOTH jobs; failing to see a finished runner is not PASS.
        g=subprocess.Popen(["docker","wait",gateway],stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,text=True)
        b=subprocess.Popen(["docker","wait",bridge],stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,text=True)
        # Monitor BOTH Docker wait processes concurrently. Waiting first on
        # gateway hides an early bridge failure for the full 840-second limit.
        exits={}
        started=time.monotonic()
        try:
            while len(exits)<2 and time.monotonic()-started<840:
                for role,proc in (("gateway",g),("bridge",b)):
                    if role in exits or proc.poll() is None:
                        continue
                    output,error=proc.communicate(timeout=3)
                    if proc.returncode!=0 or output.strip() not in ("0","1","2","3","124","137","143"):
                        raise RuntimeError("Docker wait operation failed for "+role)
                    exits[role]=output.strip()
                    if exits[role]!="0":
                        # Retain only allowlisted test failure stages/classes.
                        # Never paste unrestricted docker logs or bearer material.
                        diagnostic="unknown-typed-failure"
                        target=gateway if role=="gateway" else bridge
                        result=subprocess.run(["docker","logs","--tail","80",target],
                                              capture_output=True,text=True,timeout=8)
                        match=re.search(
                            r"synthetic (?:gateway|bridge) integration failed: "
                            r"([a-z_]{1,48}): ([A-Za-z]{1,48})",
                            result.stderr+"\n"+result.stdout)
                        if match:
                            diagnostic=match.group(1)+":"+match.group(2)
                        raise RuntimeError("synthetic "+role+" container returned "+
                                           exits[role]+"; "+diagnostic)
                if len(exits)<2:
                    time.sleep(.2)
        finally:
            for proc in (g,b):
                if proc.poll() is None:
                    proc.kill()
                try: proc.communicate(timeout=3)
                except subprocess.TimeoutExpired: pass
        if len(exits)!=2:
            raise RuntimeError("bounded Docker full-game integration timed out")
        go,bo=exits["gateway"],exits["bridge"]
        needed=("gateway/server.json","gateway/gateway-state.json","bridge/report.json")
        if any(not (out/f).is_file() for f in needed):
            raise RuntimeError("real API or UCI game output incomplete")
        # Keep only demonstrably nonsecret evidence; no docker image tar,
        # no operator signature/private key nor raw secret is retained.
        safe={"source_sha":source_sha,"color":color,
              "image_digest":image_id,"host_observation":host,
              "gateway_exit":int(go.strip()),"bridge_exit":int(bo.strip()),
              "evidence":{f:digest(out/f) for f in needed},
              "mode":"offline-fake-only","live_game_authorized":False,
              "public_bot_release":False}
        # This is only a candidate while Compose and secret cleanup remain.
        candidate=safe
    finally:
        cleanup_failed=False
        try:
            compose("down","--remove-orphans","--volumes",timeout=50)
        except (OSError,subprocess.SubprocessError,RuntimeError):
            cleanup_failed=True
        try:
            token.unlink(missing_ok=True)
            (out/"synthetic-private-key.unmounted.pem").unlink(missing_ok=True)
            shutil.rmtree(fixture)
        except OSError:
            cleanup_failed=True
        if cleanup_failed:
            try:
                (out/"cleanup-incomplete.json").write_text(json.dumps({
                    "status":"NOT_COMPLETED","phase":"compose_or_secret_cleanup",
                    "scope":"synthetic-fake-only",
                    "public_bot_release":False,"live_game_authorized":False
                },sort_keys=True)+"\n")
            except OSError:
                pass
            # Preserve the original operation failure during exception unwind;
            # refuse SUCCESS if cleanup was the only failing step.
            if sys.exc_info()[0] is None:
                raise RuntimeError("synthetic cleanup incomplete")
    candidate["compose_cleanup_ok"]=True
    write(out/"integration.json",candidate)
    return candidate

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--color",choices=("white","black"),required=True)
    p.add_argument("--image",required=True)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        summary=run(a.color,a.image,a.source_sha,a.output)
        print(json.dumps({"color":summary["color"],"mode":"offline-fake-only",
                          "completed":True,"live_game_authorized":False}))
        return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,
            subprocess.TimeoutExpired,KeyError) as exc:
        # A negative producer artifact is useful but never a completed game.
        # Store only allowlisted types, never arbitrary exception text.
        try:
            a.output.mkdir(parents=True,exist_ok=True)
            (a.output/"incomplete.json").write_text(json.dumps({
                "schema_version":1,"scope":"synthetic-fake-only",
                "status":"NOT_COMPLETED","color":a.color,
                "source_sha":a.source_sha,
                "failure_type":type(exc).__name__,
                "cause_type":type(exc.__cause__).__name__ if exc.__cause__ else None,
                "live_game_authorized":False,"public_bot_release":False
            },sort_keys=True)+"\n")
        except OSError:
            pass
        print("ONLINE-PLAY-1 fake integration NOT_COMPLETED: "+type(exc).__name__,
              file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
