from __future__ import annotations
import os, time
from pathlib import Path
from typing import Any
from tests.harness.uci_session import UciSession
from .corpus import PositionCase
from .metrics import parse_search
from .report import sha256


def run_case(*, binary: Path, cwd: Path, family: str, case: PositionCase, options: dict[str, Any], nodes: int, deadline_ms: float, environment: dict[str,str] | None=None) -> dict[str,Any]:
    old={}
    environment=environment or {}
    for key,value in environment.items():
        old[key]=os.environ.get(key)
        os.environ[key]=value
    try:
        with UciSession(binary,cwd=cwd,timeout=max(10.0,deadline_ms/1000+5),start_new_session=True) as session:
            session.configure(options)
            session.new_game()
            session.set_position({"fen":case.fen,"moves":[]})
            started=time.monotonic()
            lines=session.search_nodes(nodes,timeout=max(5.0,deadline_ms/1000+2))
            wall=(time.monotonic()-started)*1000.0
            metric=parse_search(lines,family=family,wall_ms=wall,deadline_ms=deadline_ms)
            return {
                "case_id":case.case_id,
                "family":family,
                "binary_sha256":sha256(binary),
                "options":options,
                "nodes_requested":nodes,
                "metrics":metric.as_dict(),
                "transcript":list(session.transcript),
            }
    finally:
        for key,value in old.items():
            if value is None:
                os.environ.pop(key,None)
            else:
                os.environ[key]=value
