#!/usr/bin/env python3
"""ONLINE-2 end-to-end real-network online CPU profile qualification."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from controller.online_profile import OnlineProfileError,load_json,sha256_file,validate_bundle_manifest,validate_policy,validate_runtime_config as validate_online_runtime_config,verify_bundle_files
from controller.replay import discover_replay_bundles,load_manifest,verify_bundle_integrity
from controller.runtime import BackendManager,load_runtime_config
from tests.harness.uci_session import UciError,UciSession

POLICY_PATH=ROOT/"qualification/online-cpu-reference.json"
CONFIG_PATH=ROOT/"config/allfather.online.cpu-reference.json"
LC0_LOCK_PATH=ROOT/"qualification/lc0-strength.lock.json"
LC0_PROFILE_PATH=ROOT/"qualification/lc0-strength-profile.json"
LC0_REPORT_PATH=ROOT/"build/test-results/lc0-strength/report.json"
RESULT_DIR=ROOT/"build/test-results/online-profile"
MOVE_RE=re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
class ContractError(RuntimeError): pass
def require(c,m):
    if not c: raise ContractError(m)
def wait_bundle(root,known):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        for run in discover_replay_bundles(root).bundles:
            if run.name not in known and (run/"route.json").is_file(): return run
        time.sleep(.03)
    raise ContractError("ONLINE-2 replay did not finalize")
def hardware_probe():
    out=subprocess.check_output([sys.executable,str(ROOT/"scripts/online-hardware-probe.py")],cwd=ROOT,text=True)
    data=json.loads(out); require(isinstance(data,dict),"hardware probe did not return object"); return data
def validate_hardware(hw,mode,source_sha):
    require(hw.get("commit_sha")==source_sha,"hardware/source SHA mismatch")
    require(hw.get("system")=="Linux",f"ONLINE-2 requires Linux, got {hw.get('system')!r}")
    require(hw.get("architecture") in {"x86_64","amd64"},f"ONLINE-2 requires x86_64, got {hw.get('architecture')!r}")
    require(bool(hw.get("cpu_model")),"CPU model missing")
    mem=hw.get("memory_bytes"); require(isinstance(mem,int) and not isinstance(mem,bool) and mem>0,"positive memory evidence missing")
    tc=hw.get("toolchain"); require(isinstance(tc,dict),"toolchain evidence missing")
    for name in ("gcc","g++","rustc","cargo","meson","ninja","pkg-config","protoc"):
        require(isinstance(tc.get(name),str) and tc[name].strip(),f"toolchain component missing: {name}")
    if mode=="reference":
        rel=hw.get("os_release")
        require(isinstance(rel,dict) and rel.get("ID")=="ubuntu" and rel.get("VERSION_ID")=="24.04",f"reference requires Ubuntu 24.04: {rel!r}")
        require(hw.get("runner_environment")=="github-hosted","reference requires GitHub-hosted runner")
        require(hw.get("runner_class")=="github-hosted-ubuntu-24.04-cpu-reference","reference runner class mismatch")
def verify_contract_hashes(manifest):
    expected={
      "vendor_lock_sha256":sha256_file(ROOT/"vendor.lock.json"),
      "online_policy_sha256":sha256_file(POLICY_PATH),
      "runtime_config_sha256":sha256_file(CONFIG_PATH),
      "lc0_strength_lock_sha256":sha256_file(LC0_LOCK_PATH),
      "lc0_strength_profile_sha256":sha256_file(LC0_PROFILE_PATH),
    }
    require(manifest.get("contracts")==expected,f"build manifest contract hashes mismatch: {manifest.get('contracts')!r}")
def validate_lc0_reference(report,bundle,lock,source_sha):
    require(report.get("commit_sha")==source_sha,"LC0 reference source SHA mismatch")
    require(report.get("requested_backend")=="blas" and report.get("observed_backend")=="blas","LC0 BLAS not independently observed")
    binary=report.get("binary"); network=report.get("network")
    require(isinstance(binary,dict) and isinstance(network,dict),"LC0 reference identities missing")
    require(binary.get("sha256")==bundle["engines"]["lc0"]["sha256"],"bundle LC0 differs from qualified LC0 binary")
    require(network.get("sha256")==lock["network"]["sha256"]==bundle["networks"]["lc0"]["sha256"],"LC0 network identity mismatch")
def find_lc0_semantics(run,manifest):
    out=set(); streams=manifest.get("streams")
    if not isinstance(streams,list): return out
    rec=next((x for x in streams if isinstance(x,dict) and x.get("instance")=="lc0-shadow"),None)
    if not isinstance(rec,dict) or not isinstance(rec.get("path"),str): return out
    p=run/rec["path"]
    if not p.is_file(): return out
    for raw in p.read_text(encoding="utf-8").splitlines():
        try: event=json.loads(raw)
        except json.JSONDecodeError: continue
        if event.get("event_type")!="candidate.update": continue
        for ev in (event.get("candidate") or {}).get("evaluations") or []:
            if isinstance(ev,dict) and isinstance(ev.get("semantics"),str): out.add(ev["semantics"])
    return out
def run_cases(config):
    require(config.online_time is not None and config.hybrid_authority is None and config.refinement is None,"ONLINE-2 authority/refinement boundary changed")
    require(config.shadow is not None,"shadow settings missing")
    replay_root=config.shadow.replay_root; known={p.name for p in discover_replay_bundles(replay_root).bundles}
    cases=[
      ("white-clock",{"startpos_moves":[]},"go wtime 60000 btime 30000 winc 1000 binc 0","w",None),
      ("black-clock",{"startpos_moves":["e2e4"]},"go wtime 60000 btime 30000 winc 1000 binc 0","b",None),
      ("restricted-movetime",{"startpos_moves":[]},"go movetime 1200 searchmoves e2e4","w","e2e4"),
    ]
    records=[]; semantics=set()
    with UciSession(config.backends[config.anchor].binary,cwd=ROOT,timeout=10) as oracle, UciSession(
        Path(sys.executable),cwd=ROOT,timeout=40,args=["-m","controller","--config",str(CONFIG_PATH)]) as shell:
      oracle.configure({"Threads":1,"Hash":16}); shell.configure({"UCI_Chess960":False}); shell.new_game()
      for label,position,command,side,restriction in cases:
        oracle.set_position(position); oracle.send("go perft 1")
        perft=oracle.read_until(lambda l:l.startswith("Nodes searched:"),label="legal root oracle")
        legal={m.group(1) for line in perft if (m:=re.match(r"^([a-h][1-8][a-h][1-8][qrbn]?): 1$",line))}
        require(bool(legal),f"{label}: no legal roots")
        shell.set_position(position); shell.ready(); started=time.monotonic(); shell.send(command)
        lines=shell.read_until(lambda l:l.startswith("bestmove "),label=label,timeout=8); elapsed=(time.monotonic()-started)*1000
        moves=[l.split()[1] for l in lines if l.startswith("bestmove ")]
        require(len(moves)==1 and MOVE_RE.fullmatch(moves[0]) is not None and moves[0] in legal,f"{label}: invalid outward move {moves!r}")
        if restriction: require(moves[0]==restriction,f"{label}: root restriction escaped")
        run=wait_bundle(replay_root,known); known.add(run.name)
        problems=verify_bundle_integrity(run); require(not problems,f"{label}: replay integrity failed {problems}")
        manifest=load_manifest(run); plan=manifest.get("time_plan"); outcome=manifest.get("clock_outcome")
        require(isinstance(plan,dict) and isinstance(outcome,dict),f"{label}: timing evidence missing")
        anchor=next(s for s in manifest["stages"] if isinstance(s,dict) and s.get("role")=="anchor")
        require(plan.get("external_go_command")==command and plan.get("side_to_move")==side and plan.get("anchor_go_command")==anchor.get("command"),f"{label}: TimePlan binding mismatch")
        require(anchor.get("bestmove")==moves[0],f"{label}: ONLINE-2 is not anchor-authoritative")
        require(outcome.get("output_within_deadline") is True,f"{label}: hard deadline missed")
        route=json.loads((run/"route.json").read_text(encoding="utf-8")); claim=route.get("envelope_claim") or {}
        require(claim.get("claimed") is True,f"{label}: measured envelope claim denied {claim!r}")
        lc0=(manifest.get("engines") or {}).get("lc0-shadow") or {}
        require(lc0.get("environment")=={"OPENBLAS_NUM_THREADS":"1","GOTO_NUM_THREADS":"1","OMP_NUM_THREADS":"1"},f"{label}: LC0 environment not replay-bound")
        semantics.update(find_lc0_semantics(run,manifest))
        records.append({"case":label,"run_id":run.name,"side":side,"external_command":command,"internal_command":anchor.get("command"),"outward":moves[0],"driver_observed_ms":elapsed,"hard_budget_ms":plan.get("hard_budget_ms"),"physical_cpu_ms":(route.get("resource_measurement") or {}).get("physical_cpu_ms"),"envelope_claim":claim})
      shell.send("go wtime 0 btime 60000")
      rejected=shell.read_until(lambda l:l.startswith("bestmove "),label="zero-clock rejection",timeout=2)
      require(rejected[-1]=="bestmove 0000","zero-clock request did not fail explicitly"); shell.ready()
      transcript=shell.transcript
    require("lc0.uci_score.WDL_mu" in semantics,f"WDL_mu telemetry missing: {sorted(semantics)}")
    (RESULT_DIR/"uci.log").write_text("\n".join(transcript)+"\n",encoding="utf-8")
    return records
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--mode",choices=["reference","deployment"],default="reference"); parser.add_argument("--lc0-report",type=Path,default=LC0_REPORT_PATH); args=parser.parse_args()
    policy=load_json(POLICY_PATH); config_doc=load_json(CONFIG_PATH); vendor=load_json(ROOT/"vendor.lock.json"); lock=load_json(LC0_LOCK_PATH); strength=load_json(LC0_PROFILE_PATH)
    validate_policy(policy,strength); validate_online_runtime_config(config_doc,lock,strength,policy,vendor)
    bundle_root=ROOT/policy["bundle_root"]; manifest_path=bundle_root/"build-manifest.json"; manifest=load_json(manifest_path)
    validate_bundle_manifest(manifest,policy,vendor,lock); verify_contract_hashes(manifest); bundle=verify_bundle_files(bundle_root,manifest)
    source_sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    require(manifest.get("source_commit")==source_sha,"bundle source differs from checkout")
    if os.environ.get("ALLFATHER_SOURCE_SHA"): require(os.environ["ALLFATHER_SOURCE_SHA"]==source_sha,"workflow source differs from checkout")
    hw=hardware_probe(); validate_hardware(hw,args.mode,source_sha)
    report=load_json(args.lc0_report); validate_lc0_reference(report,bundle,lock,source_sha)
    config=load_runtime_config(CONFIG_PATH); manager=BackendManager(config)
    try:
      for instance,identity in manager.engine_identity.items():
        family=identity["engine"]; require(identity.get("binary_sha256")==bundle["engines"][family]["sha256"],f"{instance}: runtime binary differs from bundle")
      weights=manager.engine_identity["lc0-shadow"].get("artifacts",{}).get("weights",{})
      require(weights.get("sha256")==bundle["networks"]["lc0"]["sha256"],"runtime LC0 weights differ from bundle")
    finally: manager.close()
    RESULT_DIR.mkdir(parents=True,exist_ok=True); records=run_cases(config)
    hw_path=RESULT_DIR/"hardware.json"; hw_path.write_text(json.dumps(hw,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    payload={"schema_version":1,"profile_id":policy["profile_id"],"mode":args.mode,"source_commit":source_sha,
      "contracts":{"policy_sha256":sha256_file(POLICY_PATH),"config_sha256":sha256_file(CONFIG_PATH),"build_manifest_sha256":sha256_file(manifest_path),"lc0_reference_report_sha256":sha256_file(args.lc0_report),"hardware_sha256":sha256_file(hw_path)},
      "bundle":bundle,"hardware":hw,"cases":records,"negative_control":"zero own clock rejected explicitly; no legal fallback claimed","authority":"stockfish-anchor",
      "claim":"ONLINE-2 operational qualification only: portable CPU-target build, real LC0 BLAS inference, pinned network bytes, clock-safe execution and measured CPU envelope. No hybrid authority, learned SKIP, Elo or superiority claim."}
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False); payload["report_id"]="onlineq-"+hashlib.sha256(canonical.encode()).hexdigest()[:16]
    (RESULT_DIR/"report.json").write_text(json.dumps(payload,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print(f"ONLINE-2 qualification passed: report={payload['report_id']} cases={len(records)} authority=stockfish-anchor"); return 0
if __name__=="__main__":
  try: raise SystemExit(main())
  except (ContractError,OnlineProfileError,UciError,OSError,RuntimeError,ValueError,subprocess.CalledProcessError) as exc:
    RESULT_DIR.mkdir(parents=True,exist_ok=True); msg=f"{type(exc).__name__}: {exc}"; (RESULT_DIR/"failure.txt").write_text(msg+"\n",encoding="utf-8"); print("ONLINE-2 qualification failed: "+msg,file=sys.stderr); raise SystemExit(1)
