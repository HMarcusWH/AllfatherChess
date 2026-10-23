#!/usr/bin/env python3
"""PR-A only: retain an allowlisted failure receipt, never raw container stderr."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

STAGES=frozenset({
    "UPSTREAM_LOCK_CHECK","ENGINE_SEAL_CHECK","OFFLINE_STAGE_COPY_OR_CONFIG",
    "BRIDGE_CONFIG_VALIDATE","UPSTREAM_RECOVERY_PATCH","LIVE_GUARD_SEAL",
    "SOURCE_OR_PACKAGE_PREFLIGHT",
})
CLASSES=frozenset({
    "ValueError","RuntimeError","OSError","PermissionError","FileNotFoundError",
    "CalledProcessError","TimeoutExpired","KeyError","TypeError","OtherError",
})


def sanitize(raw: str, exit_code: int) -> dict[str, object] | None:
    if exit_code == 0:
        return None
    if not 1 <= exit_code <= 255:
        raise ValueError("invalid process exit status")
    phase="STAGE_FAILURE_UNCLASSIFIED"
    failure_class="OtherError"
    for line in reversed(raw.splitlines()):
        try:
            value=json.loads(line)
        except (ValueError,TypeError):
            continue
        if not isinstance(value,dict):
            continue
        if (value.get("scope")=="synthetic-stage-preflight-only"
            and value.get("status")=="FAIL"):
            proposed=value.get("stage_code")
            if isinstance(proposed,str) and proposed in STAGES:
                phase=proposed
            kind=value.get("exception_type")
            if isinstance(kind,str) and kind in CLASSES:
                failure_class=kind
            break
    return {
        "schema_version":1,
        "scope":"synthetic-stage-preflight-only",
        "status":"NOT_COMPLETED",
        "stage_code":phase,
        "exception_type":failure_class,
        "process_exit_code":exit_code,
        "live_game_authorized":False,
        "public_bot_release":False,
        "qualifies_dry_run":False,
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--raw-stderr",type=Path,required=True)
    parser.add_argument("--exit-code",type=int,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    raw=args.raw_stderr.read_text(encoding="utf-8",errors="replace")[-16384:]
    result=sanitize(raw,args.exit_code)
    if result is not None:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
