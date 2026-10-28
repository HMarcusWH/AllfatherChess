"""S3-B single-turn REAL-ENGINE diagnostic (unplayed, observe-only).

One actual S3-A Stockfish UCI frontend remains authoritative and publishes
its native bestmove independently. In a separate offline lab participant we
run ONE real Reckless or BLAS-LC0 proposer, followed by a *different* Stockfish
MultiPV2 process, never simultaneously in the auxiliary slot. Unreviewed
procfs costs and source fixture reservations are NOT a physical cgroup claim.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid

from adapters.process.uci_process import UciProcess, UciProcessError, UciDispatchRejected
from common.search_request import parse_position_command
from controller.budget import BudgetLedger, ResourceEnvelope
from controller.strength.contracts import (TurnKey, SearchIdentity, StrengthContractError,
                                            digest, require, BESTMOVE)
from controller.strength.epoch import IncumbentEpochTracker
from controller.strength.root_frames import parse_info
from controller.strength.scheduler import StrengthAuxScheduler, CostObservationV1
from controller.strength.proposer import (ProposerRequestV1, ProposerCollector,
                                           NATIVE_SEEDS)
from controller.strength.comparison import (ComparisonRequestV1, ComparatorRoundCollector)
from controller.strength.candidates import make_pair, reconstruct_proposal
from controller.strength.integrity import verify_flight, strict_load
from tools.strength_lab.e00_runner import sha,write,head
from tools.strength_lab.s3a_rules import legal_roots,verify_proposal

ROOT=Path(__file__).resolve().parents[2]
BUNDLE=ROOT/"build/online-engine-opt-v2"
POLICY=ROOT/"qualification/strength/s3b-real-probe-v1.json"
SOURCE_PROFILE=ROOT/"config/allfather.strength-s3a-observe.experimental.json"
OLDER_PROFILE=ROOT/"config/allfather.online-hybrid-v2.validation.json"
OUTPUT_ROOT=ROOT/"build/test-results/strength-s3b"
FLIGHT_ROOT=ROOT/"build/strength-s3a-evidence"


def _checked_file(path:Path,root:Path)->Path:
    require(path.is_file() and not path.is_symlink() and
            path.resolve().is_relative_to(root.resolve()),
            "S3-B source artifact absent, linked or outside immutable bundle")
    return path


def preflight()->dict:
    require(os.environ.get("ALLFATHER_S3B_OFFLINE")=="1",
            "S3-B requires explicit offline-only experimental opt-in")
    source=head()
    policy=strict_load(POLICY)
    require(policy["status"]=="REAL_ENGINE_DIAGNOSTIC_NOT_RESOURCE_QUALIFIED" and
            policy["allow_unqualified_source_only_diagnostic"] is True and
            policy["mode"]=="observe_only" and
            all(v is False for v in policy["claims"].values()),
            "S3-B cannot run with a promoted policy")
    profile=strict_load(SOURCE_PROFILE)
    older=strict_load(OLDER_PROFILE)
    require(profile["schema_version"]==3 and
            profile["strength"]["policy"]=="strength_s3a_contracts_observe_v1" and
            profile["strength"]["mode"]=="observe_only" and
            len(profile["instances"])==1 and
            profile["instances"]["stockfish-anchor"]["options"]["MultiPV"]==1,
            "S3-B anchor profile must be the real S3A one-writer observer")
    selection=strict_load(ROOT/"qualification/engine-opt-v2-selection.json")
    require(selection["selected"]["lc0"]["backend"]=="blas" and
            selection["selected"]["lc0"]["network"]=="791556",
            "S3-B expected canonical pinned LC0 BLAS selection")
    manifest_path=_checked_file(BUNDLE/"build-manifest.json",BUNDLE)
    bundle=strict_load(manifest_path)
    require(bundle["source_commit"]==source and
            set(bundle["artifacts"]["engines"])=={"stockfish","reckless","lc0"} and
            set(bundle["artifacts"]["networks"])=={"stockfish","reckless","lc0"},
            "S3-B actual binary bundle differs from exact checked-out source")
    inputs={}
    for category in ("engines","networks"):
        for family,record in bundle["artifacts"][category].items():
            path=_checked_file(BUNDLE/record["path"],BUNDLE)
            require(sha(path)==record["sha256"] and
                    path.stat().st_size==record["size"],
                    "S3-B corrupted or substituted "+family+"/"+category)
            inputs[family+"_"+category]=path
    stockfish_path=ROOT/profile["instances"]["stockfish-anchor"]["binary"]
    require(stockfish_path.resolve()==inputs["stockfish_engines"].resolve(),
            "S3-B S2 anchor binary differs from comparator source")
    return {"source_commit":source,"policy":policy,"s3a":profile,"legacy":older,
            "manifest":bundle,"manifest_sha256":sha(manifest_path),
            "inputs":inputs,"profile_sha256":sha(SOURCE_PROFILE)}


def _procfs(pid:int|None)->dict|None:
    """Experimental process-only CPU/RSS measurement: NOT subtree accounting."""
    if sys.platform!="linux" or type(pid) is not int or pid<1:
        return None
    try:
        stat=Path(f"/proc/{pid}/stat").read_text()
        rest=stat[stat.rfind(")")+2:].split()
        usage=(int(rest[11])+int(rest[12]))/os.sysconf("SC_CLK_TCK")*1000
        status=Path(f"/proc/{pid}/status").read_text()
        rss=0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss=int(line.split()[1])*1024
        return {"pid":pid,"process_cpu_ms":usage,"process_rss_bytes":rss,
                "accounting":"PROCESS_ONLY_UNQUALIFIED"}
    except (OSError,ValueError,IndexError):
        return None


@dataclass
class NativeAnchorObservation:
    search:SearchIdentity
    allowed_roots:tuple[str,...]

    def __post_init__(self):
        self.lock=threading.RLock()
        self.info_available=threading.Event()
        self.done=threading.Event()
        self.tracker=IncumbentEpochTracker(self.search,self.allowed_roots)
        self.terminal=None
        self.terminal_ns=None
        self.events=0
        self.errors=[]

    def on_info(self,token:int,line:str)->None:
        with self.lock:
            if self.done.is_set() or token!=self.search.turn.generation:
                return
            if self.events>=8192:
                self.errors.append("ANCHOR_INFO_OVERFLOW")
                return
            self.events+=1
            frame=parse_info(line)
            if frame is None:
                return
            try:
                if self.tracker.note_frame(frame,token=token,observed_ns=time.monotonic_ns()):
                    self.info_available.set()
            except StrengthContractError as exc:
                self.errors.append(str(exc)[:160])

    def on_complete(self,token:int,line:str)->None:
        with self.lock:
            if token!=self.search.turn.generation or self.done.is_set():
                self.errors.append("ANCHOR_DUPLICATE_OR_STALE_TERMINAL")
                return
            observed=time.monotonic_ns()
            try:
                match=BESTMOVE.fullmatch(line)
                require(match is not None and match.group(1)!="0000",
                        "S3-B anchor published invalid move")
                self.tracker.terminal(match.group(1),token=token,
                                      observed_ns=observed)
            except StrengthContractError as exc:
                self.errors.append(str(exc)[:160])
            self.terminal=line
            self.terminal_ns=observed
            self.done.set()

    def current(self):
        with self.lock:
            return self.tracker.current

    def permits(self,epoch:int)->bool:
        with self.lock:
            return (not self.done.is_set() and
                    not self.errors and self.tracker.root_epoch==epoch and
                    self.tracker.current is not None)


def _wait_task(process:UciProcess,collector,anchor:NativeAnchorObservation,
               epoch:int,token:int,deadline:float)->bool:
    """Stop a stale/late helper by its EXACT search token, never the anchor."""
    completed=collector.event if hasattr(collector,"event") else collector._event
    while not completed.wait(0.075):
        if not anchor.permits(epoch) or time.monotonic()>=deadline:
            stopped=process.stop_search(token,timeout=.25)
            if not stopped and process.active_token==token:
                process.kill_search(token)
            completed.wait(.35)
            return False
    return anchor.permits(epoch)


def _cost(role:str,record:dict,*,source:str,profile:str,host:str)->CostObservationV1:
    # This is an intentionally UNREVIEWED *diagnostic* allowance. No existing
    # production strength scheduler may spend these values or use these grants.
    return CostObservationV1(
        semantic_role=role,source_commit=source,profile_sha256=profile,
        host_domain_sha256=host,evidence_sha256=digest(record),
        native_limit=record["nodes"],native_semantics=record["family"]+".uci_nodes",
        reserved_cpu_ms=record["reserved_cpu_ms"],reserved_gpu_ms=0.,
        wall_bound_ms=record["wall_bound_ms"],
        memory_peak_bytes=record["peak_memory_seed_bytes"],
        review_status="SOURCE_TEST_FIXTURE")


def _open(name:str,binary:Path,options:dict,*,args:list[str]|None=None,
          env:dict|None=None)->UciProcess:
    p=UciProcess(name=name,binary=binary,cwd=ROOT,args=args or [],
                 environment=env or {},timeout=12.)
    p.start()
    if options:
        p.configure(options)
    return p


def _join_flight(existing:set[str],expected_history_sha:str,
                 expected_bestmove:str)->dict:
    current={p.name for p in FLIGHT_ROOT.glob("*.json")}
    changed=sorted(current-existing)
    require(len(changed)==1,"S3-B needs exactly one native S3A flight")
    path=_checked_file(FLIGHT_ROOT/changed[0],FLIGHT_ROOT)
    doc=strict_load(path)
    verdict=verify_flight(doc)
    require(verdict["status"]=="PUBLISHED_ANCHOR" and
            verdict["replacement_count"]==0 and
            verdict["qualified"] is False,
            "S3-B did not retain an actual one-writer Stockfish publication")
    begins=[r["data"] for r in doc["events"] if r["event"]=="begin"]
    pubs=[r["data"] for r in doc["events"] if r["event"]=="published"]
    require(len(begins)==len(pubs)==1 and
            begins[0]["turn"]["history_sha256"]==expected_history_sha and
            pubs[0]["chosen_line"]==expected_bestmove,
            "S3-B S2 flight cannot be reconciled to actual published bestmove")
    return {"flight":doc,"flight_sha256":sha(path),"flight_source_path":str(path)}


def run(*,family:str,position_command:str,go_command:str,
        output:Path|None=None)->Path:
    cfg=preflight()
    policy=cfg["policy"]
    require(family in ("reckless","lc0"),"S3-B requires one specialist only")
    require(go_command in policy["supported_native_go_commands"],
            "S3-B native analysis command must be a source-frozen workload")
    parsed=parse_position_command(position_command)
    legal=legal_roots(position_command)
    require(len(legal)>2,"S3-B needs a genuine nontrivial legal position")
    if output is None:
        output=OUTPUT_ROOT/("s3b-"+family+"-"+uuid.uuid4().hex)
    require(output.parent.resolve()==OUTPUT_ROOT.resolve() and not output.exists(),
            "S3-B run must use fresh controlled output under build")
    output.mkdir(parents=True)
    before_flights={p.name for p in FLIGHT_ROOT.glob("*.json")} if FLIGHT_ROOT.exists() else set()
    source=cfg["source_commit"]
    term=TurnKey(session_id=uuid.uuid4().hex,generation=1,
                 history_sha256=hashlib.sha256(position_command.encode()).hexdigest(),
                 side_to_move=parsed.side_to_move,variant="standard",
                 restriction_sha256=digest({"searchmoves":[]}),
                 search_nonce=secrets.token_hex(16))
    aopts=cfg["s3a"]["instances"]["stockfish-anchor"]["options"]
    asearch=SearchIdentity(term,source,cfg["profile_sha256"],
                           sha(cfg["inputs"]["stockfish_engines"]),
                           digest({"options":aopts,"args":[]}))
    anchor=NativeAnchorObservation(asearch,())
    # Host identity is merely a diagnostic current-domain fingerprint.
    host_rec={"system":sys.platform,"machine":os.uname().machine,
              "allowed_cpus":sorted(os.sched_getaffinity(0)),
              "policy_sha256":sha(POLICY)}
    host_id=digest(host_rec)
    nparams=policy["proposer"][family]
    compare_params=policy["comparator"]
    schedule=None
    started={}
    rows={"schema_version":1,"kind":"strength_s3b_offline_diagnostic_v1",
          "source_commit":source,"source_profile_sha256":cfg["profile_sha256"],
          "bundle_manifest_sha256":cfg["manifest_sha256"],
          "policy_sha256":sha(POLICY),"host_domain_sha256":host_id,
          "family":family,"position_command":position_command,
          "external_go_command":go_command,"turn":term.as_dict(),
          "anchor_search":asearch.as_dict(),"legal_roots":list(legal),
          "anchor_terminal":None,"anchor_terminal_ns":None,
          "proposer":None,"comparison_rounds":[],"scheduler":None,
          "flight":None,"errors":[],"optional_denial":None,"status":"NOT_RUN",
          "resource_qualified":False,"strength_qualified":False,
          "equal_compute":False,"move_authorized":False,
          "public_bot_release":False,"live_game_authorized":False}
    try:
        helper_opts=dict(cfg["legacy"]["instances"][family+"-shadow"]["options"])
        if family=="lc0":
            # Explicitly versioned S3-B unplayed MultiPV2 proposal profile:
            # retain the canonical BLAS engine, backend and network source.
            require(policy["proposer"]["lc0"]["multipv"]==2,
                    "S3B LC0 full-root two-rank policy changed")
            helper_opts["MultiPV"]=2
        helper_args=list(cfg["legacy"]["instances"][family+"-shadow"].get("args",[]))
        helper_env=dict(cfg["legacy"]["instances"][family+"-shadow"].get("environment",{}))
        compare_opts=dict(aopts)
        compare_opts.update({"Threads":1,"Hash":128,"MultiPV":2})
        helper=_open("s3b-"+family,cfg["inputs"][family+"_engines"],helper_opts,
                     args=helper_args,env=helper_env)
        started["helper"]=helper
        comparator=_open("s3b-stockfish-comparator",
                         cfg["inputs"]["stockfish_engines"],compare_opts)
        started["comparator"]=comparator
        # The only process exposing chess output is a REAL S3A native-clock
        # controller using its original UciFrontend single-writer path.
        s3a=_open("s3b-native-stockfish-anchor",Path(sys.executable),{},
                  args=["-m","controller","--config",str(SOURCE_PROFILE)],
                  env={"ALLFATHER_S3A_OFFLINE":"1"})
        started["anchor"]=s3a
        total_seed=(policy["anchor_memory_seed_bytes"]+
                    policy["proposer"][family]["peak_memory_seed_bytes"]+
                    policy["comparator"]["peak_memory_seed_bytes"])
        current_rss={name:_procfs(obj.pid) for name,obj in started.items()}
        rows["startup_memory"]={
            "seed_sum_bytes":total_seed,
            "total_seed_ceiling_bytes":policy["memory_seed_bytes"],
            "observed_process_only_rss":current_rss,
            "qualifying_peak_measured":False}
        require(total_seed<=policy["memory_seed_bytes"] and
                all(item is not None for item in current_rss.values()) and
                sum(item["process_rss_bytes"] for item in current_rss.values())
                    <=policy["memory_seed_bytes"],
                "S3B both idle sidecars plus anchor exceed source memory ceiling")
        for item in started.values():
            item.new_game()
            item.ready()
            item.send_position(position_command)
        a_start=time.monotonic()
        s3a.start_search(go_command,token=1,
                         on_info=anchor.on_info,
                         on_complete=anchor.on_complete)
        if not anchor.info_available.wait(policy["first_incumbent_timeout_s"]):
            rows["status"]="NO_INCUMBENT_BEFORE_DEADLINE"
        else:
            # First PV root changes rapidly during shallow Stockfish search.
            # Bind the latest genuine epoch after a source-frozen observation
            # window; later changes still cancel optional work atomically.
            if policy["incumbent_stabilization_s"]>0:
                anchor.done.wait(policy["incumbent_stabilization_s"])
            checkpoint=anchor.current()
            rows["incumbent_snapshot"]=(None if checkpoint is None
                else {"root":checkpoint.move,"epoch":checkpoint.root_epoch,
                      "progress_seq":checkpoint.progress_seq,
                      "depth":checkpoint.depth})
            if checkpoint is None or anchor.done.is_set():
                rows["status"]="ANCHOR_ALREADY_TERMINAL"
            else:
                epoch=checkpoint.root_epoch
                # Only a source-fixture diagnostic cost is present. This
                # scheduler is NOT exposed to actual live/backend authority.
                proposed=_cost("PROPOSE_LC0" if family=="lc0" else "PROPOSE_RECKLESS",
                              nparams,source=source,profile=cfg["profile_sha256"],
                              host=host_id)
                reserved=_cost("COMPARE_PAIR",compare_params,source=source,
                               profile=cfg["profile_sha256"],host=host_id)
                total_wall=policy["diagnostic_wall_budget_ms"]
                envelope=ResourceEnvelope(
                    wall_ms=total_wall,cpu_ms=policy["diagnostic_cpu_budget_ms"],
                    verification_reserve_fraction=policy["verification_reserve_fraction"],
                    controller_overhead_reserve_ms=policy["controller_overhead_reserve_ms"])
                ledger=BudgetLedger(envelope,started=a_start)
                scope=StrengthAuxScheduler(
                    anchor_search=asearch,ledger=ledger,root_epoch=epoch,
                    host_domain_sha256=host_id,
                    total_memory_bytes=policy["memory_seed_bytes"],
                    anchor_memory_bytes=policy["anchor_memory_seed_bytes"],
                    stop_cleanup_wall_ms=policy["stop_cleanup_wall_ms"],
                    allow_source_fixture=True)
                schedule=scope
                action="LC0_ONLY" if family=="lc0" else "RECKLESS_ONLY"
                accepted=scope.prepare(action=action,proposer_cost=proposed,
                                       comparator_cost=reserved,
                                       current_root_epoch=epoch,
                                       native_slack_wall_ms=policy["diagnostic_wall_budget_ms"])
                rows["action"]=accepted
                if accepted=="NONE":
                    rows["status"]="RESOURCE_PLANNING_DENIED"
                elif not anchor.permits(epoch):
                    scope.abort("ANCHOR_CHANGED_BEFORE_HELPER")
                    rows["status"]="STALE_ANCHOR_BEFORE_HELPER"
                else:
                    scope.begin_proposer(turn=term,epoch=epoch)
                    ps=SearchIdentity(term,source,cfg["profile_sha256"],
                                      sha(cfg["inputs"][family+"_engines"]),
                                      digest({"options":helper_opts,"args":helper_args}))
                    preq=ProposerRequestV1(
                        asearch,ps,checkpoint,position_command,legal,(),
                        family,ps.binary_sha256,
                        sha(cfg["inputs"]["lc0_networks"]) if family=="lc0" else None,
                        nparams["nodes"],secrets.token_hex(16))
                    prop=ProposerCollector(preq,token=1)
                    h0=_procfs(helper.pid)
                    deadline=time.monotonic()+nparams["wall_bound_ms"]/1000.
                    helper.start_search(preq.search_command,token=1,
                                        on_info=prop.on_info,
                                        on_complete=prop.on_complete,
                                        permit=lambda:anchor.permits(epoch),
                                        dispatch_gate=anchor.lock)
                    active=_wait_task(helper,prop,anchor,epoch,1,deadline)
                    h1=_procfs(helper.pid)
                    cpu=None if h0 is None or h1 is None else max(
                        0.,h1["process_cpu_ms"]-h0["process_cpu_ms"])
                    resource={"domain":"PROCESS_ONLY_UNQUALIFIED","before":h0,
                              "after":h1,"cpu_delta_ms":cpu}
                    settlement=digest(resource)
                    if active:
                        proposal_result=prop.wait(.25,grant_digest=scope.proposer.task_digest,
                                                   settlement_sha256=settlement)
                        rows["proposer"]=proposal_result
                    # Missing actual whole-participant resource never gains
                    # authority. Process CPU is only an exploratory estimate.
                    scope.settle_proposer(turn=term,epoch=epoch,actual_cpu_ms=cpu,
                                          succeeded=active and
                                          rows["proposer"] is not None and
                                          rows["proposer"]["status"]=="PROPOSED")
                    rows["proposer_resource"]=resource
                    if scope.state=="PROPOSER_SETTLED" and anchor.permits(epoch):
                        proposal=reconstruct_proposal(rows["proposer"]["proposal"])
                        verify_proposal(proposal.as_dict())
                        pair=make_pair(proposal,anchor.current())
                        scope.begin_comparator(turn=term,epoch=epoch)
                        options_digest=digest({"options":compare_opts,"args":[]})
                        cs=SearchIdentity(term,source,cfg["profile_sha256"],
                                          asearch.binary_sha256,options_digest)
                        comp0=_procfs(comparator.pid)
                        for round_no,nodes in enumerate(compare_params["round_nodes"],1):
                            if not anchor.permits(epoch):
                                rows["status"]="ANCHOR_CHANGED_DURING_COMPARISON"
                                break
                            creq=ComparisonRequestV1(
                                pair,cs,asearch.binary_sha256,
                                sha(cfg["inputs"]["stockfish_networks"]),
                                cfg["manifest_sha256"],round_no,nodes,
                                secrets.token_hex(16))
                            collector=ComparatorRoundCollector(creq)
                            comparator.start_search(creq.go_command,
                                                    token=round_no,
                                                    on_info=collector.on_info,
                                                    on_complete=collector.on_complete,
                                                    permit=lambda:anchor.permits(epoch),
                                                    dispatch_gate=anchor.lock)
                            round_deadline=time.monotonic()+compare_params["round_timeout_s"]
                            active=_wait_task(comparator,collector,anchor,epoch,
                                              round_no,round_deadline)
                            if not active:
                                rows["status"]="COMPARATOR_STOPPED_OR_STALE"
                                break
                            result=collector.wait(.3)
                            rows["comparison_rounds"].append(result)
                            comparator.ready()
                        comp1=_procfs(comparator.pid)
                        ccpu=None if comp0 is None or comp1 is None else max(
                            0.,comp1["process_cpu_ms"]-comp0["process_cpu_ms"])
                        rows["comparator_resource"]={
                            "domain":"PROCESS_ONLY_UNQUALIFIED",
                            "before":comp0,"after":comp1,"cpu_delta_ms":ccpu}
                        scope.settle_comparator(
                            turn=term,epoch=epoch,actual_cpu_ms=ccpu,
                            succeeded=(anchor.permits(epoch) and
                                       len(rows["comparison_rounds"])==
                                       len(compare_params["round_nodes"]) and
                                       all(x["disposition"]=="COMPLETE_CP"
                                           for x in rows["comparison_rounds"])))
                        if scope.state=="DONE":
                            rows["status"]="COMPARISON_RECORDED_UNQUALIFIED"
                        elif rows["status"]=="NOT_RUN":
                            rows["status"]="COMPARATOR_DENIED_OR_UNQUALIFIED"
                    elif rows["status"]=="NOT_RUN":
                        rows["status"]="NO_ALTERNATIVE_OR_STALE"
        # Do not delay real anchor publication for ANY helper work.
        if not anchor.done.wait(policy["anchor_wait_timeout_s"]):
            rows["errors"].append("ANCHOR_MISSING_TERMINAL")
            rows["status"]="INCOMPLETE"
        rows["anchor_terminal"]=anchor.terminal
        rows["anchor_terminal_ns"]=anchor.terminal_ns
        if anchor.errors:
            rows["errors"].extend(anchor.errors)
            rows["status"]="INCOMPLETE"
    except UciDispatchRejected:
        # An epoch change is a correct refusal, not a reason to
        # terminate or forget the independent Stockfish anchor output.
        rows["optional_denial"]="STALE_DISPATCH_REFUSED"
        rows["status"]="OPTIONAL_ABSTAINED_STALE"
        if schedule is not None:
            schedule.abort("STALE_OPTIONAL_DISPATCH")
    except (OSError,ValueError,TimeoutError,UciProcessError,
            StrengthContractError,RuntimeError) as exc:
        rows["errors"].append(type(exc).__name__+": "+str(exc)[:220])
        rows["status"]="INCOMPLETE"
    finally:
        if "anchor" in started:
            # Native Stockfish owns its terminal even if optional work failed.
            if not anchor.done.wait(policy["anchor_wait_timeout_s"]):
                rows["errors"].append("ANCHOR_MISSING_TERMINAL")
                rows["status"]="INCOMPLETE"
            rows["anchor_terminal"]=anchor.terminal
            rows["anchor_terminal_ns"]=anchor.terminal_ns
            if anchor.errors:
                rows["errors"].extend(anchor.errors)
                rows["status"]="INCOMPLETE"
        if schedule is not None:
            schedule.finish()
            rows["scheduler"]=schedule.report()
        rows["engine_stderr_tails"]={
            label:list(obj.stderr_tail)[-120:]
            for label,obj in started.items()}
        for label in ("helper","comparator","anchor"):
            obj=started.get(label)
            if obj is not None:
                try:obj.close()
                except (OSError,ValueError,UciProcessError,RuntimeError) as exc:
                    rows["errors"].append(label+" cleanup: "+str(exc)[:200])
                    rows["status"]="INCOMPLETE"
        if rows["anchor_terminal"] is not None:
            try:
                rows["flight"]=_join_flight(
                    before_flights,term.history_sha256,rows["anchor_terminal"])
            except (StrengthContractError,OSError,ValueError) as exc:
                rows["errors"].append("S2 flight join: "+str(exc)[:200])
                rows["status"]="INCOMPLETE"
        rows["execution_integrity"]="INCOMPLETE" if rows["errors"] else "RECORDED"
        rows["resource_qualified"]=False
        rows["strength_qualified"]=False
        rows["equal_compute"]=False
        rows["move_authorized"]=False
        # No signed evidence claim is accepted merely from this producer's
        # success status. Independent verifier rechecks raw history/moves.
        if rows["flight"] is not None:
            # Never serialize a path to execute from untrusted evidence.
            rows["flight"].pop("flight_source_path",None)
        write(output/"experiment.json",rows)
        write(output/"artifact-index.json",{
            "schema_version":1,"kind":"strength_s3b_artifact_index_v1",
            "experiment_sha256":sha(output/"experiment.json"),
            "source_commit":source,"source_policy_sha256":sha(POLICY),
            "authority":{"move":False,"public":False,"release":False}})
    return output


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family",choices=("reckless","lc0"),default="reckless")
    parser.add_argument("--position",default="position startpos moves e2e4")
    parser.add_argument("--go",default="go wtime 300000 btime 300000 winc 0 binc 0")
    parser.add_argument("--verify",type=Path)
    parser.add_argument("--require-comparison",action="store_true",
                        help="fail unless real proposer and both comparison rounds are reconstructed")
    args=parser.parse_args()
    if args.verify is not None:
        from tools.strength_lab.s3b_verify import verify
        report=verify(args.verify)
        print(json.dumps(report,sort_keys=True))
        return 0 if report["execution_integrity"]=="RECORDED" else 2
    folder=run(family=args.family,position_command=args.position,
               go_command=args.go)
    from tools.strength_lab.s3b_verify import verify
    report=verify(folder)
    print(json.dumps({"run":str(folder),**report},sort_keys=True))
    sufficient=(report["execution_integrity"]=="RECORDED" and
                report["native_anchor_published"] is True and
                (not args.require_comparison or
                 (report["actual_proposal_observed"] is True and
                  report["comparator_rounds_reconstructed"]==2)))
    return 0 if sufficient else 2


if __name__=="__main__":
    raise SystemExit(main())
