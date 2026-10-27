"""S3-A: fail-closed ONE auxiliary slot and comparator-first reservation.

Only typed resource planning is implemented here: NO engine dispatch, quality
witness, resource qualification, or outward move authority. Unlike old J12
VERIFY receipts these records are separately discriminated S3 task semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Any

from controller.budget import (BudgetLedger, BudgetError, BudgetExceeded,
                               Reservation)
from .contracts import (TurnKey, SearchIdentity, StrengthContractError,
                        require, digest, SHA)

ACTIONS = ("NONE", "LC0_ONLY", "RECKLESS_ONLY")
PHASES = {
    "PROPOSE_LC0": ("lc0", "EXPLORE", "solver"),
    "PROPOSE_RECKLESS": ("reckless", "EXPLORE", "solver"),
    "COMPARE_PAIR": ("stockfish", "VERIFY", "verify"),
    "COMPARE_REFINE": ("stockfish", "STAGED_VERIFY", "verify"),
}


def _sha(value: object, label: str) -> None:
    require(type(value) is str and SHA.fullmatch(value) is not None,
            "S3 " + label + " must be full lowercase SHA256")


def _positive(value: object, label: str) -> float:
    require(type(value) in (int, float) and math.isfinite(value)
            and 0 < float(value) <= 1e9,
            "S3 " + label + " must be positive finite physical quantity")
    return float(value)


@dataclass(frozen=True)
class CostObservationV1:
    semantic_role: str
    source_commit: str
    profile_sha256: str
    host_domain_sha256: str
    evidence_sha256: str
    native_limit: int
    native_semantics: str
    reserved_cpu_ms: float
    reserved_gpu_ms: float
    wall_bound_ms: float
    memory_peak_bytes: int
    review_status: str

    def __post_init__(self) -> None:
        require(self.semantic_role in ("PROPOSE_LC0", "PROPOSE_RECKLESS",
                                        "COMPARE_PAIR"),
                "S3-A supports only one proposer + one first-round comparator")
        require(type(self.source_commit) is str and
                len(self.source_commit) == 40 and
                all(ch in "0123456789abcdef" for ch in self.source_commit),
                "S3 cost source commit invalid")
        for key in ("profile_sha256", "host_domain_sha256", "evidence_sha256"):
            _sha(getattr(self, key), key)
        family, _, _ = PHASES[self.semantic_role]
        require(self.native_semantics == family + ".uci_nodes" and
                type(self.native_limit) is int and 1 <= self.native_limit <= 2**48,
                "S3 cost native-limit semantics mismatch")
        _positive(self.reserved_cpu_ms, "reserved_cpu_ms")
        require(type(self.reserved_gpu_ms) in (int, float) and
                math.isfinite(self.reserved_gpu_ms) and
                0 <= self.reserved_gpu_ms <= 1e9,
                "S3 GPU reservation invalid")
        _positive(self.wall_bound_ms, "wall_bound_ms")
        require(type(self.memory_peak_bytes) is int and
                self.memory_peak_bytes > 0, "S3 helper memory needs measurement")
        require(self.review_status in ("SOURCE_TEST_FIXTURE", "INDEPENDENTLY_REVIEWED"),
                "S3 cost must be source-test or independently reviewed")

    @property
    def cost_digest(self) -> str:
        return digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "strength_cost_observation_v1",
                "semantic_role": self.semantic_role, "source_commit": self.source_commit,
                "profile_sha256": self.profile_sha256,
                "host_domain_sha256": self.host_domain_sha256,
                "evidence_sha256": self.evidence_sha256,
                "native_limit": self.native_limit,
                "native_semantics": self.native_semantics,
                "reserved_cpu_ms": self.reserved_cpu_ms,
                "reserved_gpu_ms": self.reserved_gpu_ms,
                "wall_bound_ms": self.wall_bound_ms,
                "memory_peak_bytes": self.memory_peak_bytes,
                "review_status": self.review_status,
                "authority": {"move": False, "dispatch": False,
                              "strength_qualified": False}}


@dataclass(frozen=True)
class StrengthTaskV1:
    anchor_search: SearchIdentity
    semantic_role: str
    root_epoch: int
    cost: CostObservationV1
    slot: int = 0

    def __post_init__(self) -> None:
        require(type(self.anchor_search) is SearchIdentity and
                type(self.cost) is CostObservationV1,
                "S3 strength task requires typed source/cost")
        require(type(self.root_epoch) is int and self.root_epoch>0,
                "S3 invalid incumbent epoch")
        require(self.semantic_role == self.cost.semantic_role and
                self.semantic_role in PHASES and self.slot == 0
                and type(self.slot) is int,
                "S3 only one leased slot exists")
        require(self.cost.source_commit == self.anchor_search.source_commit and
                self.cost.profile_sha256 == self.anchor_search.profile_sha256,
                "S3 task cost source/profile differs from incumbent")

    def as_dict(self) -> dict[str, Any]:
        family, phase, purpose = PHASES[self.semantic_role]
        return {"schema_version": 1, "kind": "strength_semantic_task_v1",
                "anchor_search": self.anchor_search.as_dict(),
                "turn_digest": self.anchor_search.turn.turn_digest,
                "root_epoch": self.root_epoch,
                "semantic_role": self.semantic_role, "native_family": family,
                "legacy_phase_mapping": phase,
                "ledger_purpose_mapping": purpose,
                "slot": self.slot, "cost": self.cost.as_dict(),
                "authority": {"work_dispatch": False, "outward_move": False,
                              "comparison_quality": False}}

    @property
    def task_digest(self) -> str:
        return digest(self.as_dict())


class StrengthAuxScheduler:
    """One-use, ledger-backed source-lab planner. Engine dispatch is not wired.

    compare is RESERVED first, then propose; reservations coexist, but
    start/finish of the single slot are strictly sequential. S3-B must verify
    and bind an authentic WorkGrant/cost catalog before enabling real work.
    """
    def __init__(self, *, anchor_search: SearchIdentity, ledger: BudgetLedger,
                 root_epoch: int, host_domain_sha256: str,
                 total_memory_bytes: int, anchor_memory_bytes: int,
                 stop_cleanup_wall_ms: float, allow_source_fixture: bool=False):
        require(type(anchor_search) is SearchIdentity and
                type(ledger) is BudgetLedger and
                type(root_epoch) is int and root_epoch > 0,
                "S3 scheduler requires anchor identity, physical ledger and epoch")
        _sha(host_domain_sha256, "host domain")
        require(type(total_memory_bytes) is int and total_memory_bytes>0 and
                type(anchor_memory_bytes) is int and
                0<=anchor_memory_bytes<=total_memory_bytes,
                "S3 invalid whole-participant memory envelope")
        self.anchor_search=anchor_search
        self.ledger=ledger
        self.root_epoch=root_epoch
        self.host_domain_sha256=host_domain_sha256
        self.total_memory_bytes=total_memory_bytes
        self.anchor_memory_bytes=anchor_memory_bytes
        self.stop_cleanup_wall_ms=_positive(stop_cleanup_wall_ms,"cleanup guard")
        self.allow_source_fixture=bool(allow_source_fixture)
        self._lock=threading.RLock()
        self.action="NONE"
        self.state="IDLE"
        self.reason="DEFAULT_NONE"
        self.proposer: StrengthTaskV1 | None=None
        self.comparator: StrengthTaskV1 | None=None
        self._prop_res: Reservation | None=None
        self._comp_res: Reservation | None=None
        self._prop_settled=False
        self._comp_settled=False
        self._failure=False
        self._active_role: str | None=None
        self._events: list[dict[str, Any]]=[]

    def _event(self, name: str, **fields: Any) -> None:
        # Deliberately no unsafe raw engine text in privileged planning records.
        self._events.append({"seq":len(self._events)+1,"event":name,**fields})

    def _deny(self, reason: str) -> str:
        self.reason=reason
        self._event("deny",reason=reason)
        return "NONE"

    def prepare(self, *, action: str, proposer_cost: CostObservationV1 | None,
                comparator_cost: CostObservationV1 | None,
                current_root_epoch: int, native_slack_wall_ms: float) -> str:
        with self._lock:
            require(self.state=="IDLE","S3 plan already used; never retry one turn")
            self.state="DENIED"  # no retry even after a denied/source-bad attempt
            require(type(action) is str and action in ACTIONS,"unknown S3 action")
            require(type(current_root_epoch) is int and
                    current_root_epoch == self.root_epoch, "S3 stale incumbent epoch")
            if action=="NONE":
                return self._deny("POLICY_NONE")
            if type(proposer_cost) is not CostObservationV1 or \
               type(comparator_cost) is not CostObservationV1:
                return self._deny("NO_MEASURED_COST_PROFILE")
            want="PROPOSE_LC0" if action=="LC0_ONLY" else "PROPOSE_RECKLESS"
            if proposer_cost.semantic_role!=want or comparator_cost.semantic_role!="COMPARE_PAIR":
                return self._deny("COST_TASK_FAMILY_MISMATCH")
            for cost in (proposer_cost, comparator_cost):
                if cost.source_commit!=self.anchor_search.source_commit or \
                   cost.profile_sha256!=self.anchor_search.profile_sha256 or \
                   cost.host_domain_sha256!=self.host_domain_sha256:
                    return self._deny("COST_OR_HOST_DOMAIN_MISMATCH")
                if cost.review_status!="INDEPENDENTLY_REVIEWED" and \
                   not (self.allow_source_fixture and cost.review_status=="SOURCE_TEST_FIXTURE"):
                    return self._deny("COST_NOT_INDEPENDENTLY_REVIEWED")
            if type(native_slack_wall_ms) not in (int,float) or \
               not math.isfinite(native_slack_wall_ms) or native_slack_wall_ms<=0:
                return self._deny("INVALID_NATIVE_SLACK")
            sum_wall=proposer_cost.wall_bound_ms+comparator_cost.wall_bound_ms+self.stop_cleanup_wall_ms
            if sum_wall>native_slack_wall_ms or sum_wall>self.ledger.wall_remaining_ms():
                return self._deny("COMPARISON_NO_LONGER_FEASIBLE")
            if self.anchor_memory_bytes+max(proposer_cost.memory_peak_bytes,
                                             comparator_cost.memory_peak_bytes)>self.total_memory_bytes:
                return self._deny("MEMORY_OUTSIDE_ENVELOPE")
            self.proposer=StrengthTaskV1(self.anchor_search,want,self.root_epoch,proposer_cost)
            self.comparator=StrengthTaskV1(self.anchor_search,"COMPARE_PAIR",
                                            self.root_epoch,comparator_cost)
            # Comparator money is committed before proposer dispatch. The
            # two ledger reservations have distinct purpose partitions.
            try:
                self._comp_res=self.ledger.reserve(
                    "strength-s3-compare",cpu_ms=comparator_cost.reserved_cpu_ms,
                    gpu_ms=comparator_cost.reserved_gpu_ms,purpose="verify")
                self._prop_res=self.ledger.reserve(
                    "strength-s3-propose",cpu_ms=proposer_cost.reserved_cpu_ms,
                    gpu_ms=proposer_cost.reserved_gpu_ms,purpose="solver")
            except (BudgetError,BudgetExceeded):
                if self._comp_res is not None:
                    self.ledger.release(self._comp_res)
                    self._comp_res=None
                self._prop_res=None
                self.proposer=None
                self.comparator=None
                return self._deny("LEDGER_ADMISSION_DENIED")
            self.state="RESERVED"
            self.action=action
            self.reason="RESOURCE_PREPARED_ONLY"
            self._event("prepared",action=action,
                        compare_id=self._comp_res.reservation_id,
                        propose_id=self._prop_res.reservation_id,
                        compare_task=self.comparator.task_digest,
                        propose_task=self.proposer.task_digest)
            return action

    def begin_proposer(self, *, turn: TurnKey, epoch: int) -> StrengthTaskV1:
        with self._lock:
            self._assert_current(turn,epoch)
            require(self.state=="RESERVED" and self._active_role is None,
                    "S3 proposer slot already occupied/closed")
            self.state="PROPOSER_ACTIVE"
            self._active_role="propose"
            self._event("proposer_started",task=self.proposer.task_digest)
            return self.proposer

    def _assert_current(self, turn: TurnKey, epoch: int) -> None:
        require(type(turn) is TurnKey and turn==self.anchor_search.turn
                and type(epoch) is int and epoch==self.root_epoch
                and not self._failure, "S3 stale generation/nonce or terminated task")

    def settle_proposer(self, *, turn: TurnKey, epoch: int,
                        actual_cpu_ms: float | None, actual_gpu_ms: float | None=0.,
                        succeeded: bool) -> None:
        with self._lock:
            self._assert_current(turn,epoch)
            require(self.state=="PROPOSER_ACTIVE" and self._active_role=="propose"
                    and self._prop_res is not None, "S3 proposer not active")
            self._settle(self._prop_res, actual_cpu_ms,actual_gpu_ms)
            self._prop_res=None
            self._prop_settled=True
            self._active_role=None
            cost=self.proposer.cost
            overspent=actual_cpu_ms is not None and actual_cpu_ms>cost.reserved_cpu_ms
            measured=actual_cpu_ms is not None and \
                (cost.reserved_gpu_ms==0 or actual_gpu_ms is not None)
            self._event("proposer_settled",succeeded=bool(succeeded),
                        measured=measured,overspent=overspent)
            if type(succeeded) is not bool or not succeeded or not measured or overspent:
                self.abort("PROPOSER_FAILED_OR_UNMEASURED")
                return
            self.state="PROPOSER_SETTLED"

    def begin_comparator(self, *, turn: TurnKey, epoch: int) -> StrengthTaskV1:
        with self._lock:
            self._assert_current(turn,epoch)
            require(self.state=="PROPOSER_SETTLED" and self._prop_settled
                    and self._active_role is None and self._comp_res is not None,
                    "S3 comparator cannot overlap proposer or unreserved work")
            self.state="COMPARATOR_ACTIVE"
            self._active_role="compare"
            self._event("comparator_started",task=self.comparator.task_digest)
            return self.comparator

    def settle_comparator(self, *, turn: TurnKey, epoch: int,
                          actual_cpu_ms: float | None, actual_gpu_ms: float | None=0.,
                          succeeded: bool) -> None:
        with self._lock:
            self._assert_current(turn,epoch)
            require(self.state=="COMPARATOR_ACTIVE" and self._active_role=="compare"
                    and self._comp_res is not None,"S3 comparator not active")
            self._settle(self._comp_res,actual_cpu_ms,actual_gpu_ms)
            self._comp_res=None
            self._active_role=None
            self._comp_settled=True
            cost=self.comparator.cost
            overspent=actual_cpu_ms is not None and actual_cpu_ms>cost.reserved_cpu_ms
            measured=actual_cpu_ms is not None and \
                (cost.reserved_gpu_ms==0 or actual_gpu_ms is not None)
            self._event("comparator_settled",succeeded=bool(succeeded),
                        measured=measured,overspent=overspent)
            if type(succeeded) is not bool or not succeeded or not measured or overspent:
                self.abort("COMPARATOR_FAILED_OR_UNMEASURED")
                return
            self.state="DONE"
            self.reason="SOURCE_ONLY_NO_COMPARISON_AUTHORITY"

    def _settle(self, reservation: Reservation, cpu:float|None,gpu:float|None) -> None:
        self.ledger.settle(reservation,actual_cpu_ms=cpu,actual_gpu_ms=gpu,
                           cpu_source="measured" if cpu is not None else "declared_fallback",
                           gpu_source="measured" if gpu is not None else "declared_fallback")

    def abort(self, reason: str) -> None:
        with self._lock:
            if self.state in ("ABORTED","DENIED","DONE"):
                return
            # An already-started task has UNKNOWN cost when no receipt exists.
            # Charge the declared amount conservatively; never release spent
            # work as though the search never happened.
            if self._prop_res is not None:
                if self._active_role=="propose":
                    self._settle(self._prop_res,None,None)
                else:
                    self.ledger.release(self._prop_res)
                self._prop_res=None
            if self._comp_res is not None:
                if self._active_role=="compare":
                    self._settle(self._comp_res,None,None)
                else:
                    self.ledger.release(self._comp_res)
                self._comp_res=None
            self.state="ABORTED"
            self._failure=True
            self._active_role=None
            self.reason=str(reason)[:100]
            self._event("abort",reason=self.reason)

    def incumbent_changed(self, *, turn: TurnKey,new_epoch:int) -> None:
        with self._lock:
            require(type(turn) is TurnKey and turn==self.anchor_search.turn,
                    "foreign turn cannot mutate strength reservation")
            require(type(new_epoch) is int and new_epoch>=self.root_epoch,
                    "incumbent epoch cannot regress")
            if new_epoch!=self.root_epoch:
                self.abort("INCUMBENT_EPOCH_STALE")
                self.root_epoch=new_epoch

    def finish(self) -> None:
        with self._lock:
            if self.state in ("RESERVED","PROPOSER_ACTIVE",
                              "PROPOSER_SETTLED","COMPARATOR_ACTIVE"):
                self.abort("ANCHOR_TERMINAL_OR_STOP")
            if self.state=="IDLE":
                self.state="DENIED"
                self.reason="NEVER_PLANNED"
            self._event("closed",state=self.state)

    def report(self) -> dict[str, Any]:
        with self._lock:
            result={"schema_version":1,"kind":"strength_s3a_scheduler_report_v1",
                    "action":self.action,"state":self.state,"reason":self.reason,
                    "anchor_search_digest":self.anchor_search.search_digest,
                    "turn_digest":self.anchor_search.turn.turn_digest,
                    "root_epoch":self.root_epoch,
                    "proposer_task_digest":None if self.proposer is None else self.proposer.task_digest,
                    "comparator_task_digest":None if self.comparator is None else self.comparator.task_digest,
                    "proposer_settled":self._prop_settled,
                    "comparator_settled":self._comp_settled,
                    "events":list(self._events),
                    "ledger_journal_digest":self.ledger.journal_digest(),
                    "authority":{"work_dispatch":False,"move":False,
                                 "comparison_quality":False,"strength":False,
                                 "equal_compute":False}}
            result["report_sha256"]=digest(result)
            return result
