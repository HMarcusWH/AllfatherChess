#!/usr/bin/env python3
"""ONLINE-1 real-engine qualification: clocks, source identity and measured cost.

This uses the fast deterministic LC0 backend. It is not strength qualification.
Every positive case requires a real legal Stockfish anchor result and a full
measured per-move certificate. Invalid-clock rejection is a separate control.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controller.runtime import load_runtime_config
from controller.replay import discover_replay_bundles,load_manifest,verify_bundle_integrity
from tests.harness.uci_session import UciSession,UciError

CONFIG=ROOT/'config/allfather.online-clock.validation.json'
RESULT=ROOT/'build/test-results/online-clock'


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def wait_bundle(root: Path, known: set[str]) -> Path:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        for run in discover_replay_bundles(root).bundles:
            if run.name not in known and (run/'route.json').is_file():
                return run
        time.sleep(.02)
    raise ValueError('online clock replay did not finalize')


def main() -> int:
    config=load_runtime_config(CONFIG)
    require(config.online_time is not None and config.hybrid_authority is None,'invalid online profile')
    require(config.shadow is not None,'missing online shadow settings')
    root=config.shadow.replay_root
    known={p.name for p in discover_replay_bundles(root).bundles}
    cases=[
        ('white-clock',{'startpos_moves':[]},'go wtime 60000 btime 30000 winc 1000 binc 0','w',None),
        ('black-clock',{'startpos_moves':['e2e4']},'go wtime 60000 btime 30000 winc 1000 binc 0','b',None),
        ('restricted-movetime',{'startpos_moves':[]},'go movetime 1200 searchmoves e2e4','w','e2e4'),
    ]
    records=[]
    with UciSession(config.backends[config.anchor].binary,cwd=ROOT,timeout=10) as oracle, UciSession(
        Path(sys.executable),cwd=ROOT,timeout=30,args=['-m','controller','--config',str(CONFIG)]) as shell:
        oracle.configure({'Threads':1,'Hash':16})
        shell.configure({'UCI_Chess960':False})
        shell.new_game()
        for label,position,command,side,restriction in cases:
            oracle.set_position(position)
            oracle.send('go perft 1')
            perft=oracle.read_until(lambda line:line.startswith('Nodes searched:'),label='legal root oracle')
            legal={m.group(1) for line in perft if (m:=re.match(r'^([a-h][1-8][a-h][1-8][qrbn]?): 1$',line))}
            require(bool(legal),'oracle returned no legal roots')
            shell.set_position(position)
            shell.ready()
            sent=time.monotonic();shell.send(command)
            if label=='white-clock':
                shell.send('isready')
            lines=shell.read_until(lambda line:line.startswith('bestmove '),label=label,timeout=5)
            observed_ms=(time.monotonic()-sent)*1000
            moves=[line.split()[1] for line in lines if line.startswith('bestmove ')]
            require(len(moves)==1 and moves[0] in legal,f'illegal/missing outward result in {label}: {moves}')
            if restriction:
                require(moves[0]==restriction,'external root restriction escaped')
            if label=='white-clock':
                require('readyok' in lines,'isready was not serviced during clock search')
            run=wait_bundle(root,known);known.add(run.name)
            require(not verify_bundle_integrity(run),f'clock replay integrity failure: {verify_bundle_integrity(run)}')
            manifest=load_manifest(run)
            plan=manifest['time_plan'];outcome=manifest['clock_outcome']
            anchor=next(stage for stage in manifest['stages'] if stage['role']=='anchor')
            route=json.loads((run/'route.json').read_text())
            require(plan['external_go_command']==command,'external request was rewritten')
            require(plan['side_to_move']==side,'incorrect clock side')
            require(plan['anchor_go_command']==anchor['command'],'actual request not bound')
            require(anchor['bestmove']==moves[0],'outward result differs from anchor')
            require(outcome['emitted_line'].split()[1]==moves[0] and outcome['output_within_deadline'],'late or false output claim')
            require(route['time_plan']==plan,'route/replay plan mismatch')
            require(route['envelope_claim']['claimed'] is True,f'full clock envelope claim denied: {route["envelope_claim"]}')
            resource=run/'resource.json'
            digest=hashlib.sha256(resource.read_bytes()).hexdigest()
            require(route['resource_measurement']['sha256']==digest,'resource hash mismatch')
            records.append({'case':label,'run_id':run.name,'side':side,'external_command':command,
                'internal_command':anchor['command'],'outward':moves[0],
                'hard_budget_ms':plan['hard_budget_ms'],'emitted_ms':outcome['emitted_ms'],
                'driver_observed_ms':observed_ms,'envelope_claim':route['envelope_claim'],
                'resource_sha256':digest,'physical_cpu_ms':route['resource_measurement']['physical_cpu_ms']})
        shell.send('go wtime 0 btime 60000')
        rejected=shell.read_until(lambda line:line.startswith('bestmove '),label='zero-clock rejection',timeout=2)
        require(rejected[-1]=='bestmove 0000','invalid clock did not fail explicitly')
        shell.ready()
        # All positive manifests are already sealed; no result is discarded.
        transcript=shell.transcript
    try:
        source_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    except (OSError,subprocess.CalledProcessError):
        source_sha=None
    RESULT.mkdir(parents=True,exist_ok=True)
    (RESULT/'uci.log').write_text('\n'.join(transcript)+'\n')
    (RESULT/'report.json').write_text(json.dumps({'schema_version':1,'source_checkout_sha':source_sha,
        'config_sha256':hashlib.sha256(CONFIG.read_bytes()).hexdigest(), 'cases':records,
        'negative_control':'zero own clock rejected explicitly; no legal fallback claimed',
        'claim':'Clock/lifecycle and measured-envelope mechanism only; random LC0, no hybrid authority, no Elo.'},indent=2)+'\n')
    print(f'Online clock contract passed: {len(records)} legal anchor results with full measured envelopes; zero-clock rejection')
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,UciError) as exc:
        RESULT.mkdir(parents=True,exist_ok=True)
        (RESULT/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'online clock contract failed: {exc}',file=sys.stderr)
        raise SystemExit(1)
