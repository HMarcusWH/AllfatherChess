"""Bounded in-memory S2 flight log, flushed outside outward UCI publication."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from .contracts import canonical, digest, require, StrengthContractError


class FlightLog:
    def __init__(self, max_events: int=4096) -> None:
        require(type(max_events) is int and 64<=max_events<=65536,"invalid flight log bound")
        self.max_events=max_events
        self._lock=threading.Lock()
        self._entries: list[dict]=[]
        self._previous="0"*64
        self.overflow=False
        self.failure: str | None=None

    def append(self, event: str, data: dict) -> bool:
        """Never called with raw engine stderr or nonbounded stdout."""
        with self._lock:
            if len(self._entries)>=self.max_events:
                self.overflow=True
                return False
            try:
                record={"seq":len(self._entries)+1,"event":event,
                        "data":data,"previous_sha256":self._previous}
                digest_value=digest(record)
                record["event_sha256"]=digest_value
            except (StrengthContractError,ValueError) as exc:
                self.failure=str(exc)
                return False
            self._entries.append(record)
            self._previous=digest_value
            return True

    def snapshot(self)->dict:
        with self._lock:
            return {"schema_version":1,"kind":"strength_s2_flight_v1",
                    "diagnostic_only":True,"qualified":False,
                    "strength_qualified":False,"replacement_authorized":False,
                    "overflow":self.overflow,"failure":self.failure,
                    "events":[dict(x) for x in self._entries],
                    "end_sha256":self._previous}

    def write(self,path:Path) -> None:
        """Only outside move publication; atomic and fsync'd, no silent success."""
        path=Path(path)
        parent=path.parent
        parent.mkdir(parents=True,exist_ok=True)
        require(not path.is_symlink() and not parent.is_symlink(),
                "evidence output cannot be symlinked")
        content=json.dumps(self.snapshot(),sort_keys=True,indent=2,
                           ensure_ascii=True,allow_nan=False)+"\n"
        fd,tmp=tempfile.mkstemp(prefix="."+path.name+".",suffix=".tmp",dir=str(parent))
        try:
            with os.fdopen(fd,"w",encoding="ascii") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp,path)
            parent_fd=os.open(parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
            try:os.fsync(parent_fd)
            finally:os.close(parent_fd)
        finally:
            try:os.unlink(tmp)
            except FileNotFoundError:pass
