"""Strictly offline, fixed-node E00 watchdog. Not an extension of S2 native clocks."""
from __future__ import annotations
from dataclasses import dataclass
import math
from common.search_request import PositionRequest, parse_go_request, SearchRequestError
from .contracts import StrengthContractError, require, digest, MOVE

POLICY = "strength_e00_fixed_nodes_watchdog_v1"

@dataclass(frozen=True)
class E00NodeSettings:
    policy: str = POLICY
    max_nodes: int = 200000
    wall_timeout_ms: int = 15000
    stop_grace_ms: int = 300
    output_margin_ms: int = 50
    prepare_budget_ms: int = 100
    max_events_per_turn: int = 4096

    def __post_init__(self) -> None:
        require(self.policy == POLICY, "E00 node policy mismatch")
        for key,low,high in (
            ("max_nodes",1,10000000),("wall_timeout_ms",1000,120000),
            ("stop_grace_ms",10,3000),("output_margin_ms",10,2000),
            ("prepare_budget_ms",1,1000),("max_events_per_turn",64,65536)):
            v=getattr(self,key)
            require(type(v) is int and low<=v<=high, "E00 invalid "+key)
        require(self.wall_timeout_ms >
                self.stop_grace_ms+self.output_margin_ms+self.prepare_budget_ms,
                "E00 reserves exceed watchdog window")

    @classmethod
    def from_config(cls,raw:object) -> "E00NodeSettings":
        require(type(raw) is dict and set(raw)==set(cls.__dataclass_fields__),
                "E00 timing has missing/unknown keys")
        return cls(**raw)

def _parse_nodes(command: str, settings: E00NodeSettings) -> tuple[int,tuple[str,...]]:
    require(type(command) is str and command.startswith("go ") and
            command==command.strip() and len(command)<=2048, "bad E00 go request")
    try:
        parsed=parse_go_request(command)
    except SearchRequestError as exc:
        raise StrengthContractError("invalid E00 UCI limits: "+str(exc)) from exc
    require(not parsed.get("unknown_tokens"),"E00 refuses unknown go tokens")
    limits=parsed.get("limits")
    require(type(limits) is list and len(limits)==1 and
            limits[0].get("name")=="nodes" and type(limits[0].get("value")) is int,
            "E00 requires exclusively go nodes N")
    nodes=limits[0]["value"]
    require(1<=nodes<=settings.max_nodes,"E00 node budget outside frozen bound")
    tokens=command.split()
    roots=tuple(parsed.get("root_moves") or ())
    require(tokens.count("nodes")==1 and tokens.count("searchmoves")<=1
            and ("searchmoves" not in tokens or roots), "duplicate limits/root clause")
    require(len(set(roots))==len(roots) and all(MOVE.fullmatch(x) and x in tokens for x in roots),
            "invalid E00 restricted roots")
    return nodes,roots

@dataclass(frozen=True)
class E00NodePlan:
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
    native_nodes: int
    settings: E00NodeSettings
    policy: str = POLICY

    @property
    def soft_deadline(self) -> float:
        return self.received_monotonic+self.soft_budget_ms/1000
    @property
    def hard_deadline(self) -> float:
        return self.received_monotonic+self.hard_budget_ms/1000
    def as_dict(self) -> dict:
        row={"schema_version":3,"kind":"strength_e00_node_plan_v1",
             "generation":self.generation,"position_id":self.position_id,
             "side_to_move":self.side_to_move,
             "external_go_command":self.external_go_command,
             "anchor_go_command":self.anchor_go_command,
             "received_monotonic":self.received_monotonic,
             "controller_cpu_started_ns":self.controller_cpu_started_ns,
             "hard_budget_ms":self.hard_budget_ms,"soft_budget_ms":self.soft_budget_ms,
             "prepare_budget_ms":self.prepare_budget_ms,
             "helper_advice_ms":self.helper_advice_ms,
             "restriction":list(self.restriction),"native_nodes":self.native_nodes,
             "settings":dict(vars(self.settings)),"policy":self.policy,
             "authority":{"timing":True,"outward_move":False},
             "experimental_only":True}
        row["plan_id"]="e00-"+digest(row)
        return row

def make_e00_plan(*,command:str,position:PositionRequest,generation:int,
                  settings:E00NodeSettings,received_monotonic:float,
                  controller_cpu_started_ns:int) -> E00NodePlan:
    require(position.variant=="standard", "E00 excludes Chess960")
    require(type(generation) is int and generation>0,"invalid E00 generation")
    require(type(received_monotonic) in (int,float) and math.isfinite(received_monotonic),
            "invalid E00 monotonic origin")
    require(type(controller_cpu_started_ns) is int and controller_cpu_started_ns>=0,
            "invalid E00 CPU origin")
    nodes,roots=_parse_nodes(command,settings)
    hard=settings.wall_timeout_ms
    soft=hard-settings.stop_grace_ms-settings.output_margin_ms
    return E00NodePlan(generation=generation,position_id=position.position_id,
                       side_to_move=position.side_to_move,
                       external_go_command=command,anchor_go_command=command,
                       received_monotonic=float(received_monotonic),
                       controller_cpu_started_ns=controller_cpu_started_ns,
                       hard_budget_ms=hard,soft_budget_ms=soft,
                       prepare_budget_ms=settings.prepare_budget_ms,
                       helper_advice_ms=0,restriction=roots,native_nodes=nodes,
                       settings=settings)

def verify_e00_plan(doc:dict,*,position:PositionRequest|None=None) -> list[str]:
    """Reconstruct the full plan instead of trusting producer verdict fields."""
    try:
        require(type(doc) is dict and doc.get("schema_version")==3 and
                doc.get("kind")=="strength_e00_node_plan_v1","not E00 node plan")
        require(set(doc)==set(E00NodePlan.__dataclass_fields__) |
                {"schema_version","kind","plan_id","authority","experimental_only"},
                "E00 plan unknown/missing fields")
        settings=E00NodeSettings.from_config(doc["settings"])
        require(doc["policy"]==POLICY and doc["experimental_only"] is True and
                doc["authority"]=={"timing":True,"outward_move":False},
                "E00 plan claims unauthorized authority")
        if position is None:
            from common.search_request import parse_position_command
            # This mode validates metadata only; full-history checking is done by flight.
            require(doc["side_to_move"] in ("w","b"),"missing E00 side")
            pos=type("_Position",(),{"position_id":doc["position_id"],
                                     "side_to_move":doc["side_to_move"],
                                     "variant":"standard"})()
        else:
            pos=position
        expected=make_e00_plan(command=doc["external_go_command"],position=pos,
                               generation=doc["generation"],settings=settings,
                               received_monotonic=doc["received_monotonic"],
                               controller_cpu_started_ns=doc["controller_cpu_started_ns"])
        require(doc==expected.as_dict(),"E00 plan/limits/watchdog/source mismatch")
        return []
    except (StrengthContractError,TypeError,KeyError,ValueError,AttributeError) as exc:
        return [str(exc)]
