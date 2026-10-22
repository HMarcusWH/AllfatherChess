"""Local file-based health signals: never bind a public diagnostic port."""
from __future__ import annotations
import json
from pathlib import Path

def health(root:Path)->dict:
    root=Path(root)
    if root.is_symlink() or not root.is_dir():
        return {"ready":False,"state":"UNAVAILABLE"}
    status=root/"status.json"
    if not status.is_file() or status.is_symlink() or status.stat().st_size>8192:
        return {"ready":False,"state":"NOT_ADMITTED"}
    data=json.loads(status.read_text(encoding="utf-8"))
    ready=(data.get("state") in ("ARMED","PLAYING")
           and data.get("gateway_authenticated") is True
           and data.get("host_source_verified") is True
           and data.get("stop_requested") is False)
    return {"ready":bool(ready),"state":str(data.get("state","INVALID"))[:32]}
