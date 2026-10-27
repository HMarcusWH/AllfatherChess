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
from tools.strength_lab.e00_resources import cpulist

def enforce_search_cpu(cpu:int,scope:Path)->None:
    """A child inherits its harness parent's *narrow* affinity.

    Validate against the effective delegated cpuset, not current task affinity,
    which intentionally does not include the reserved search core.
    """
    if type(cpu) is not int or cpu<0:
        raise ResourceScopeError("invalid E00 search CPU")
    path=scope/"cpuset.cpus.effective"
    if path.is_symlink() or not path.is_file():
        raise ResourceScopeError("E00 delegated cpuset source unavailable")
    with path.open("rb") as stream:
        data=stream.read(1025)
    if len(data)>1024:
        raise ResourceScopeError("E00 delegated cpuset bound exceeded")
    try:
        allowed=cpulist(data.decode("ascii"))
    except (ValueError,UnicodeError) as exc:
        raise ResourceScopeError("E00 malformed delegated cpuset") from exc
    if cpu not in allowed:
        raise ResourceScopeError("E00 search CPU excluded from delegated cpuset")
    try:
        os.sched_setaffinity(0,{cpu})
    except OSError as exc:
        raise ResourceScopeError("E00 search CPU placement denied") from exc
    if os.sched_getaffinity(0)!={cpu}:
        raise ResourceScopeError("E00 search CPU affinity was not enforced")

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
        enforce_search_cpu(args.cpu,scope)
    participant=ParticipantCgroup(scope)
    participant.join_in_child()
    os.execvpe(argv[0],argv,os.environ.copy())
    raise AssertionError("unreachable E00 child exec")

if __name__=="__main__":
    raise SystemExit(main())
