"""Run the actual pinned upstream bot and sealed UCI under one-game supervisor.

This PID1 process has no real token or external egress. Admission signature is
ephemeral and CI-only. No source policy or public release flag is ever promoted.
"""
from __future__ import annotations
import argparse
import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT=Path("/work")
sys.path.insert(0,str(ROOT))
from deploy.live_canary.admission import SourceBinding,verify_admission
from deploy.live_canary.supervisor import LiveSupervisor
from deploy.lichess.move_journal import MoveJournal

def _read(path:Path):
    if path.is_symlink() or not path.is_file():raise RuntimeError("test input missing")
    return json.loads(path.read_text())

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--color",required=True,choices=("white","black"))
    args=p.parse_args()
    try:
        if os.getpid()!=1 or os.environ.get("ALLFATHER_BRIDGE_EGRESS_ISOLATED")!="1":
            raise RuntimeError("bridge must be a separate isolated container PID1")
        op=Path("/run/operator")
        source=SourceBinding(**_read(op/"source-binding.json"))
        lease=_read(op/"admission.json")
        verify_admission(lease,source,op/"operator-ed25519.pub.pem")
        if os.environ.get("LICHESS_BOT_TOKEN") or Path("/run/secrets").exists():
            raise RuntimeError("test bridge must not have gateway-only secret")
        supervisor=LiveSupervisor(
            binding=source,lease=lease,key=op/"operator-ed25519.pub.pem",
            policy=ROOT/"qualification/online-live-canary-v1.json",
            host_receipt=op/"host-report.json",
            release=ROOT/"dist/online3a-stage",
            bridge=ROOT/"build/vendor/lichess-bot",
            work=Path("/tmp/allfather-live-bridge"),
            state=Path("/var/lib/allfather-canary"))
        result=supervisor.run(lease_file=op/"admission.json",
                              binding_file=op/"source-binding.json",timeout_s=780)
        if not result.terminal or result.status!="TERMINAL_DURABLE":
            raise RuntimeError("simulated lifecycle did not reach terminal")
        state=Path("/var/lib/allfather-canary")
        out=Path("/results/bridge");out.mkdir(parents=True,exist_ok=True)
        journal=MoveJournal(state/"recovery/move-journal.sqlite3").snapshot()
        pgndata=(state/"game.pgn").read_text()
        def digest(path):
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        executed=Path("/tmp/allfather-live-bridge")
        source_manifest=json.loads((ROOT/"dist/online3a-stage/build/online-release/release-manifest.json").read_text())
        overlay=json.loads((executed/"bridge/recovery-overlay-manifest.json").read_text())
        lock=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
        source_record={
            "sealed_release_source_commit":source_manifest["source_commit"],
            "sealed_release_manifest_sha256":digest(ROOT/"dist/online3a-stage/build/online-release/release-manifest.json"),
            "sealed_engine_launcher_sha256":digest(ROOT/"dist/online3a-stage/deploy/bin/allfather-online"),
            "upstream_commit":lock["commit"],
            "live_effective_config_sha256":digest(executed/"config.yml"),
            "live_handler_sha256":digest(ROOT/"deploy/lichess/live_extra_game_handlers.py"),
            "patched_upstream":overlay["patched_upstream"],
            "guard_modules":overlay["guard_modules"],
            "live_policy_sha256":digest(ROOT/"qualification/online-live-canary-v1.json"),
        }
        payload={"schema_version":1,"status":result.status,
                 "source_sha":result.source_sha,
                 "game_id":result.game_id,"accepted_moves":result.accepted_moves,
                 "terminal":result.terminal,"processes":result.processes,
                 "journal":journal,"game_pgn":pgndata,
                 "one_game_lease":supervisor.ledger.snapshot(),
                 "executed_source":source_record}
        with (out/"report.json").open("x",encoding="utf-8") as f:
            json.dump(payload,f,sort_keys=True,indent=2)
        return 0
    except Exception as exc:
        print("synthetic bridge integration failed: "+type(exc).__name__,file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
