from __future__ import annotations
import hashlib, json, os, platform, subprocess
from pathlib import Path
from typing import Any


def sha256(path: Path | str) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1<<20), b""):
            h.update(block)
    return h.hexdigest()


def source_identity(root: Path) -> dict[str,str]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git","-C",str(root),*args],text=True).strip()
    return {"commit":git("rev-parse","HEAD"),"tree":git("rev-parse","HEAD^{tree}")}


def host_identity() -> dict[str, Any]:
    return {
        "platform":platform.platform(),
        "machine":platform.machine(),
        "processor":platform.processor(),
        "logical_cpus":os.cpu_count(),
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    core=dict(payload)
    core.pop("content_sha256",None)
    encoded=json.dumps(core,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    payload={**core,"content_sha256":hashlib.sha256(encoded).hexdigest()}
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
