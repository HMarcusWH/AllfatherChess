"""Single-use E00 worker launcher: join private cgroup and pin before exec.

Replaces preexec_fn inside a Python process already running reader threads.
The launcher execs the selected engine/controller in place (no extra worker
remaining outside the resource scope).
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
from tools.strength_lab.participant_scope import ParticipantCgroup,ResourceScopeError

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope",type=Path,required=True)
    parser.add_argument("--inode",type=int,required=True)
    parser.add_argument("--cpu",type=int)
    parser.add_argument("command",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    argv=list(args.command)
    if argv and argv[0]=="--":
        argv.pop(0)
    if not argv or any(not x or "\x00" in x for x in argv):
        raise ValueError("missing E00 engine command")
    scope=args.scope
    parent=Path("/sys/fs/cgroup").resolve()
    if (not scope.is_dir() or scope.is_symlink() or
        not scope.resolve(strict=True).is_relative_to(parent) or
        scope.stat().st_ino!=args.inode):
        raise ResourceScopeError("E00 scope changed before worker launch")
    if args.cpu is not None:
        if args.cpu not in os.sched_getaffinity(0):
            raise ResourceScopeError("E00 search CPU not permitted")
        os.sched_setaffinity(0,{args.cpu})
        if os.sched_getaffinity(0)!={args.cpu}:
            raise ResourceScopeError("E00 search CPU affinity was not enforced")
    participant=ParticipantCgroup(scope)
    participant.join_in_child()
    os.execvpe(argv[0],argv,os.environ.copy())
    raise AssertionError("unreachable E00 child exec")

if __name__=="__main__":
    raise SystemExit(main())
