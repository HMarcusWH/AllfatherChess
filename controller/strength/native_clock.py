"""STRENGTH-S2 native UCI clocks; reuse the ONLINE single-writer watchdog.

The *original UCI go text* is sent directly to Stockfish. This is not
ONLINE-1's go movetime translation. No helper authority or compute grants.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from common.search_request import PositionRequest, parse_go_request, SearchRequestError
from .contracts import StrengthContractError, require, digest, MOVE

POLICY = "native_anchor_clock_watchdog_v1"


@dataclass(frozen=True)
class NativeClockSettings:
    policy: str = POLICY
    guard_ms: int = 750
    stop_grace_ms: int = 300
    output_margin_ms: int = 50
    prepare_budget_ms: int = 100
    max_events_per_turn: int = 4096

    def __post_init__(self):
        require(self.policy == POLICY, "unknown native clock/watchdog policy")
        for key,lo,hi in [
            ("guard_ms",100,10000), ("stop_grace_ms",10,3000),
            ("output_margin_ms",10,2000), ("prepare_budget_ms",1,1000),
            ("max_events_per_turn",64,65536),
        ]:
            value=getattr(self,key)
            require(type(value) is int and lo <= value <= hi, f"invalid {key}")

    @classmethod
    def from_config(cls, raw: object) -> "NativeClockSettings":
        require(isinstance(raw, dict), "strength.timing must be object")
        require(set(raw) == {"policy","guard_ms","stop_grace_ms","output_margin_ms","prepare_budget_ms","max_events_per_turn"},
                "unknown/missing native clock keys")
        return cls(**raw)


@dataclass(frozen=True)
class NativeClockPlan:
    generation: int
    position_id: str
    side_to_move: str
    external_go_command: str
    anchor_go_command: str
    received_monotonic: float
    controller_cpu_started_ns: int
    hard_budget_ms: int
    soft_budget_ms: int
    prepare_budget_ms: int
    helper_advice_ms: int
    restriction: tuple[str,...]
    settings: NativeClockSettings
    policy: str = POLICY

    @property
    def soft_deadline(self) -> float:
        return self.received_monotonic + self.soft_budget_ms / 1000

    @property
    def hard_deadline(self) -> float:
        return self.received_monotonic + self.hard_budget_ms / 1000

    def as_dict(self) -> dict:
        record={"schema_version":2,"kind":"strength_native_clock_plan_v1",
                "generation":self.generation,"position_id":self.position_id,
                "side_to_move":self.side_to_move,
                "external_go_command":self.external_go_command,
                "anchor_go_command":self.anchor_go_command,
                "received_monotonic":self.received_monotonic,
                "controller_cpu_started_ns":self.controller_cpu_started_ns,
                "hard_budget_ms":self.hard_budget_ms,
                "soft_budget_ms":self.soft_budget_ms,
                "prepare_budget_ms":self.prepare_budget_ms,
                "helper_advice_ms":self.helper_advice_ms,
                "restriction":list(self.restriction),"policy":self.policy,
                "settings":dict(vars(self.settings)),
                "authority":{"timing":True,"outward_move":False}}
        record["plan_id"]="native-"+digest(record)
        return record


def _limits(command: str) -> tuple[dict[str,int],tuple[str,...]]:
    require(isinstance(command,str) and command.startswith("go ")
            and command == command.strip() and len(command)<=2048, "malformed native UCI go")
    try:
        data=parse_go_request(command)
    except SearchRequestError as exc:
        raise StrengthContractError(str(exc)) from exc
    require(not data.get("unknown_tokens"), "native clock refuses unknown/invalid go options")
    tokens=command.split()
    roots=tuple(data.get("root_moves") or ())
    require(tokens.count("searchmoves") <= 1 and ("searchmoves" not in tokens or roots),
            "searchmoves requires explicit permitted roots")
    require(len(set(roots))==len(roots), "duplicate native searchmoves")
    require(all(MOVE.fullmatch(x) and x in tokens for x in roots),
            "noncanonical searchmoves")
    result={}
    for item in data["limits"]:
        name=item["name"]
        require(name not in result and type(item["value"]) is int,
                "duplicate/noninteger go limit")
        result[name]=item["value"]
    if "movetime" in result:
        require(set(result)=={"movetime"} and result["movetime"]>0,
                "movetime cannot mix with game clocks or other limits")
    else:
        require({"wtime","btime"}<=set(result) and
                set(result)<={"wtime","btime","winc","binc","movestogo"},
                "native S2 requires wtime+btime, increments, optional movestogo")
        require(result["wtime"]>0 and result["btime"]>0, "nonpositive native clock")
        if "movestogo" in result:
            require(result["movestogo"]>0,"nonpositive moves-to-go")
    return result, roots


def make_native_plan(*, command:str, position:PositionRequest, generation:int,
                     settings:NativeClockSettings, received_monotonic:float,
                     controller_cpu_started_ns:int) -> NativeClockPlan:
    require(position.variant=="standard", "S2 excludes Chess960")
    require(type(generation) is int and generation>0, "invalid generation")
    require(type(received_monotonic) in (int,float) and math.isfinite(received_monotonic),
            "invalid monotonic origin")
    require(type(controller_cpu_started_ns) is int and controller_cpu_started_ns>=0,
            "invalid CPU receipt")
    limits,roots=_limits(command)
    if "movetime" in limits:
        remaining=limits["movetime"]
        inc=0
        hard=remaining-settings.output_margin_ms
    else:
        side=position.side_to_move
        remaining=limits[f"{side}time"]
        inc=limits.get(f"{side}inc",0)
        hard=remaining-settings.guard_ms
    require(hard>settings.stop_grace_ms+settings.output_margin_ms+settings.prepare_budget_ms,
            "clock too small for native hard stop/output/preparation guard")
    soft=hard-settings.stop_grace_ms-settings.output_margin_ms
    advice=min(500,int(.25*(remaining/30+.75*inc)),int(.1*remaining))
    return NativeClockPlan(
        generation=generation,position_id=position.position_id,
        side_to_move=position.side_to_move,
        external_go_command=command,anchor_go_command=command,
        received_monotonic=float(received_monotonic),
        controller_cpu_started_ns=controller_cpu_started_ns,
        hard_budget_ms=int(hard),soft_budget_ms=int(soft),
        prepare_budget_ms=settings.prepare_budget_ms,
        helper_advice_ms=max(0,advice),restriction=roots,settings=settings)


def verify_native_plan(doc: dict) -> list[str]:
    """Independent schema-v2 native timing validation; never reinterpret v1."""
    try:
        require(type(doc) is dict and doc.get("schema_version")==2
                and doc.get("kind")=="strength_native_clock_plan_v1",
                "not native timing schema")
        expected_keys=set(NativeClockPlan.__dataclass_fields__)-{"settings"} | {"schema_version","kind","plan_id","authority","settings"}
        # Properties are reconstructed; exclude Python-only fields not serialized.
        assert "policy" in expected_keys
        require(set(doc)==expected_keys, "timing record unknown/missing keys")
        settings=NativeClockSettings.from_config(doc["settings"])
        require(doc["policy"]==POLICY and doc["anchor_go_command"]==doc["external_go_command"],
                "native timing rewrote original UCI request")
        require(doc["authority"]=={"timing":True,"outward_move":False},
                "timing tries to grant move authority")
        limits,roots=_limits(doc["external_go_command"])
        require(list(roots)==doc["restriction"], "external root restriction drift")
        d=dict(doc)
        declared=d.pop("plan_id")
        require(declared=="native-"+digest(d), "native plan digest mismatch")
        require(type(doc["hard_budget_ms"]) is int and doc["hard_budget_ms"]>0,
                "invalid hard guard")
        require(type(doc["soft_budget_ms"]) is int and 0<doc["soft_budget_ms"]<doc["hard_budget_ms"],
                "invalid soft cutoff")
        if "movetime" in limits:
            require(doc["hard_budget_ms"]==limits["movetime"]-settings.output_margin_ms,
                    "analysis budget mutated")
        return []
    except (StrengthContractError,KeyError,TypeError,AssertionError,ValueError) as exc:
        return [str(exc)]
