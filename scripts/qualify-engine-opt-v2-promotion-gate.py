#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.engine_opt.promotion_gate import PromotionGateError, compare_promotion_surface, evaluate_promotion_gate

def load(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise PromotionGateError(f'{path}: root must be object')
    return value

def seal(report: dict[str,Any]) -> dict[str,Any]:
    core=dict(report); core.pop('content_sha256',None)
    encoded=json.dumps(core,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    core['content_sha256']=hashlib.sha256(encoded).hexdigest(); return core

def write(path: Path, report: dict[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(seal(report),indent=2,sort_keys=True,allow_nan=False)+'\n'
    path.write_text(payload,encoding='utf-8'); print(payload,end='')

def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--aggregate',type=Path,required=True)
    ap.add_argument('--host-binding',type=Path,required=True)
    ap.add_argument('--catalog',type=Path,required=True)
    ap.add_argument('--base-sha',required=True)
    ap.add_argument('--repo-root',type=Path,default=ROOT)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    surface={"base_sha":args.base_sha,"head_sha":None,"authorized_profile_change":False}
    aggregate=None
    try:
        aggregate=load(args.aggregate); host_binding=load(args.host_binding); catalog=load(args.catalog)
        surface=compare_promotion_surface(args.repo_root.resolve(),args.base_sha)
        current_head=surface['head_sha']
        report=evaluate_promotion_gate(aggregate=aggregate,host_binding=host_binding,catalog=catalog,promotion_surface=surface,current_head=current_head)
    except (PromotionGateError,OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        report={
            "schema_version":1,"passed":False,"disposition":"BLOCKED_FAIL_CLOSED",
            "failure":{"type":type(exc).__name__,"message":str(exc)},
            "aggregate_disposition":None if aggregate is None else aggregate.get('qualification_disposition'),
            "promotion_surface":surface,
            "claim_boundary":{"canonical_b4_promotion":False,"runtime_profile_selection":False,"adaptive_stop_promoted":False,"generic_host_portability":False,"strength":False,"elo":False,"equal_compute":False,"deployment":False},
        }
        write(args.output,report)
        print(f'ENGINE-OPT-V2 canonical b4 promotion gate FAILED: {exc}',file=sys.stderr)
        return 2
    write(args.output,report); return 0

if __name__=='__main__': raise SystemExit(main())
