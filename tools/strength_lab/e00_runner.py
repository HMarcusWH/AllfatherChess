"""E00 real pinned Stockfish vs S2/E00 observer, offline only.

Nothing here sends live chess traffic or changes move authority. Raw UCI
stdout and stderr are kept separate; cgroup-v2 measurements are mandatory
for the full resource-qualification phase, not silently approximated.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

from tools.strength_lab.e00_corpus import ROOT, POLICY, build_corpus, bytes_for
from tools.strength_lab.participant_scope import ParticipantCgroup, ResourceScopeError

BUNDLE=ROOT/"build/online-engine-opt-v2"
ROOT_OUT=ROOT/"build/test-results/strength-e00"
E00_PROFILE=ROOT/"config/allfather.strength-e00-nodes.experimental.json"
NATIVE_PROFILE=ROOT/"config/allfather.strength-s2-observe.experimental.json"
ARMS=("stockfish-direct-nodes","allfather-e00-observe-nodes")
MAX_LINE=8192

def sha(path:Path) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for data in iter(lambda:f.read(1<<20),b""):
            h.update(data)
    return h.hexdigest()

def stable(data:object) -> bytes:
    return (json.dumps(data,sort_keys=True,allow_nan=False,separators=(",",":"))+"\n").encode()

def write(path:Path,data:object) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp-"+uuid.uuid4().hex)
    try:
        with tmp.open("xb") as f:
            f.write(stable(data));f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()

def head() -> str:
    source=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
    dirty=subprocess.check_output(["git","-C",str(ROOT),"status","--porcelain",
                                   "--untracked-files=no"],text=True)
    if not re.fullmatch("[0-9a-f]{40}",source) or dirty.strip():
        raise ValueError("E00 needs exact clean tracked Git source")
    return source

def preflight(profile:Path,source:str) -> tuple[Path,dict]:
    policy=json.loads(POLICY.read_text(encoding="utf-8"))
    cfg=json.loads(profile.read_text(encoding="utf-8"))
    if policy["status"]!="IMPLEMENTED_NOT_EXECUTED_OR_QUALIFIED" or any(policy["claims"].values()):
        raise ValueError("E00 source policy claims unearned authority")
    if cfg["schema_version"]!=3 or cfg["strength"]["mode"]!="observe_only":
        raise ValueError("E00 source config not observe-only v3")
    if profile==E00_PROFILE and cfg["strength"]["policy"]!="strength_e00_incumbent_observe_v1":
        raise ValueError("E00 node source config mismatch")
    if profile==NATIVE_PROFILE and cfg["strength"]["policy"]!="strength_s2_incumbent_observe_v1":
        raise ValueError("S2 native source config mismatch")
    opts=cfg["instances"]["stockfish-anchor"]["options"]
    if opts!={"Threads":1,"Hash":64,"MultiPV":1,"Move Overhead":100,
              "Ponder":False,"UCI_Chess960":False}:
        raise ValueError("E00/S2 unexpected Stockfish options")
    binary=ROOT/cfg["instances"]["stockfish-anchor"]["binary"]
    manifest_path=BUNDLE/"build-manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    record=manifest["artifacts"]["engines"]["stockfish"]
    if not binary.is_file() or not os.access(binary,os.X_OK) or \
       manifest.get("source_commit")!=source or \
       binary.resolve()!= (BUNDLE/record["path"]).resolve() or sha(binary)!=record["sha256"]:
        raise ValueError("E00 Stockfish does not match exact-head canonical engine bundle")
    network=manifest["artifacts"]["networks"]["stockfish"]
    if sha(BUNDLE/network["path"])!=network["sha256"]:
        raise ValueError("E00 Stockfish NNUE identity mismatch")
    return binary,opts

class UciParticipant:
    """One real private process tree; only UCI data can influence its chess decisions."""
    def __init__(self,arm:str,binary:Path,options:dict,profile:Path,
                 *,strict_cgroup:bool=False):
        self.arm=arm
        self.source_session_id=uuid.uuid4().hex
        self.scope=None
        self.process=None
        self.rows=[]
        self.q=queue.Queue(maxsize=65536)
        self.readers=[]
        self.errors=[]
        self.started_ns=time.monotonic_ns()
        self.counter_state="UNAVAILABLE"
        try:
            try:
                self.scope=ParticipantCgroup.create()
                self.counter_state="MEASURED"
            except ResourceScopeError:
                if strict_cgroup:
                    raise
            cmd=([str(binary)] if arm==ARMS[0] else
                 [sys.executable,"-m","controller","--config",str(profile)])
            env=os.environ.copy()
            if arm==ARMS[1] and profile==E00_PROFILE:
                env["ALLFATHER_E00_OFFLINE"]="1"
            self.process=subprocess.Popen(
                cmd,cwd=ROOT,env=env,stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                start_new_session=True,bufsize=0,
                preexec_fn=None if self.scope is None else self.scope.join_in_child)
            for name,stream in (("stdout",self.process.stdout),("stderr",self.process.stderr)):
                reader=threading.Thread(target=self._reader,args=(name,stream),
                                        name=f"e00-{arm}-{name}",daemon=True)
                self.readers.append(reader)
                reader.start()
            self.send("uci")
            hello=self.until(lambda line:line=="uciok",12,allow_bestmove=False)
            offered=set()
            for line in hello:
                if line.startswith("option name ") and " type " in line:
                    offered.add(line[len("option name "):].split(" type ",1)[0])
            if arm==ARMS[0]:
                if not set(options)<=offered:
                    raise RuntimeError("direct Stockfish missing canonical UCI option")
                for name,value in options.items():
                    value=str(value).lower() if type(value) is bool else value
                    self.send(f"setoption name {name} value {value}")
            self.ready()
        except BaseException:
            self.close()
            raise

    def _reader(self,name:str,stream) -> None:
        try:
            while True:
                line=stream.readline(MAX_LINE+1)
                if not line:break
                if len(line)>MAX_LINE or not line.endswith(b"\n"):
                    raise RuntimeError("oversized/unterminated "+name+" line")
                try:
                    text=line.rstrip(b"\r\n").decode("utf-8","strict")
                except UnicodeDecodeError as exc:
                    raise RuntimeError("non-UTF8 "+name) from exc
                try:
                    self.q.put_nowait((name,text,time.monotonic_ns()))
                except queue.Full:
                    raise RuntimeError("bounded E00 UCI stream overflow")
        except BaseException as exc:
            message=f"{type(exc).__name__}: {exc}"
            self.errors.append("UCI reader "+message)
            try:self.q.put_nowait(("error",message,time.monotonic_ns()))
            except queue.Full:pass

    def send(self,command:str) -> None:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("E00 participant exited before UCI command")
        if len(command)>4096 or "\n" in command or "\r" in command:
            raise RuntimeError("invalid UCI input line")
        if len(self.rows)>=200000:
            raise RuntimeError("bounded E00 transcript exhausted")
        self.rows.append({"direction":"stdin","line":command,"ns":time.monotonic_ns()})
        assert self.process.stdin is not None
        self.process.stdin.write((command+"\n").encode())
        self.process.stdin.flush()

    def until(self,predicate,timeout:float,*,allow_bestmove:bool) -> list[str]:
        end=time.monotonic()+timeout
        lines=[]
        while True:
            remaining=end-time.monotonic()
            if remaining<=0:
                raise TimeoutError(f"E00 {self.arm} UCI response timeout")
            try:
                origin,line,when=self.q.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"E00 {self.arm} UCI queue timeout") from exc
            if len(self.rows)>=200000:
                raise RuntimeError("bounded E00 transcript exhausted")
            self.rows.append({"direction":origin,"line":line,"ns":when})
            if origin=="error":
                raise RuntimeError("E00 reader: "+line)
            if origin=="stderr":
                continue
            if line.startswith("bestmove ") and not allow_bestmove:
                raise RuntimeError("unsolicited/duplicate E00 bestmove")
            lines.append(line)
            if predicate(line):
                return lines

    def ready(self) -> None:
        self.send("isready")
        self.until(lambda line:line=="readyok",12,allow_bestmove=False)

    def search(self,row:dict,go:str,timeout:float) -> dict:
        self.send("ucinewgame")
        self.ready()
        self.send(row["position_command"])
        before=self.scope.counters() if self.scope else None
        self.send(go)
        marker=len(self.rows)
        sent_ns=time.monotonic_ns()
        lines=self.until(lambda s:s.startswith("bestmove "),timeout,allow_bestmove=True)
        ended_ns=time.monotonic_ns()
        after=self.scope.counters() if self.scope else None
        self.ready()  # Delayed duplicate outputs before readyok are fatal.
        best=[s for s in lines if s.startswith("bestmove ")]
        if len(best)!=1:
            raise RuntimeError("E00 requires one terminal bestmove per invocation")
        return {"arm":self.arm,"session_id":self.source_session_id,
                "position_command":row["position_command"],
                "go_command":go,"stdout":lines,"bestmove":best[0],
                "search_sent_ns":sent_ns,"search_ended_ns":ended_ns,
                "wall_ms":(ended_ns-sent_ns)/1e6,
                "cpu_ms":None if before is None else
                    (after["cpu_usage_usec"]-before["cpu_usage_usec"])/1000.0,
                "cpu_usage_usec_before":None if before is None else before["cpu_usage_usec"],
                "cpu_usage_usec_after":None if after is None else after["cpu_usage_usec"],
                "memory_peak_bytes":None if after is None else after["memory_peak_bytes"],
                "resource_status":self.counter_state,
                "transcript_marker":marker}

    def close(self) -> dict:
        p=self.process
        if p is not None:
            if p.poll() is None:
                try:self.send("quit")
                except (OSError,RuntimeError):pass
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:self.errors.append("unreaped UCI process")
            # Drain both source streams AFTER the engine has exited, so any
            # delayed second bestmove or stderr framing error remains visible.
            for reader in self.readers:
                reader.join(timeout=2)
                if reader.is_alive():
                    self.errors.append("UCI reader still running after process exit")
            while True:
                try:origin,line,when=self.q.get_nowait()
                except queue.Empty:break
                self.rows.append({"direction":origin,"line":line,"ns":when})
                if origin=="error":
                    self.errors.append("post-search reader error "+line)
                if origin=="stdout" and line.startswith("bestmove "):
                    self.errors.append("unsolicited/duplicate terminal after readiness")
            for stream in (p.stdin,p.stdout,p.stderr):
                if stream is not None:
                    try:stream.close()
                    except OSError:pass
        counters=None
        leaked=False
        if self.scope is not None:
            try:
                leaked=self.scope.kill_if_populated()
                counters=self.scope.counters()
                self.scope.remove()
                if leaked:self.errors.append("child survives participant exit")
            except (ResourceScopeError,OSError) as exc:
                self.errors.append("resource scope cleanup: "+str(exc))
            self.scope=None
        self.process=None
        return {"arm":self.arm,"session_id":self.source_session_id,
                "started_ns":self.started_ns,"ended_ns":time.monotonic_ns(),
                "exit_code":None if p is None else p.returncode,
                "resource_status":self.counter_state if counters is not None else "UNAVAILABLE",
                "cpu_ms":None if counters is None else counters["cpu_usage_usec"]/1000.0,
                "memory_peak_bytes":None if counters is None else counters["memory_peak_bytes"],
                "leaked_before_cleanup":leaked,"errors":self.errors,
                "events":list(self.rows)}

def run(*,phase:str,strict_cgroup:bool=False) -> Path:
    if phase not in ("smoke","full","native-smoke"):
        raise ValueError("unknown E00 run phase")
    policy=json.loads(POLICY.read_text(encoding="utf-8"))
    source=head()
    native=phase=="native-smoke"
    profile=NATIVE_PROFILE if native else E00_PROFILE
    binary,options=preflight(profile,source)
    rows=build_corpus()
    total=policy["sample_count"] if phase=="full" else policy["smoke_count"]
    run_dir=ROOT_OUT/("e00-"+phase+"-"+uuid.uuid4().hex)
    run_dir.mkdir(parents=True,exist_ok=False)
    corpus=bytes_for(rows)
    corpus_path=run_dir/"corpus.jsonl"
    corpus_path.write_bytes(corpus)
    evidence_dir=ROOT/json.loads(profile.read_text())["strength"]["evidence_dir"]
    evidence_dir.mkdir(parents=True,exist_ok=True)
    before={p.name for p in evidence_dir.glob("*.json")}
    manifest={"schema_version":1,"kind":"strength_e00_campaign_v1",
              "phase":phase,"status":"INCOMPLETE","source_commit":source,
              "policy_sha256":sha(POLICY),"profile_sha256":sha(profile),
              "binary_sha256":sha(binary),
              "bundle_manifest_sha256":sha(BUNDLE/"build-manifest.json"),
              "corpus_sha256":hashlib.sha256(corpus).hexdigest(),
              "corpus_count":len(rows),"expected_samples":total,
              "go_command":("go wtime 30000 btime 30000 winc 1000 binc 1000"
                            if native else f"go nodes {policy['search_nodes']}"),
              "arms":list(ARMS),"samples":[],"flights":[],"participants":[],
              "errors":[],"resource_qualified":False,
              "equal_compute":False,"strength_qualified":False,
              "live_game_authorized":False,"release_authorized":False}
    write(run_dir/"manifest.json",manifest)
    participants=[]
    try:
        for arm in ARMS:
            participants.append(UciParticipant(
                arm,binary,options,profile,strict_cgroup=strict_cgroup))
        for i,row in enumerate(rows[:total]):
            go=manifest["go_command"]
            sample={"schema_version":1,"id":row["id"],"history_sha256":row["history_sha256"],
                    "occurrence":i,"arms":{},"go_command":go}
            order=(ARMS if i%2==0 else tuple(reversed(ARMS)))
            for arm in order:
                p=next(x for x in participants if x.arm==arm)
                sample["arms"][arm]=p.search(row,go,25 if native else 20)
            rel=f"samples/{row['id']}.json"
            file=run_dir/rel
            write(file,sample)
            manifest["samples"].append({"id":row["id"],"path":rel,"sha256":sha(file)})
            write(run_dir/"manifest.json",manifest)
    except BaseException as exc:
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        for p in participants:
            receipt=p.close()
            path=run_dir/(p.arm+"-session.json")
            write(path,receipt)
            manifest["participants"].append({"arm":p.arm,"path":path.name,"sha256":sha(path),
                                             "resource_status":receipt["resource_status"]})
            if receipt["errors"] or receipt["exit_code"]!=0:
                manifest["errors"].append(p.arm+" session/cleanup failure")
        fresh=sorted([p for p in evidence_dir.glob("*.json") if p.name not in before])
        for file in fresh:
            manifest["flights"].append({"path":str(file.relative_to(ROOT)),
                                       "sha256":sha(file)})
        if len(manifest["flights"])!=len(manifest["samples"]):
            manifest["errors"].append("E00 observer flight count disagrees with sample count")
        manifest["status"]="COMPLETE" if len(manifest["samples"])==total and not manifest["errors"] else "FAILED"
        manifest["resource_qualified"]=bool(
            len(participants)==2 and all(x["resource_status"]=="MEASURED"
                                         for x in manifest["participants"]))
        write(run_dir/"manifest.json",manifest)
    return run_dir
