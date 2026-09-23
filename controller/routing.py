"""Active budget routing: observe, propose, authorize, dispatch.

Separation of powers
--------------------
```text
cheap observation  ->  route proposal  ->  admissibility gate  ->  action
   (Intuition)          (nomination)          (Wisdom)
```
An instability signal may **nominate** more computation. It can never by itself
**authorize** stopping or suppression. Every action passes a conjunction of
gates: calibration present, calibration in domain, sufficient support, risk
below a declared threshold, minimum observation spent, and budget available.

Scope of authority
------------------
This router allocates *shadow observation compute* inside a declared envelope.
It never grants move authority. In ordinary active mode the unrestricted anchor
remains the outward authority; in the explicit M14-C hybrid profile, a separate
DecisionAuthorization gate may consume these resource facts before selecting a
HYBRID move or deterministic anchor fallback. Stopping a shadow worker returns
budget; it never elects a different bestmove.

Fail-closed
-----------
Missing calibration, out-of-domain calibration, missing evidence, or a failed
budget reservation all resolve to the conservative branch: keep observing, buy
more compute, or fall back to anchor-only. The router never improvises.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from controller.budget import (
    BudgetExceeded,
    BudgetLedger,
    Reservation,
    ResourceEnvelope,
    REFINE_LANE,
    VERIFY_LANE,
)
from controller.calibration import (
    CalibrationError,
    CalibrationEvaluation,
    ReversalRiskModel,
    bucket_key,
    load_calibration,
)
from common.residuals import past_only_features
from common.search_request import SearchRequestError, parse_go_request
from controller.replay import atomic_write_text
from controller.replay_analysis import SearchTrajectory, reconstruct_stream
from controller.shadow import RouterCommand


ROUTE_SCHEMA_VERSION = 2
#: Engine-native counters that really are alpha-beta node counts, and so share
#: the `min_observation_nodes` floor.
_ALPHA_BETA_NODE_SEMANTICS = ("stockfish.uci_nodes", "reckless.uci_nodes")

POLICY_NAME = "conservative_v1"


class RoutingError(RuntimeError):
    """Raised when a routing policy cannot be constructed honestly."""


class RouteAction(str, Enum):
    """The admissible computational moves for this milestone."""

    CONTINUE = "continue"
    HOLD = "hold"
    EXTEND = "extend"
    STOP_WORKER = "stop_worker"
    FALLBACK_ANCHOR = "fallback_anchor"
    ABSTAIN_BUY_COMPUTE = "abstain_buy_compute"


@dataclass(frozen=True)
class OwnerObservation:
    """Cheap, past-only observation of one shadow worker."""

    owner: str
    instance: str
    active: bool
    stages_dispatched: int
    leader: str | None
    leader_flips: int
    observation_count: int
    elapsed_fraction: float
    stable_run_fraction: float
    work_value: float | None
    work_semantics: str | None
    #: True when the live event view stopped growing, so the observation is a
    #: stale prefix rather than the current state of the search.
    observation_truncated: bool = False
    #: Lines the engine has already reported that had not been translated to
    #: disk when this observation was taken. Non-zero means the events this
    #: decision saw are behind what the engine had said, and the offline audit
    #: will show the difference.
    observation_backlog: int = 0
    #: True when this worker's stream has permanently lost evidence (a dropped
    #: queue entry or a failed adapter translation). Unlike a backlog this never
    #: clears, and a lost leader flip reads as stability.
    observation_lossy: bool = False

    def calibration_features(self) -> dict[str, float]:
        """Exactly the shared past-only feature set the model was fitted on.

        `elapsed_fraction` is recorded on the observation for the audit trail
        but is deliberately **not** a calibration feature: offline its
        denominator is the observed replay span and online it is the declared
        wall envelope, so it cannot be computed identically in both paths.
        """
        return {
            "observation_count": float(self.observation_count),
            "leader_flips": float(self.leader_flips),
            "stable_run_fraction": self.stable_run_fraction,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "instance": self.instance,
            "active": self.active,
            "stages_dispatched": self.stages_dispatched,
            "leader": self.leader,
            "leader_flips": self.leader_flips,
            "observation_count": self.observation_count,
            "elapsed_fraction": round(self.elapsed_fraction, 6),
            "stable_run_fraction": round(self.stable_run_fraction, 6),
            "work_value": self.work_value,
            "work_semantics": self.work_semantics,
            "observation_truncated": self.observation_truncated,
            "observation_backlog": self.observation_backlog,
            "observation_lossy": self.observation_lossy,
        }


@dataclass(frozen=True)
class RouteProposal:
    """A nomination. Carries no authority of its own."""

    action: RouteAction
    owner: str
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action.value, "owner": self.owner, "rationale": self.rationale}


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class RouteDecision:
    """The audit certificate `H` for one routing decision.

    `(route, cost, residuals, certificates, thresholds, provenance, disposition)`
    """

    checkpoint_ms: float
    observation: OwnerObservation
    proposal: RouteProposal
    gates: tuple[Gate, ...]
    granted: bool
    action: RouteAction
    reason: str
    calibration: dict[str, Any] | None
    thresholds: dict[str, Any]
    budget: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_ms": round(self.checkpoint_ms, 3),
            "observation": self.observation.as_dict(),
            "proposal": self.proposal.as_dict(),
            "gates": [gate.as_dict() for gate in self.gates],
            "granted": self.granted,
            "action": self.action.value,
            "reason": self.reason,
            "calibration": self.calibration,
            "thresholds": self.thresholds,
            "budget": self.budget,
        }


@dataclass
class RouteAudit:
    """Unresolved-stress memory for one run, written beside the raw bundle."""

    run_id: str
    policy: str
    decisions: list[dict[str, Any]] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)
    specialist_actions: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def record(self, decision: RouteDecision) -> None:
        payload = decision.as_dict()
        self.decisions.append(payload)
        if not decision.granted:
            self.denials.append(
                {
                    "checkpoint_ms": payload["checkpoint_ms"],
                    "owner": decision.observation.owner,
                    "proposed": decision.proposal.action.value,
                    "reason": decision.reason,
                }
            )

    def record_specialist(self, payload: dict[str, Any]) -> None:
        self.specialist_actions.append(dict(payload))
        if not payload.get("granted", False):
            self.denials.append(
                {
                    "checkpoint_ms": payload.get("checkpoint_ms"),
                    "owner": payload.get("owner"),
                    "proposed": payload.get("phase"),
                    "reason": payload.get("reason"),
                }
            )

    def note(self, message: str) -> None:
        if len(self.notes) < 128:
            self.notes.append(message)


@dataclass(frozen=True)
class RoutingPolicy:
    """Declared thresholds. None of these is inherited from another domain."""

    min_observation_nodes: int
    checkpoint_interval_ms: float
    max_stages_per_owner: int
    extend_nodes: int
    stop_max_reversal_risk: float
    stop_min_support: int
    stop_min_stability_fraction: float
    stage_cpu_ms_estimate: float
    anchor_cpu_ms_estimate: float
    stage_gpu_ms_estimate: float
    #: Per-semantics observation floors. `min_observation_nodes` remains the
    #: alpha-beta default; a family whose counter means something else needs its
    #: own declared floor rather than borrowing that number.
    observation_floors: dict[str, float] = field(default_factory=dict)
    verify_stage_cpu_ms_estimate: float = 200.0
    verify_stage_gpu_ms_estimate: float = 0.0
    refine_stage_cpu_ms_estimate: float = 200.0
    refine_stage_gpu_ms_estimate: float = 0.0
    refine_oracle_cpu_ms_estimate: float = 50.0
    refine_oracle_gpu_ms_estimate: float = 0.0

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "RoutingPolicy":
        if not config:
            raise RoutingError("active mode requires an explicit routing configuration")
        policy = config.get("policy", POLICY_NAME)
        if policy != POLICY_NAME:
            raise RoutingError(f"unsupported routing policy: {policy!r}")
        def number(key: str, default: float) -> float:
            """Read one threshold, refusing anything that is not a number.

            `int()` and `float()` accept `bool`, so `min_observation_nodes:
            false` became 0 and passed every range check -- the alpha-beta
            minimum-work gate then succeeded on any reported counter -- while
            `stop_max_reversal_risk: true` became 1.0 and admitted every
            bucket. A gate deleted by a JSON boolean is not a misconfiguration
            the range checks can catch, because the coerced value is in range.
            """
            raw = config.get(key, default)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise RoutingError(
                    f"routing.{key} must be a number, got {type(raw).__name__}: {raw!r}"
                )
            return raw

        try:
            return cls(
                min_observation_nodes=int(number("min_observation_nodes", 4000)),
                checkpoint_interval_ms=float(number("checkpoint_interval_ms", 120)),
                max_stages_per_owner=int(number("max_stages_per_owner", 3)),
                extend_nodes=int(number("extend_nodes", 8000)),
                stop_max_reversal_risk=float(number("stop_max_reversal_risk", 0.05)),
                stop_min_support=int(number("stop_min_support", 25)),
                stop_min_stability_fraction=float(number("stop_min_stability_fraction", 0.6)),
                stage_cpu_ms_estimate=float(number("stage_cpu_ms_estimate", 400.0)),
                anchor_cpu_ms_estimate=float(number("anchor_cpu_ms_estimate", 0.0)),
                stage_gpu_ms_estimate=float(number("stage_gpu_ms_estimate", 0.0)),
                observation_floors=dict(config.get("observation_floors") or {}),
                verify_stage_cpu_ms_estimate=float(number("verify_stage_cpu_ms_estimate", 200.0)),
                verify_stage_gpu_ms_estimate=float(number("verify_stage_gpu_ms_estimate", 0.0)),
                refine_stage_cpu_ms_estimate=float(number("refine_stage_cpu_ms_estimate", 200.0)),
                refine_stage_gpu_ms_estimate=float(number("refine_stage_gpu_ms_estimate", 0.0)),
                refine_oracle_cpu_ms_estimate=float(number("refine_oracle_cpu_ms_estimate", 50.0)),
                refine_oracle_gpu_ms_estimate=float(number("refine_oracle_gpu_ms_estimate", 0.0)),
            )
        except (TypeError, ValueError) as exc:
            raise RoutingError(f"invalid routing configuration: {exc}") from exc

    def __post_init__(self) -> None:
        """Refuse a configuration that makes the conservative gates vacuous.

        Every suppression gate is a comparison against one of these numbers, so
        an out-of-range value does not merely misconfigure the policy -- it
        deletes the gate. `stop_max_reversal_risk: 2` passes any risk,
        `stop_min_support: -1` passes any support, and
        `stop_min_stability_fraction: -1` passes any stability. A policy that
        calls itself conservative has to be unable to say that.
        """
        for name in ("stop_max_reversal_risk", "stop_min_stability_fraction"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise RoutingError(f"{name} must be a probability in [0, 1], got {value!r}")
        if self.stop_min_support < 1:
            raise RoutingError(
                f"stop_min_support must be at least 1, got {self.stop_min_support!r}: "
                "a floor below one authorizes suppression from no evidence"
            )
        if self.max_stages_per_owner < 1:
            raise RoutingError("max_stages_per_owner must be at least 1")
        if self.extend_nodes < 1:
            raise RoutingError("extend_nodes must be a positive node count")
        if self.min_observation_nodes < 0:
            raise RoutingError("min_observation_nodes must be non-negative")
        interval = float(self.checkpoint_interval_ms)
        if not math.isfinite(interval) or interval <= 0.0:
            raise RoutingError("checkpoint_interval_ms must be a positive, finite duration")
        for name in (
            "stage_cpu_ms_estimate",
            "anchor_cpu_ms_estimate",
            "stage_gpu_ms_estimate",
            "verify_stage_cpu_ms_estimate",
            "verify_stage_gpu_ms_estimate",
            "refine_stage_cpu_ms_estimate",
            "refine_stage_gpu_ms_estimate",
            "refine_oracle_cpu_ms_estimate",
            "refine_oracle_gpu_ms_estimate",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise RoutingError(f"{name} must be a non-negative, finite duration")
        # Round three range-checked every threshold precisely because an
        # out-of-range value deletes a gate rather than misconfiguring it. This
        # mapping was added in round six and skipped that rule: a floor of -1
        # makes `minimum_observation` pass for any tagged observation.
        if not isinstance(self.observation_floors, dict):
            raise RoutingError("observation_floors must be a mapping of semantics to floors")
        for semantics, floor in self.observation_floors.items():
            if not isinstance(semantics, str) or not semantics:
                raise RoutingError("observation_floors keys must be non-empty semantics tags")
            if isinstance(floor, bool) or not isinstance(floor, (int, float)):
                raise RoutingError(f"observation_floors[{semantics!r}] must be a number")
            if not math.isfinite(float(floor)) or float(floor) < 0.0:
                raise RoutingError(
                    f"observation_floors[{semantics!r}] must be a non-negative, finite count"
                )

    def observation_floor_for(self, semantics: str | None) -> float | None:
        """The declared minimum observation for this engine-native quantity.

        Returns None when the quantity carries no semantics tag or has no
        declared floor: an undeclared counter is not evidence that some other
        engine's floor has been met.
        """
        if not semantics:
            return None
        if semantics in self.observation_floors:
            return self.observation_floors[semantics]
        if semantics in _ALPHA_BETA_NODE_SEMANTICS:
            return float(self.min_observation_nodes)
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": POLICY_NAME,
            "min_observation_nodes": self.min_observation_nodes,
            "checkpoint_interval_ms": self.checkpoint_interval_ms,
            "max_stages_per_owner": self.max_stages_per_owner,
            "extend_nodes": self.extend_nodes,
            "stop_max_reversal_risk": self.stop_max_reversal_risk,
            "stop_min_support": self.stop_min_support,
            "stop_min_stability_fraction": self.stop_min_stability_fraction,
            "stage_cpu_ms_estimate": self.stage_cpu_ms_estimate,
            "anchor_cpu_ms_estimate": self.anchor_cpu_ms_estimate,
            "stage_gpu_ms_estimate": self.stage_gpu_ms_estimate,
            "verify_stage_cpu_ms_estimate": self.verify_stage_cpu_ms_estimate,
            "verify_stage_gpu_ms_estimate": self.verify_stage_gpu_ms_estimate,
            "refine_stage_cpu_ms_estimate": self.refine_stage_cpu_ms_estimate,
            "refine_stage_gpu_ms_estimate": self.refine_stage_gpu_ms_estimate,
            "refine_oracle_cpu_ms_estimate": self.refine_oracle_cpu_ms_estimate,
            "refine_oracle_gpu_ms_estimate": self.refine_oracle_gpu_ms_estimate,
            "observation_floors": dict(self.observation_floors),
        }


def observe_owner(
    *,
    owner: str,
    instance: str,
    trajectory: SearchTrajectory | None,
    active: bool,
    stages_dispatched: int,
    elapsed_ms: float,
    wall_ms: float,
    truncated: bool = False,
    backlog: int = 0,
    lossy: bool = False,
) -> OwnerObservation:
    """Cheap observation. It nominates; it does not decide."""
    elapsed_fraction = 0.0 if wall_ms <= 0 else max(0.0, min(1.0, elapsed_ms / wall_ms))
    if trajectory is None:
        return OwnerObservation(
            owner=owner,
            instance=instance,
            active=active,
            stages_dispatched=stages_dispatched,
            leader=None,
            leader_flips=0,
            observation_count=0,
            elapsed_fraction=elapsed_fraction,
            stable_run_fraction=0.0,
            work_value=None,
            work_semantics=None,
            observation_truncated=truncated,
            observation_backlog=backlog,
            observation_lossy=lossy,
        )

    # The same function the calibration was fitted with, over the same input.
    leaders = trajectory.primary_moves_until(trajectory.span_ms)
    features = past_only_features(leaders)
    work = trajectory.work_at(trajectory.span_ms)
    return OwnerObservation(
        owner=owner,
        instance=instance,
        active=active,
        stages_dispatched=stages_dispatched,
        leader=leaders[-1] if leaders else None,
        leader_flips=features.leader_flips,
        observation_count=features.observation_count,
        elapsed_fraction=elapsed_fraction,
        stable_run_fraction=features.stable_run_fraction,
        work_value=None if work is None else work[0],
        work_semantics=None if work is None else work[1],
        observation_truncated=truncated,
        observation_backlog=backlog,
        observation_lossy=lossy,
    )


def propose(observation: OwnerObservation, policy: RoutingPolicy) -> RouteProposal:
    """Nominate the next computational move for one worker."""
    if observation.active:
        if observation.leader is None:
            return RouteProposal(
                RouteAction.CONTINUE,
                observation.owner,
                "no candidate observed yet; continue the minimum observation",
            )
        if observation.stable_run_fraction >= policy.stop_min_stability_fraction:
            return RouteProposal(
                RouteAction.STOP_WORKER,
                observation.owner,
                "leader has been stable for the declared fraction of this worker's observations",
            )
        return RouteProposal(
            RouteAction.CONTINUE,
            observation.owner,
            "leader is still moving; more observation is nominated",
        )

    if observation.stages_dispatched >= policy.max_stages_per_owner:
        return RouteProposal(
            RouteAction.HOLD, observation.owner, "stage budget for this owner is exhausted"
        )
    if observation.leader is None:
        return RouteProposal(
            RouteAction.ABSTAIN_BUY_COMPUTE,
            observation.owner,
            "worker finished without a usable observation; buy compute rather than assume",
        )
    if observation.stable_run_fraction >= policy.stop_min_stability_fraction:
        return RouteProposal(
            RouteAction.HOLD, observation.owner, "worker finished with a stable leader"
        )
    return RouteProposal(
        RouteAction.ABSTAIN_BUY_COMPUTE,
        observation.owner,
        "worker finished while its leader was still moving; buy more compute",
    )


class ConservativeRouter:
    """Auditable `conservative_v1` policy.

    It prefers spending compute over asserting a shortcut, and every suppression
    requires a calibrated, in-domain, sufficiently supported, low-risk verdict.
    """

    def __init__(
        self,
        *,
        envelope: ResourceEnvelope,
        policy: RoutingPolicy,
        calibration: ReversalRiskModel | None = None,
        calibration_source: str | None = None,
        clock: Callable[[], float] | None = None,
        verify_enabled: bool = False,
        refine_enabled: bool = False,
    ) -> None:
        self.envelope = envelope
        self.policy = policy
        self.calibration = calibration
        self.calibration_source = calibration_source
        self._clock = clock
        self.verify_enabled = bool(verify_enabled)
        self.refine_enabled = bool(refine_enabled)
        self.ledger = BudgetLedger(envelope, clock=clock)
        self.audit: RouteAudit | None = None
        self._reservations: dict[str, list[Reservation]] = {}
        self._anchor_reservation: Reservation | None = None
        self._specialist_reservations: dict[str, Reservation] = {}
        self._specialist_counter = 0
        self._specialist_unresolved = False
        self._fallback = False
        self._anchor_bound: tuple[bool, str] = (False, "not evaluated")
        self._anchor_reserved = False

    @property
    def checkpoint_interval_s(self) -> float:
        return max(0.005, self.policy.checkpoint_interval_ms / 1000.0)

    def decision_authority_snapshot(self) -> dict[str, object]:
        """Expose already-owned budget facts to the separate M14-C gate.

        This method grants no move authority and performs no filesystem or
        engine IO. The anchor reservation is expected to remain open until the
        post-output terminal resource sample, so only specialist reservations
        are required to be fully settled here.
        """
        open_counts = self.ledger.open_reservation_counts()
        return {
            "anchor_request_bounded": bool(self._anchor_bound[0]),
            "anchor_request_reason": str(self._anchor_bound[1]),
            "anchor_reserved": bool(self._anchor_reserved),
            "open_anchor_reservations": int(open_counts.get("anchor", 0)),
            "budget_within_envelope": self.ledger.within_envelope(),
            "partitions_within_caps": self.ledger.within_partition_caps(),
            "wall_within_envelope": (
                self.ledger.elapsed_ms() <= self.envelope.wall_ms
            ),
            "specialist_settlement_complete": not self._specialist_unresolved,
            "open_specialist_reservations": int(
                open_counts.get("verify", 0) + open_counts.get("refine", 0)
            ),
            "open_solver_reservations": int(open_counts.get("solver", 0)),
            "gpu_accounted": self._gpu_accounted(),
            "controller_fallback_latched": bool(self._fallback),
        }

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _classify_anchor_request(self, command: str) -> tuple[bool, str]:
        """Is the outward request actually bounded by the declared envelope?

        The controller must never constrain the anchor: the external request is
        the caller's, and narrowing it would breach the decision firewall. What
        it can do is refuse to *claim* envelope compliance for a search whose
        own request is unbounded, rather than quietly settling an estimate and
        printing a tidy budget snapshot.
        """
        try:
            request = parse_go_request(command)
        except SearchRequestError as exc:
            return False, f"external request could not be parsed: {exc}"
        limits = {item["name"]: item["value"] for item in request.get("limits", [])}
        if "infinite" in limits or "ponder" in limits:
            return False, "external request is unbounded (infinite/ponder)"
        if "movetime" in limits:
            movetime = float(limits["movetime"])
            if movetime <= self.envelope.wall_ms:
                return True, f"movetime {movetime:.0f}ms within wall envelope {self.envelope.wall_ms:.0f}ms"
            return False, (
                f"movetime {movetime:.0f}ms exceeds the declared wall envelope "
                f"{self.envelope.wall_ms:.0f}ms"
            )
        if "wtime" in limits or "btime" in limits:
            return False, "external request defers timing to the GUI clock"
        if "nodes" in limits or "depth" in limits or "mate" in limits:
            return False, (
                "external request bounds work but not wall time, so wall-envelope "
                "compliance cannot be asserted"
            )
        return False, "external request declares no limit"

    def on_run_start(self, context: Any) -> None:
        # Measure the envelope from the external `go`, not from here: legal-root
        # qualification runs before the router is involved, so a self-started
        # clock would hand a slow oracle a second full envelope.
        started = getattr(context, "started_monotonic", None)
        self.ledger = BudgetLedger(self.envelope, clock=self._clock, started=started)
        self.audit = RouteAudit(run_id=context.run_id, policy=POLICY_NAME)
        self._reservations = {}
        self._anchor_reservation = None
        self._specialist_reservations = {}
        self._specialist_counter = 0
        self._specialist_unresolved = False
        self._fallback = False
        self._anchor_reserved = False

        # Controller work already done for this run -- run preparation and the
        # legal-root oracle -- happened before any reservation existed. Charging
        # it now keeps it inside B instead of outside the accounting.
        already_elapsed = 0.0
        try:
            already_elapsed = max(0.0, float(context.elapsed_ms()))
        except Exception:  # pragma: no cover - defensive against older contexts
            already_elapsed = 0.0
        if already_elapsed > 0.0:
            self.ledger.charge_elapsed(
                "qualification",
                cpu_ms=already_elapsed,
                note="controller.qualification_ms",
            )

        if self.calibration is None:
            self.audit.note(
                "no calibration is loaded: this run may not authorize any suppression, "
                "so every stop proposal will be denied"
            )
        self._anchor_bound = self._classify_anchor_request(context.external_go_command)
        if not self._anchor_bound[0]:
            self.audit.note(
                f"outward request is not bounded by the declared envelope: {self._anchor_bound[1]}; "
                "this run cannot claim envelope compliance"
            )
        # The wall-time fallback is a DURATION, not a CPU figure. A four-thread
        # anchor running `go movetime 1000` spends roughly 4000 CPU-ms, and
        # reserving 1000 let `claimed` stay true while the anchor consumed four
        # times its share. Shadow workers were scaled in rounds five and six;
        # this path was not.
        anchor_cost = self.policy.anchor_cpu_ms_estimate
        anchor_threads = 1
        if not anchor_cost:
            anchor_threads = self._anchor_threads(context)
            anchor_cost = self.envelope.wall_ms * anchor_threads
        self._anchor_threads_used = anchor_threads
        try:
            # The outward anchor spends from the same envelope as everything
            # else; reserving it first is what makes the envelope binding.
            self._anchor_reservation = self.ledger.reserve(
                "anchor", cpu_ms=anchor_cost, purpose="anchor"
            )
            self._anchor_reserved = True
        except BudgetExceeded as exc:
            # The anchor is already searching and cannot be recalled. Its cost is
            # therefore an unrecorded obligation, and a run carrying one may not
            # report itself compliant however tidy the rest of the ledger looks.
            self.audit.note(
                f"anchor reservation exceeded the declared envelope: {exc}; "
                "this run cannot claim envelope compliance"
            )
            self._fallback = True

    def authorize_initial(self, context: Any, owner: str) -> bool:
        """Gate the first shadow stage so the envelope binds from the start."""
        audit = self.audit
        if audit is None:
            return False
        with self.ledger.controller_overhead("authorize_initial"):
            if self._fallback:
                audit.note(f"initial dispatch for {owner} refused: anchor-only fallback is active")
                return False
            if self.ledger.wall_exhausted():
                # The extension path has always checked this; the initial one
                # did not. Preparation and legal-root qualification can consume
                # the wall envelope while the anchor is still searching, and
                # every initial stage was then reserved and dispatched past the
                # declared deadline, running until some later checkpoint
                # noticed. An envelope that binds only after the first stage is
                # not the envelope that was declared.
                audit.note(
                    f"initial dispatch for {owner} refused: the wall envelope was "
                    f"already exhausted ({self.ledger.elapsed_ms():.0f}ms of "
                    f"{self.envelope.wall_ms:.0f}ms) before any stage was dispatched"
                )
                self._fallback = True
                return False
            try:
                reservation = self.ledger.reserve(
                    f"shadow:{owner}",
                    cpu_ms=self.policy.stage_cpu_ms_estimate,
                    gpu_ms=self.policy.stage_gpu_ms_estimate,
                )
            except BudgetExceeded as exc:
                audit.note(f"initial dispatch for {owner} refused by the envelope: {exc}")
                return False
            self._reservations.setdefault(owner, []).append(reservation)
            return True

    def on_run_end(self, context: Any) -> None:
        audit = self.audit
        if audit is None:
            return
        # Native work is otherwise recorded only during checkpoints, and
        # `_await_completion` returns without a final one once the run is
        # cancelled -- by an external `stop`, by `on_anchor_complete: cancel`,
        # or by a drain deadline. Everything the engines reported after the last
        # checkpoint, including the final update before a stopped worker's
        # `bestmove`, never reached route.json; a cancellation before the first
        # checkpoint reported no native work at all.
        # Reconstructing every owner's trajectory and serializing the audit is
        # controller CPU like any other, and it happens before the snapshot the
        # claim is computed from. Leaving it outside the accounting let a run
        # report compliance while this work pushed it past `cpu_ms`.
        with self.ledger.controller_overhead("finalization"):
            self._record_final_native_work(context)
            for owner in list(self._reservations):
                self._settle_owner(context, owner)
        # A leftover specialist reservation means the coordinator did not tell
        # us whether the authorized work dispatched, completed, or how long it
        # actually ran. Close the reservation at its declared estimate so no
        # phantom capacity remains, but invalidate the envelope claim: the
        # estimate is not evidence of actual consumption.
        for token, reservation in list(self._specialist_reservations.items()):
            self.ledger.settle(reservation)
            self._specialist_reservations.pop(token, None)
            self._specialist_unresolved = True
            audit.note(
                f"specialist reservation {token} lacked explicit settlement; "
                "closed at its estimate and envelope claim invalidated"
            )

        if self._anchor_reservation is not None:
            anchor_cpu: float | None = None
            anchor_source = "declared_fallback"
            try:
                anchor_resource = context.anchor_resource()
            except AttributeError:  # pragma: no cover - older/fake contexts
                anchor_resource = None
            if isinstance(anchor_resource, dict) and anchor_resource.get("complete") is True:
                value = anchor_resource.get("cpu_ms")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    anchor_cpu = float(value)
                    anchor_source = "measured"
            self.ledger.settle(
                self._anchor_reservation,
                actual_cpu_ms=anchor_cpu,
                cpu_source=anchor_source,
            )
            self._anchor_reservation = None

        try:
            resource_required = bool(context.resource_measurement_required())
        except AttributeError:  # pragma: no cover - older/fake contexts
            resource_required = False
        try:
            resource_summary = context.seal_resource_report()
        except AttributeError:  # pragma: no cover
            resource_summary = None

        resource_qualified = not resource_required
        physical_cpu_within = not resource_required
        cpu_measurement = "stage_wall_ms_x_configured_threads"
        if isinstance(resource_summary, dict):
            if resource_required:
                resource_qualified = bool(resource_summary.get("qualified"))
            physical_cpu = resource_summary.get("physical_cpu_ms")
            if isinstance(physical_cpu, (int, float)) and not isinstance(physical_cpu, bool):
                physical_cpu_within = float(physical_cpu) <= self.envelope.cpu_ms
            elif resource_required:
                physical_cpu_within = False
            provider = resource_summary.get("provider")
            if resource_summary.get("qualified") and isinstance(provider, str):
                cpu_measurement = provider

        payload = {
            "schema_version": ROUTE_SCHEMA_VERSION,
            "run_id": audit.run_id,
            "policy": audit.policy,
            "thresholds": self.policy.as_dict(),
            "envelope": self.envelope.as_dict(),
            "calibration": self._calibration_provenance(),
            "budget": self.ledger.snapshot(),
            "resource_measurement": resource_summary,
            "envelope_claim": {
                # Reservation accounting staying inside B is necessary but not
                # sufficient: if the outward request itself is not bounded by the
                # envelope, the search as a whole was not either.
                "anchor_request_bounded": self._anchor_bound[0],
                "anchor_request_reason": self._anchor_bound[1],
                "anchor_cost_reserved": self._anchor_reserved,
                "gpu_accounted": self._gpu_accounted(),
                "reservations_within_envelope": self.ledger.within_envelope(),
                "specialist_partitions_within_caps": self.ledger.within_partition_caps(),
                "specialist_settlement_complete": not self._specialist_unresolved,
                # Reservation accounting is about CPU and GPU ceilings. A run can
                # sit inside both and still have taken longer than the declared
                # wall envelope -- a slow legal-root oracle alone can do it --
                # and a claim that ignores the clock is not a claim about the
                # envelope that was declared.
                "wall_ms_elapsed": round(self.ledger.elapsed_ms(), 3),
                "wall_within_envelope": (
                    self.ledger.elapsed_ms() <= self.envelope.wall_ms
                ),
                "cpu_measurement": cpu_measurement,
                "physical_measurement_required": resource_required,
                "physical_measurement_qualified": resource_qualified,
                "physical_cpu_within_envelope": physical_cpu_within,
                "claimed": (
                    self._anchor_bound[0]
                    and self._anchor_reserved
                    and self._gpu_accounted()
                    and self.ledger.within_envelope()
                    and self.ledger.within_partition_caps()
                    and not self._specialist_unresolved
                    and self.ledger.elapsed_ms() <= self.envelope.wall_ms
                    and resource_qualified
                    and physical_cpu_within
                ),
            },
            "decisions": audit.decisions,
            "specialist_actions": audit.specialist_actions,
            "denials": audit.denials,
            "notes": audit.notes,
            "authority": (
                "Resource routing grants compute authority only, never move authority. "
                "Without the explicit M14-C hybrid gate the outward bestmove remained "
                "the unrestricted Stockfish anchor's; when that gate is enabled, "
                "DecisionAuthorization selects HYBRID or deterministic anchor fallback "
                "separately from this routing record."
            ),
        }
        try:
            atomic_write_text(
                Path(context.run_dir) / "route.json",
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )
        except OSError as exc:
            # The outward anchor has already answered, so this must not rewrite
            # chess authority. It *must* however invalidate the evidence run:
            # ShadowRunCoordinator catches this RoutingError and records it in
            # the parent replay manifest before that manifest is finalized.
            raise RoutingError(f"could not persist route.json: {exc}") from exc

    def _gpu_accounted(self) -> bool:
        """A declared GPU envelope with no per-stage estimate accounts nothing.

        Reserving only CPU would let a GPU-backed worker consume arbitrary
        accelerator time while the ledger reported itself inside the envelope.
        """
        if self.envelope.gpu_ms <= 0.0:
            return True
        if self.policy.stage_gpu_ms_estimate <= 0.0:
            return False
        if self.verify_enabled and self.policy.verify_stage_gpu_ms_estimate <= 0.0:
            return False
        if self.refine_enabled and (
            self.policy.refine_stage_gpu_ms_estimate <= 0.0
            or self.policy.refine_oracle_gpu_ms_estimate <= 0.0
        ):
            return False
        return True

    def release_undispatched(self, owner: str) -> None:
        """Return the most recent extension reservation for `owner`.

        The coordinator calls this when it could not run an extension the
        router authorized. The reservation covers a stage that will never
        exist, so holding it denies capacity to real work and leaves
        finalization to settle spend that never happened.
        """
        if self.ledger is None:
            return
        reservations = self._reservations.get(owner)
        if not reservations:
            return
        reservation = reservations.pop()
        if not reservations:
            self._reservations.pop(owner, None)
        self.ledger.release(reservation)
        if self.audit is not None:
            self.audit.note(
                f"released the extension reservation for {owner}: the coordinator "
                "could not dispatch the authorized stage"
            )

    def _specialist_cost(self, phase: str) -> tuple[str, float, float]:
        if phase == "verify":
            return (
                "verify",
                self.policy.verify_stage_cpu_ms_estimate,
                self.policy.verify_stage_gpu_ms_estimate,
            )
        if phase == "refine":
            return (
                "refine",
                self.policy.refine_stage_cpu_ms_estimate,
                self.policy.refine_stage_gpu_ms_estimate,
            )
        if phase == "refine_oracle":
            return (
                "refine",
                self.policy.refine_oracle_cpu_ms_estimate,
                self.policy.refine_oracle_gpu_ms_estimate,
            )
        raise RoutingError(f"unknown specialist phase: {phase!r}")

    def authorize_specialist(
        self,
        context: Any,
        *,
        phase: str,
        owner: str | None = None,
        target_id: str | None = None,
    ) -> str | None:
        """Reserve active VERIFY/REFINE work before the coordinator dispatches it.

        Nomination remains in the execution layer; this method grants only
        resource authority. A missing reservation is a hard no-dispatch result.
        """

        audit = self.audit
        if audit is None:
            return None
        purpose, cpu_ms, gpu_ms = self._specialist_cost(phase)
        if phase == "verify" and not self.verify_enabled:
            enabled = False
        elif phase.startswith("refine") and not self.refine_enabled:
            enabled = False
        else:
            enabled = True

        with self.ledger.controller_overhead(f"authorize_{phase}"):
            before_cpu = self.ledger.available_cpu_ms(purpose=purpose)
            before_gpu = self.ledger.available_gpu_ms(purpose=purpose)
            reason = "authorized"
            reservation: Reservation | None = None
            if not enabled:
                reason = "phase is not enabled in this active profile"
            elif self._fallback:
                reason = "anchor-only fallback is active"
            elif self.ledger.wall_exhausted():
                reason = "wall envelope is exhausted"
                self._fallback = True
            else:
                lane_parts = [VERIFY_LANE if purpose == "verify" else REFINE_LANE]
                if target_id:
                    lane_parts.append(str(target_id))
                if owner:
                    lane_parts.append(str(owner))
                lane = ":".join(lane_parts)
                try:
                    reservation = self.ledger.reserve(
                        lane,
                        cpu_ms=cpu_ms,
                        gpu_ms=gpu_ms,
                        purpose=purpose,
                    )
                except BudgetExceeded as exc:
                    reason = str(exc)

            self._specialist_counter += 1
            token = (
                None
                if reservation is None
                else f"{phase}:{self._specialist_counter}:{reservation.reservation_id}"
            )
            if reservation is not None and token is not None:
                self._specialist_reservations[token] = reservation

            audit.record_specialist(
                {
                    "event": "authorize",
                    "checkpoint_ms": round(float(context.elapsed_ms()), 3),
                    "phase": phase,
                    "owner": owner,
                    "target_id": target_id,
                    "reservation_token": token,
                    "requested_cpu_ms": cpu_ms,
                    "requested_gpu_ms": gpu_ms,
                    "available_cpu_ms_before": round(before_cpu, 3),
                    "available_gpu_ms_before": round(before_gpu, 3),
                    "granted": reservation is not None,
                    "reason": reason,
                }
            )
            return token

    def settle_specialist(
        self,
        token: str,
        *,
        actual_wall_ms: float | None = None,
        threads: int = 1,
        actual_cpu_ms: float | None = None,
        measurement_source: str = "estimated_fallback",
    ) -> None:
        """Settle one VERIFY/REFINE reservation without conflating estimate and fact."""

        reservation = self._specialist_reservations.pop(token, None)
        if reservation is None:
            return
        actual_cpu = actual_cpu_ms
        source = measurement_source
        if actual_cpu is None and actual_wall_ms is not None:
            actual_cpu = max(0.0, float(actual_wall_ms)) * max(1, int(threads))
            source = "estimated_fallback"
        if actual_cpu is None:
            source = "declared_fallback"
        self.ledger.settle(
            reservation,
            actual_cpu_ms=actual_cpu,
            cpu_source=source,
        )
        if self.audit is not None:
            self.audit.record_specialist(
                {
                    "event": "settle",
                    "checkpoint_ms": round(self.ledger.elapsed_ms(), 3),
                    "phase": token.split(":", 1)[0],
                    "owner": None,
                    "target_id": None,
                    "reservation_token": token,
                    "requested_cpu_ms": reservation.cpu_ms,
                    "requested_gpu_ms": reservation.gpu_ms,
                    "actual_cpu_ms": (
                        reservation.cpu_ms if actual_cpu is None else actual_cpu
                    ),
                    "cpu_source": source,
                    "granted": True,
                    "reason": "settled dispatched specialist work",
                }
            )

    def release_specialist(self, token: str, *, reason: str) -> None:
        """Release a reservation for specialist work that never dispatched."""

        reservation = self._specialist_reservations.pop(token, None)
        if reservation is None:
            return
        self.ledger.release(reservation)
        if self.audit is not None:
            self.audit.record_specialist(
                {
                    "event": "release",
                    "checkpoint_ms": round(self.ledger.elapsed_ms(), 3),
                    "phase": token.split(":", 1)[0],
                    "owner": None,
                    "target_id": None,
                    "reservation_token": token,
                    "requested_cpu_ms": reservation.cpu_ms,
                    "requested_gpu_ms": reservation.gpu_ms,
                    "granted": True,
                    "reason": reason,
                }
            )

    def charge_controller_elapsed(self, label: str, elapsed_ms: float) -> None:
        """Charge controller-side specialist preparation/restoration work."""

        value = max(0.0, float(elapsed_ms))
        self.ledger.charge_elapsed(
            f"controller:{label}",
            cpu_ms=value,
            note=f"controller.{label}_ms",
            purpose="controller",
        )

    def _calibration_provenance(self) -> dict[str, Any] | None:
        if self.calibration is None:
            return None
        return {
            "model_id": self.calibration.model_id,
            "model_kind": self.calibration.model_kind,
            "extractor_version": self.calibration.extractor_version,
            "min_support": self.calibration.min_support,
            "source": self.calibration_source,
            "evaluation": self.calibration.evaluation,
        }

    # ------------------------------------------------------------------
    # checkpoint
    # ------------------------------------------------------------------

    def on_checkpoint(self, context: Any) -> list[RouterCommand]:
        audit = self.audit
        if audit is None:
            return []
        commands: list[RouterCommand] = []
        with self.ledger.controller_overhead("checkpoint"):
            elapsed = context.elapsed_ms()
            wall = self.envelope.wall_ms

            if self.ledger.wall_exhausted() and not self._fallback:
                self._fallback = True
                audit.note("wall envelope exhausted: falling back to anchor-only compute")
                for owner in context.active_owners():
                    commands.append(
                        RouterCommand(
                            action="stop_worker",
                            owner=owner,
                            reason="wall envelope exhausted",
                        )
                    )
                return commands

            try:
                owners = tuple(context.dispatchable_owners())
            except AttributeError:  # pragma: no cover - defensive against older contexts
                owners = tuple(context.owners)
            for owner in owners:
                # Drain first, then read. Measuring the backlog after building
                # the trajectory would report a queue that had already emptied
                # into events this observation never looked at.
                backlog = 0
                try:
                    backlog = int(context.owner_events_pending(owner))
                except AttributeError:  # pragma: no cover - older contexts
                    backlog = 0
                trajectory = self._live_trajectory(context, owner)
                truncated = False
                try:
                    truncated = bool(context.owner_events_truncated(owner))
                except AttributeError:  # pragma: no cover - older contexts
                    truncated = False
                if truncated:
                    audit.note(
                        f"owner {owner} live observation view is truncated; "
                        "suppression is withheld for this worker"
                    )
                if backlog:
                    audit.note(
                        f"owner {owner} had {backlog} telemetry event(s) in flight when "
                        "this checkpoint read it; suppression is withheld for this worker"
                    )
                lossy = False
                try:
                    lossy = bool(context.owner_evidence_lossy(owner))
                except AttributeError:  # pragma: no cover - older contexts
                    lossy = False
                if lossy:
                    audit.note(
                        f"owner {owner} has permanently lost telemetry evidence "
                        "(dropped events or failed adapter translation); suppression "
                        "is withheld for this worker for the rest of the run"
                    )
                observation = observe_owner(
                    owner=owner,
                    instance=context.owner_instance(owner),
                    trajectory=trajectory,
                    active=context.owner_active(owner),
                    stages_dispatched=context.owner_stages(owner),
                    elapsed_ms=elapsed,
                    wall_ms=wall,
                    truncated=truncated,
                    backlog=backlog,
                    lossy=lossy,
                )
                if observation.work_value is not None and observation.work_semantics:
                    # Tagged per semantics and never summed across them: the
                    # audit format promises these counters, so a real run has to
                    # actually carry them.
                    self.ledger.record_native_work(
                        f"shadow:{owner}",
                        value=observation.work_value,
                        semantics=observation.work_semantics,
                        # An extension is a fresh `go`, so the engine's counter
                        # restarts. Tag the stage so the ledger maxes within it
                        # and sums across stages instead of reporting the
                        # largest single stage as the lane's whole output.
                        stage=f"{owner}#{observation.stages_dispatched}",
                    )
                if not observation.active and self._reservations.get(owner):
                    # The stage finished: convert its reservation into measured
                    # spend before deciding whether to buy any more.
                    self._settle_owner(context, owner)
                proposal = propose(observation, self.policy)
                decision = self._authorize(proposal, observation, elapsed)
                audit.record(decision)
                command = self._to_command(decision, context)
                if command is not None:
                    commands.append(command)
        return commands

    def _settle_owner(self, context: Any, owner: str) -> None:
        """Charge one EXPLORE stage from physical CPU when available.

        Reservation size remains an admission-time declaration. A complete
        procfs measurement becomes settlement spend; otherwise the pre-M14-B
        wall-times-threads estimate remains a conservative development fallback
        and is labelled as such in the ledger.
        """
        actual_cpu = None
        source = "estimated_fallback"
        try:
            resource = context.owner_last_stage_resource(owner)
        except AttributeError:  # pragma: no cover - older contexts
            resource = None
        if isinstance(resource, dict) and resource.get("complete") is True:
            value = resource.get("cpu_ms")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                actual_cpu = float(value)
                source = "measured"

        if actual_cpu is None:
            wall_ms = None
            try:
                wall_ms = context.owner_last_stage_ms(owner)
            except AttributeError:  # pragma: no cover
                wall_ms = None
            if wall_ms is not None:
                actual_cpu = float(wall_ms) * self._owner_threads(context, owner)
                source = "estimated_fallback"

        for reservation in self._reservations.pop(owner, []):
            self.ledger.settle(
                reservation,
                actual_cpu_ms=actual_cpu,
                cpu_source=(source if actual_cpu is not None else "declared_fallback"),
            )

    def _record_final_native_work(self, context: Any) -> None:
        """Reconstruct each worker's last reported counter before finalizing."""
        if self.ledger is None:
            return
        try:
            owners = tuple(context.dispatchable_owners())
        except AttributeError:  # pragma: no cover - older contexts
            owners = tuple(getattr(context, "owners", ()))
        for owner in owners:
            trajectory = self._live_trajectory(context, owner)
            if trajectory is None:
                continue
            work = trajectory.work_at(trajectory.span_ms)
            if work is None:
                continue
            value, semantics = work[0], work[1]
            if not semantics:
                continue
            try:
                stages = int(context.owner_stages(owner))
            except (AttributeError, TypeError, ValueError):
                stages = 0
            self.ledger.record_native_work(
                f"shadow:{owner}",
                value=value,
                semantics=semantics,
                stage=f"{owner}#{stages}",
            )

    @staticmethod
    def _anchor_threads(context: Any) -> int:
        """Declared `Threads` for the outward anchor, defaulting to one."""
        try:
            return max(1, int(context.anchor_threads()))
        except (AttributeError, TypeError, ValueError):
            return 1

    @staticmethod
    def _owner_threads(context: Any, owner: str) -> int:
        """Declared thread count for this worker, defaulting to one."""
        try:
            threads = int(context.owner_threads(owner))
        except (AttributeError, TypeError, ValueError):
            return 1
        return max(1, threads)

    def _live_trajectory(self, context: Any, owner: str) -> SearchTrajectory | None:
        events = context.owner_events(owner)
        if not events:
            return None
        instance = context.owner_instance(owner)
        trajectories = reconstruct_stream(
            events,
            instance=instance,
            family=context.owner_family(owner),
            role="shadow",
            owner_roots={owner: context.owner_roots.get(owner, ())},
        )
        return trajectories[-1] if trajectories else None

    # ------------------------------------------------------------------
    # admissibility
    # ------------------------------------------------------------------

    def _authorize(
        self,
        proposal: RouteProposal,
        observation: OwnerObservation,
        elapsed_ms: float,
    ) -> RouteDecision:
        gates: list[Gate] = []
        verdict: CalibrationEvaluation | None = None
        budget_snapshot = {
            "available_solver_cpu_ms": self.ledger.available_cpu_ms(),
            "wall_remaining_ms": self.ledger.wall_remaining_ms(),
        }

        if proposal.action is RouteAction.STOP_WORKER:
            present = self.calibration is not None
            gates.append(
                Gate(
                    "calibration_present",
                    present,
                    "a calibrated model is required before any suppression",
                )
            )
            if present:
                # A model that was never evaluated on held-out data has not
                # earned the right to license a shortcut, however confident its
                # in-sample buckets look.
                held_out = int(self.calibration.evaluation.get("test_rows") or 0)
                # `test_rows > 0` only says the model saw *some* held-out data.
                # A bucket can be well supported in training while every held-out
                # row landed in unrelated buckets, so the risk estimate actually
                # being served was never evaluated out of sample at all. The
                # reliability table records which buckets had held-out counts;
                # authorization requires this one to be among them.
                reliability = self.calibration.evaluation.get("reliability") or []
                served_bucket = bucket_key(
                    observation.calibration_features(), scope=observation.owner
                )
                evaluated = {
                    str(entry.get("bucket"))
                    for entry in reliability
                    if isinstance(entry, dict) and int(entry.get("count") or 0) > 0
                }
                gates.append(
                    Gate(
                        "calibration_validated",
                        held_out > 0 and served_bucket in evaluated,
                        (
                            f"held-out rows {held_out}; bucket {served_bucket!r} "
                            f"{'has' if served_bucket in evaluated else 'has no'} "
                            "out-of-sample evaluation of its own"
                        ),
                    )
                )
                verdict = self.calibration.evaluate(
                    observation.calibration_features(), scope=observation.owner
                )
                gates.append(
                    Gate(
                        "calibration_in_domain",
                        verdict.in_domain,
                        verdict.reason or f"bucket {verdict.bucket}",
                    )
                )
                gates.append(
                    Gate(
                        "support",
                        verdict.support >= self.policy.stop_min_support,
                        f"support {verdict.support} vs floor {self.policy.stop_min_support}",
                    )
                )
                gates.append(
                    Gate(
                        "reversal_risk",
                        verdict.risk <= self.policy.stop_max_reversal_risk,
                        f"risk {verdict.risk:.4f} vs threshold {self.policy.stop_max_reversal_risk}",
                    )
                )
            # `min_observation_nodes` is an alpha-beta node count. LC0 reports
            # `lc0.uci_nodes`, which the telemetry layer declares incomparable
            # with it -- combining the two elsewhere raises ScaleMixingError --
            # so the floor is declared per semantics and an untagged or
            # unrecognised quantity cannot satisfy it at all.
            floor = self.policy.observation_floor_for(observation.work_semantics)
            gates.append(
                Gate(
                    "minimum_observation",
                    floor is not None and (observation.work_value or 0.0) >= floor,
                    (
                        f"work {observation.work_value} ({observation.work_semantics}) "
                        f"vs floor {floor}"
                        if floor is not None
                        else (
                            f"no declared observation floor for semantics "
                            f"{observation.work_semantics!r}; an alpha-beta node count "
                            "and an LC0 visit-derived count are not the same quantity"
                        )
                    ),
                )
            )
            gates.append(
                Gate(
                    "observation_current",
                    not observation.observation_truncated,
                    "the live event view stopped tracking this stream, so the "
                    "observation is a stale prefix",
                )
            )
            gates.append(
                Gate(
                    "observation_drained",
                    observation.observation_backlog == 0,
                    f"{observation.observation_backlog} telemetry event(s) were still "
                    "in flight, so the engine has already reported something this "
                    "observation did not see",
                )
            )
            gates.append(
                Gate(
                    "observation_intact",
                    not observation.observation_lossy,
                    "this stream dropped events or failed adapter translation, so an "
                    "observation it has lost cannot be distinguished from one the "
                    "engine never made",
                )
            )

        elif proposal.action in (RouteAction.EXTEND, RouteAction.ABSTAIN_BUY_COMPUTE):
            gates.append(
                Gate(
                    "stage_budget",
                    observation.stages_dispatched < self.policy.max_stages_per_owner,
                    f"stages {observation.stages_dispatched} vs max {self.policy.max_stages_per_owner}",
                )
            )
            affordable = self.ledger.can_afford(
                cpu_ms=self.policy.stage_cpu_ms_estimate,
                gpu_ms=self.policy.stage_gpu_ms_estimate,
            )
            gates.append(
                Gate(
                    "envelope",
                    affordable,
                    f"needs {self.policy.stage_cpu_ms_estimate}ms, "
                    f"available {budget_snapshot['available_solver_cpu_ms']:.1f}ms",
                )
            )
            gates.append(
                Gate(
                    "not_in_fallback",
                    not self._fallback,
                    "anchor-only fallback suppresses new observational work",
                )
            )

        granted = all(gate.passed for gate in gates)
        action = proposal.action
        reason = proposal.rationale
        if not granted:
            failed = [gate.name for gate in gates if not gate.passed]
            if proposal.action is RouteAction.STOP_WORKER:
                # Instability, or the absence of evidence, is never authorization.
                action = RouteAction.CONTINUE
                reason = f"stop denied by {failed}; continuing observation instead"
            else:
                action = RouteAction.HOLD
                reason = f"{proposal.action.value} denied by {failed}; holding"

        return RouteDecision(
            checkpoint_ms=elapsed_ms,
            observation=observation,
            proposal=proposal,
            gates=tuple(gates),
            granted=granted,
            action=action,
            reason=reason,
            calibration=None if verdict is None else verdict.as_dict(),
            thresholds=self.policy.as_dict(),
            budget=budget_snapshot,
        )

    @staticmethod
    def _consumed_ms(context: Any, owner: str | None = None) -> float | None:
        if owner is None:
            return None
        try:
            return context.owner_elapsed_stage_ms(owner)
        except AttributeError:  # pragma: no cover - older contexts
            return None

    def _to_command(self, decision: RouteDecision, context: Any = None) -> RouterCommand | None:
        owner = decision.observation.owner
        if decision.action is RouteAction.STOP_WORKER and decision.granted:
            # Keep the reservation open until the backend actually terminates
            # the stage. Settling here used the checkpoint timestamp and omitted
            # CPU consumed between the stop request and the terminal bestmove.
            # The completion/finalization path now takes the physical endpoint
            # sample and settles the reservation from that measurement. Holding
            # capacity until then is conservative and prevents phantom reuse.
            return RouterCommand(action="stop_worker", owner=owner, reason=decision.reason)
        if (
            decision.action in (RouteAction.EXTEND, RouteAction.ABSTAIN_BUY_COMPUTE)
            and decision.granted
        ):
            try:
                self._reservations.setdefault(owner, []).append(
                    self.ledger.reserve(
                        f"shadow:{owner}",
                        cpu_ms=self.policy.stage_cpu_ms_estimate,
                        gpu_ms=self.policy.stage_gpu_ms_estimate,
                    )
                )
            except BudgetExceeded:
                return None
            return RouterCommand(
                action="extend",
                owner=owner,
                extend_limit={"nodes": self.policy.extend_nodes},
                reason=decision.reason,
            )
        return None


def build_router(config: Any) -> ConservativeRouter:
    """Construct the active router from a validated runtime configuration."""
    envelope = ResourceEnvelope.from_config(config.budget)
    policy = RoutingPolicy.from_config(config.routing)

    calibration: ReversalRiskModel | None = None
    source = (config.routing or {}).get("calibration")
    if source:
        path = Path(source)
        if not path.is_absolute():
            path = (config.root / source).resolve()
        try:
            calibration = load_calibration(path)
        except CalibrationError as exc:
            # A broken or foreign calibration must not silently degrade into an
            # uncalibrated policy that still looks configured.
            raise RoutingError(f"declared calibration could not be loaded: {exc}") from exc

    return ConservativeRouter(
        envelope=envelope,
        policy=policy,
        calibration=calibration,
        calibration_source=None if not source else str(source),
        verify_enabled=config.verification is not None,
        refine_enabled=config.refinement is not None,
    )
