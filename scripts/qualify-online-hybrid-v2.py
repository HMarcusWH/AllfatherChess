#!/usr/bin/env python3
"""Real-process G3-v2 authority qualification on the ENGINE-OPT-V2 CPU bundle."""
from __future__ import annotations
import argparse,json,re,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import load_json,validate_hybrid,validate_reference
from controller.decision import CLOCKED_AUTHORIZATION_POLICY
from controller.replay import discover_replay_bundles,load_manifest,verify_bundle_integrity
from controller.counterfactual import verify_counterfactual_integrity
from controller.final_decision import load_final_decision_artifact,verify_final_decision_integrity
from tests.harness.uci_session import UciSession

POLICY=ROOT/"qualification/online-hybrid-v2.json"
REFERENCE_POLICY=ROOT/"qualification/online-engine-opt-v2.json"
SELECTION=ROOT/"qualification/engine-opt-v2-selection.json"
CONFIG=ROOT/"config/allfather.online-hybrid-v2.validation.json"
REFERENCE=ROOT/"config/allfather.online-engine-opt-v2.json"
RESULT=ROOT/"build/test-results/online-hybrid-v2"
MOVE_RE=re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

class QualificationError(RuntimeError): pass
def require(condition,message):
    if not condition: raise QualificationError(message)

def static_contract():
    policy=load_json(POLICY); reference=load_json(REFERENCE); hybrid=load_json(CONFIG)
    selection=load_json(SELECTION); reference_policy=load_json(REFERENCE_POLICY)
    validate_reference(reference_policy,selection,reference,require_selected=False)
    validate_hybrid(policy,reference,hybrid)
    return policy,hybrid

def wait_bundle(root:Path,known:set[str])->Path:
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        for run in discover_replay_bundles(root).bundles:
            if run.name not in known and (run/"decision"/"final.json").is_file(): return run
        time.sleep(.05)
    raise QualificationError("G3-v2 replay/final decision did not finalize")

def main(*,static_only:bool=False)->int:
    policy,config=static_contract()
    if static_only:
        print(json.dumps({"profile_id":policy["profile_id"],"static_contract":True,"passed":True},sort_keys=True)); return 0
    replay_root=ROOT/config["shadow"]["replay_root"]
    known={p.name for p in discover_replay_bundles(replay_root).bundles}
    cases=policy.get("positive_cases"); requirement=policy.get("positive_requirement") or {}
    require(isinstance(cases,list) and cases,"positive_cases must be non-empty")
    records=[]; winner=None
    for case in cases:
        label=str(case["id"]); command=str(case["command"])
        with UciSession(Path(sys.executable),cwd=ROOT,timeout=60,args=["-m","controller","--config",str(CONFIG)]) as shell:
            shell.configure({"UCI_Chess960":False}); shell.new_game()
            shell.set_position({"startpos_moves":list(case["moves"])}); shell.ready()
            started=time.monotonic(); shell.send(command)
            lines=shell.read_until(lambda line:line.startswith("bestmove "),label=f"G3-v2 bestmove {label}",timeout=10)
            shell.send("isready")
            barrier=shell.read_until(lambda line:line=="readyok",label=f"G3-v2 readyok {label}",timeout=10)
            observed=[*lines,*barrier]; elapsed=(time.monotonic()-started)*1000.0
            moves=[line.split()[1] for line in observed if line.startswith("bestmove ")]
            require(len(moves)==1 and MOVE_RE.fullmatch(moves[0]) is not None,
                    f"{label}: expected exactly one legal outward bestmove, got {moves!r}")
            run=wait_bundle(replay_root,known)
        known.add(run.name)
        require(not verify_bundle_integrity(run),f"{label}: replay integrity failed")
        final_problems=verify_final_decision_integrity(run)
        require(not final_problems,f"{label}: final decision integrity failed: {final_problems}")
        manifest=load_manifest(run); decision=load_final_decision_artifact(run)["decision"]
        authorization=decision.get("authorization") or {}; snap=decision.get("authorization_snapshot") or {}
        route_doc=json.loads((run/"route.json").read_text(encoding="utf-8"))
        resource_doc=json.loads((run/"resource.json").read_text(encoding="utf-8"))
        envelope=route_doc.get("envelope_claim") or {}; route_resource=route_doc.get("resource_measurement") or {}
        record={"case":label,"run_id":run.name,"authority":decision.get("authority"),
          "authorization_policy":authorization.get("policy"),"authorization_granted":authorization.get("authorized"),
          "anchor_move":decision.get("anchor_move"),"proposal_move":decision.get("proposal_move"),
          "emitted_move":decision.get("emitted_move"),"terminal_source":snap.get("terminal_source"),
          "route_action":snap.get("route_action"),"staged_complete":snap.get("staged_complete"),
          "envelope_claimed":envelope.get("claimed"),"resource_qualified":resource_doc.get("qualified"),
          "route_resource_qualified":route_resource.get("qualified"),"driver_observed_ms":elapsed,
          "clock_outcome":manifest.get("clock_outcome")}
        records.append(record)
        qualifies=(decision.get("authority")==requirement.get("require_authority")
          and authorization.get("policy")==CLOCKED_AUTHORIZATION_POLICY
          and authorization.get("authorized") is True
          and snap.get("terminal_source")==requirement.get("require_terminal_source")
          and snap.get("route_action")=="BUY_STAGED_VERIFY" and snap.get("route_buy_extension") is True
          and snap.get("staged_complete") is True and envelope.get("claimed") is True
          and resource_doc.get("qualified") is True and route_resource.get("qualified") is True
          and decision.get("proposal_move") is not None
          and (not requirement.get("require_non_anchor_move") or decision.get("proposal_move")!=decision.get("anchor_move")))
        if not qualifies: continue
        cf=verify_counterfactual_integrity(run); require(not cf,f"{label}: counterfactual integrity failed: {cf}")
        outcome=manifest.get("clock_outcome") or {}; require(outcome.get("output_within_deadline") is True,f"{label}: hard deadline missed")
        require((manifest.get("outward_decision") or {}).get("emitted_move")==moves[0],f"{label}: manifest/UCI move mismatch")
        winner=record; break
    require(winner is not None,"no predeclared ENGINE-OPT real-backend case demonstrated non-anchor HYBRID "+json.dumps(records,sort_keys=True))
    RESULT.mkdir(parents=True,exist_ok=True)
    report={"schema_version":1,"profile_id":policy["profile_id"],"passed":True,"positive_case":winner,"cases":records,
            "claim":"G3-v2 authority integration only; no Elo, superiority, or deployment claim."}
    (RESULT/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("G3-v2 qualification passed:",json.dumps(report,sort_keys=True)); return 0

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--static",action="store_true"); args=parser.parse_args()
    try: raise SystemExit(main(static_only=args.static))
    except Exception as exc:
        RESULT.mkdir(parents=True,exist_ok=True)
        (RESULT/"failure.txt").write_text(f"{type(exc).__name__}: {exc}\n",encoding="utf-8")
        raise
