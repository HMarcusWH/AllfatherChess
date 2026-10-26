"""Closed, opt-in schema-v3 configuration for observe-only native Stockfish.

Never auto-promote an old schema-v2 config. No helper process starts in S2.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from .contracts import StrengthContractError, require
from .coordinator import S2Settings
from .native_clock import NativeClockSettings

TOP_KEYS = {"schema_version","root","mode","anchor","instances","strength"}
STRENGTH_KEYS = {"policy","mode","timing","evidence_dir"}
 
def from_v3(*,raw:dict,path:Path,root:Path,
            anchor:str,specs:dict) -> tuple[S2Settings,NativeClockSettings]:
    require(set(raw)==TOP_KEYS, "S2 v3 unknown/missing top-level keys")
    require(raw["schema_version"]==3 and type(raw["schema_version"]) is int,
            "wrong S2 runtime version")
    require(raw["mode"]=="anchor" and isinstance(anchor,str)
            and len(specs)==1 and anchor in specs, "S2 requires one native anchor only")
    spec=specs[anchor]
    require(spec.family=="stockfish" and spec.role=="anchor",
            "S2 must have a single Stockfish incumbent")
    require(not spec.phase_options and spec.warmup is None,
            "S2 forbids phase rewrites or unmeasured warmups")
    opts=spec.options
    require(type(opts.get("MultiPV")) is int and opts["MultiPV"]==1,
            "S2 incumbent must search unrestricted MultiPV1")
    require(type(opts.get("Threads")) is int and opts["Threads"]>=1,
            "S2 requires explicit positive Stockfish Threads")
    require(type(opts.get("Hash")) is int and opts["Hash"]>=16,
            "S2 requires explicit Stockfish Hash >=16")
    require(type(opts.get("Move Overhead")) is int
            and 0<=opts["Move Overhead"]<=1000,
            "S2 requires explicit bounded Stockfish Move Overhead")
    require(opts.get("Ponder") in (None,False)
            and opts.get("UCI_Chess960") in (None,False),
            "S2 forbids ponder/Chess960")
    require(not spec.environment,"S2 does not accept unaudited engine environment")
    conf=raw["strength"]
    require(type(conf) is dict and set(conf)==STRENGTH_KEYS,
            "S2 unknown/missing strength profile keys")
    require(conf["policy"]=="strength_s2_incumbent_observe_v1"
            and conf["mode"]=="observe_only","S2 mode/policy is observe-only only")
    timing=NativeClockSettings.from_config(conf["timing"])
    target=conf["evidence_dir"]
    require(type(target) is str and target.startswith("build/") and
            len(target)<=192, "evidence root must be relative under build/")
    path_parts=Path(target).parts
    require(all(x not in (".","..") for x in path_parts),
            "evidence directory escapes build")
    evidence=root/target
    require(evidence.resolve().is_relative_to((root/"build").resolve()),
            "S2 evidence must remain inside repository build")
    # Existing parent symlinks could redirect audit writes away from build.
    for parent in (evidence,*evidence.parents):
        if parent==root:
            break
        require(not parent.is_symlink(),"evidence path contains symlink")
    try:
        result=subprocess.run(["git","-C",str(root),"rev-parse","HEAD"],
                              capture_output=True,text=True,check=True,timeout=5)
        source_commit=result.stdout.strip()
        dirty=subprocess.run(["git","-C",str(root),"status","--porcelain",
                               "--untracked-files=no"],
                              capture_output=True,text=True,check=True,timeout=5)
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        raise StrengthContractError("S2 requires an exact source Git checkout") from exc
    require(re.fullmatch(r"[0-9a-f]{40}",source_commit) is not None,
            "Git source commit incomplete")
    require(not dirty.stdout.strip(), "S2 source checkout has modified tracked files")
    profile_digest=hashlib.sha256(path.read_bytes()).hexdigest()
    return S2Settings(source_commit=source_commit,profile_sha256=profile_digest,
                      evidence_dir=evidence,max_events_per_turn=timing.max_events_per_turn),timing
