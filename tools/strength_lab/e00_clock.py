"""E00 native-clock diagnostics; timing is not equal-work equivalence."""
from __future__ import annotations
import re
from tools.strength_lab.e00_metrics import summary

_NUM=r"(?:^| ){} ([0-9]+)(?= |$)"
def last_engine_work(stdout:list[str]) -> dict:
    """Get last observed Stockfish native work, not a source of move authority."""
    if type(stdout) is not list:
        raise ValueError("invalid E00 engine stdout")
    observations=[]
    for line in stdout:
        if not isinstance(line,str) or not line.startswith("info "):
            continue
        parsed={}
        for key in ("nodes","time","depth"):
            hit=re.search(_NUM.format(key),line)
            if hit:
                parsed[key]=int(hit.group(1))
        if "nodes" in parsed:
            observations.append(parsed)
    return observations[-1] if observations else {}

def frontend_publication_delay_ms(flight:dict)->float:
    events=flight.get("events")
    if type(events) is not list:
        raise ValueError("invalid E00 flight events")
    ends=[e["data"]["checkpoint"]["observed_ns"] for e in events
          if e.get("event")=="terminal"]
    publications=[e["data"]["at_ns"] for e in events
                  if e.get("event")=="published"]
    if len(ends)!=1 or len(publications)!=1 or \
       type(ends[0]) is not int or type(publications[0]) is not int or publications[0]<ends[0]:
        raise ValueError("invalid E00 publication timestamp sequence")
    return (publications[0]-ends[0])/1e6

def summarize_clock(pub_ms:list[float],native_work:dict[str,list[dict]])->dict:
    return {"frontend_terminal_to_publication_ms":summary(pub_ms),
            "engine_work_per_arm":{
                arm:{"nodes":summary([float(row["nodes"]) for row in rows])
                     if rows and all("nodes" in row for row in rows) else None,
                     "engine_reported_time_ms":summary([float(row["time"]) for row in rows])
                     if rows and all("time" in row for row in rows) else None,
                     "depth":summary([float(row["depth"]) for row in rows])
                     if rows and all("depth" in row for row in rows) else None}
                for arm,rows in native_work.items()},
            "timed_search_equivalence_qualified":False,
            "clock_overhead_qualified":False,
            "strength_qualified":False}
