"""Paired E00 latency and CPU analysis from independently validated UCI streams."""
from __future__ import annotations
import math

def p95(values:list[float])->float:
    if not values or any(type(x) not in (int,float) or not math.isfinite(x) for x in values):
        raise ValueError("missing/nonfinite E00 metric")
    return float(sorted(values)[math.ceil(.95*len(values))-1])

def summary(values:list[float])->dict:
    if not values or any(type(x) not in (int,float) or not math.isfinite(x) for x in values):
        raise ValueError("missing/nonfinite E00 metric")
    ordered=sorted(values)
    n=len(ordered)
    median=(ordered[(n-1)//2]+ordered[n//2])/2
    return {"count":n,"median":float(median),"p95":p95(values),
            "min":float(ordered[0]),"max":float(ordered[-1])}

def paired_metrics(wrapped_wall:list[float],direct_wall:list[float],
                   wrapped_cpu:list[float]|None=None,
                   direct_cpu:list[float]|None=None)->dict:
    if len(wrapped_wall)!=len(direct_wall) or not wrapped_wall:
        raise ValueError("unmatched E00 paired wall observations")
    wall=[a-b for a,b in zip(wrapped_wall,direct_wall,strict=True)]
    cpu=None
    if wrapped_cpu is not None or direct_cpu is not None:
        if wrapped_cpu is None or direct_cpu is None or len(wrapped_cpu)!=len(direct_cpu) or len(wrapped_cpu)!=len(wall):
            raise ValueError("incomplete E00 per-search CPU observations")
        cpu=summary([a-b for a,b in zip(wrapped_cpu,direct_cpu,strict=True)])
    return {"wrapper_minus_direct_wall_ms":summary(wall),
            "wrapper_minus_direct_cpu_ms":cpu,
            "same_actual_compute_claim":False}
