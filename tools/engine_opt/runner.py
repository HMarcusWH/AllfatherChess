from __future__ import annotations
import os,time
from pathlib import Path
from typing import Any
from tests.harness.uci_session import UciSession
from .corpus import PositionCase
from .metrics import parse_search
from .report import sha256

def _proc(pid: int) -> tuple[float,float] | None:
    try:
        text=(Path("/proc")/str(pid)/"stat").read_text()
        fields=text[text.rfind(")")+2:].split()
        ticks=os.sysconf("SC_CLK_TCK")
        cpu=(int(fields[11])+int(fields[12]))*1000.0/ticks
        rss=int(fields[21])*os.sysconf("SC_PAGE_SIZE")/1024.0
        return cpu,rss
    except (OSError,ValueError,IndexError):
        return None

def run_case(*, binary: Path, cwd: Path, family: str, case: PositionCase,
             options: dict[str, Any], nodes: int, deadline_ms: float,
             environment: dict[str,str] | None=None,
             args: list[str] | None=None,
             warmup_nodes: int | None=None) -> dict[str,Any]:
    old={}
    environment=environment or {}
    for key,value in environment.items():
        old[key]=os.environ.get(key)
        os.environ[key]=value
    try:
        with UciSession(
            binary,cwd=cwd,timeout=max(10.0,deadline_ms/1000+5),
            args=list(args or []),start_new_session=True
        ) as session:
            session.configure(options)
            session.new_game()
            warmup=None
            if warmup_nodes is not None:
                session.set_position({"startpos_moves":[]})
                started=time.monotonic()
                warm_lines=session.search_nodes(
                    warmup_nodes,timeout=max(5.0,deadline_ms/1000+2)
                )
                warm_ms=(time.monotonic()-started)*1000.0
                warm_metric=parse_search(
                    warm_lines,family=family,wall_ms=warm_ms,
                    deadline_ms=max(deadline_ms,warm_ms)
                )
                warmup={
                    "nodes":int(warmup_nodes),
                    "wall_ms":round(warm_ms,3),
                    "bestmove":warm_metric.bestmove,
                }
                # Reset every game/search state before the measured case.
                session.new_game()
            session.set_position({"fen":case.fen,"moves":[]})
            assert session.proc is not None
            before=_proc(session.proc.pid)
            started=time.monotonic()
            lines=session.search_nodes(nodes,timeout=max(5.0,deadline_ms/1000+2))
            wall=(time.monotonic()-started)*1000.0
            after=_proc(session.proc.pid)
            cpu=None if before is None or after is None else max(0.0,after[0]-before[0])
            rss=None if after is None else after[1]
            metric=parse_search(
                lines,family=family,wall_ms=wall,deadline_ms=deadline_ms,
                cpu_ms=cpu,rss_kib_end=rss
            )
            return {
                "case_id":case.case_id,
                "tags":list(case.tags),
                "family":family,
                "binary_sha256":sha256(binary),
                "args":list(args or []),
                "options":options,
                "environment":environment,
                "warmup":warmup,
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
