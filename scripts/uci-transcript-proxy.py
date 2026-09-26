#!/usr/bin/env python3
"""Transparent UCI subprocess proxy used only by LOCAL-1 qualification.

The proxy never interprets chess. It records exact runner<->engine line traffic,
assigns game/request ordinals, and forwards bytes line-for-line. The child keeps
a real pipe on stdout, preserving the ONLINE deadline-safe PIPE_BUF contract.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--log",type=Path,required=True)
    parser.add_argument("--session-id",required=True)
    parser.add_argument("child",nargs=argparse.REMAINDER)
    args=parser.parse_args()
    child=list(args.child)
    if child and child[0]=="--":
        child=child[1:]
    if not child:
        parser.error("child command is required after --")

    args.log.parent.mkdir(parents=True,exist_ok=True)
    proxy_instance=uuid.uuid4().hex
    lock=threading.Lock()
    seq=0
    game_index=-1
    request_index=0
    active_request=None
    current_position=None
    bestmove_counts:dict[int,int]={}
    handle=args.log.open("a",encoding="utf-8",buffering=1)

    proc=subprocess.Popen(
        child,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
        cwd=os.getcwd(),
    )
    assert proc.stdin is not None and proc.stdout is not None

    def record(event:str, *, direction:str|None=None, line:str|None=None, extra=None) -> None:
        nonlocal seq
        with lock:
            seq+=1
            row={
                "schema_version":1,
                "session_id":args.session_id,
                "proxy_instance":proxy_instance,
                "proxy_pid":os.getpid(),
                "child_pid":proc.pid,
                "seq":seq,
                "monotonic_ns":time.monotonic_ns(),
                "utc":_dt.datetime.now(_dt.timezone.utc).isoformat(),
                "event":event,
                "direction":direction,
                "line":line,
                "game_index":game_index,
                "request_index":active_request,
                "position":current_position,
            }
            if extra:
                row.update(extra)
            handle.write(json.dumps(row,sort_keys=True,allow_nan=False)+"\n")

    record("proxy_start",extra={"child":child})

    def reader() -> None:
        nonlocal active_request
        try:
            for raw in proc.stdout:
                line=raw.rstrip("\r\n")
                if line.startswith("bestmove "):
                    if active_request is None:
                        record("protocol_error",direction="out",line=line,
                               extra={"reason":"bestmove without active go"})
                    else:
                        bestmove_counts[active_request]=bestmove_counts.get(active_request,0)+1
                        if bestmove_counts[active_request]>1:
                            record("protocol_error",direction="out",line=line,
                                   extra={"reason":"duplicate bestmove for request"})
                record("line",direction="out",line=line)
                sys.stdout.write(line+"\n")
                sys.stdout.flush()
        finally:
            record("stdout_eof")

    thread=threading.Thread(target=reader,name="local1-uci-proxy-reader",daemon=True)
    thread.start()

    try:
        for raw in sys.stdin:
            line=raw.rstrip("\r\n")
            if line=="ucinewgame":
                game_index+=1
            if line.startswith("position "):
                current_position=line
            if line=="go" or line.startswith("go "):
                request_index+=1
                active_request=request_index
                bestmove_counts[request_index]=0
            record("line",direction="in",line=line)
            try:
                proc.stdin.write(line+"\n")
                proc.stdin.flush()
            except BrokenPipeError:
                record("protocol_error",direction="in",line=line,
                       extra={"reason":"child stdin closed"})
                break
            if line=="quit":
                break
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            rc=proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc=proc.wait(timeout=5)
            record("protocol_error",extra={"reason":"child required kill on proxy shutdown"})
        thread.join(timeout=5)
        missing=sorted(index for index,count in bestmove_counts.items() if count!=1)
        record("proxy_exit",extra={"returncode":rc,"non_single_bestmove_requests":missing})
        handle.close()
    return int(rc)


if __name__=="__main__":
    raise SystemExit(main())
