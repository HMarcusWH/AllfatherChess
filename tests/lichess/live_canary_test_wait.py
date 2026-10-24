"""Synthetic PR-A container PID1: inert until test-only fixture appears."""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

def main():
    if os.getpid()!=1 or len(sys.argv)!=3 or sys.argv[1] not in ("gateway","bridge") or sys.argv[2] not in ("white","black"):
        raise SystemExit("synthetic test supervisor requires container PID1 and role/color")
    ready=Path("/run/operator/admission.json")
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        if ready.is_file() and Path("/run/operator/host-report.json").is_file():
            script=f"/work/tests/lichess/live_canary_fake_{sys.argv[1]}.py"
            os.execv("/opt/online3b/bin/python",
                     ["/opt/online3b/bin/python",script,"--color",sys.argv[2]])
        time.sleep(.25)
    raise SystemExit("test-only admission never materialized; no gateway or engine started")

if __name__=="__main__":
    main()
