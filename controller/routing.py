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
It cannot change the outward move: the unrestricted anchor remains the sole
decision authority, exactly as in shadow mode. Stopping a shadow worker returns
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
)
from controller.calibration import (
    CalibrationError,
    CalibrationEvaluation,
    ReversalRiskModel,
    load_calibration,
)
from common.residuals import past_only_features
from common.search_request import SearchRequestError, parse_go_request
from controller.replay_analysis import SearchTrajectory, reconstruct_stream
from controller.shadow import RouterCommand


ROUTE_SCHEMA_VERSION = 1
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

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "RoutingPolicy":
        if not config:
            raise RoutingError("active mode requires an explicit routing configuration")
        policy = config.get("policy", POLICY_NAME)
        if policy != POLICY_NAME:
            raise RoutingError(f"unsupported routing policy: {policy!r}")
        try:
            return cls(
                min_observation_nodes=int(config.get("min_observation_nodes", 4000)),
                checkpoint_interval_ms=float(config.get("checkpoint_interval_ms", 120)),
                max_stages_per_owner=int(config.get("max_stages_per_owner", 3)),
                extend_nodes=int(config.get("extend_nodes", 8000)),
                stop_max_reversal_risk=float(config.get("stop_max_reversal_risk", 0.05)),
                stop_min_support=int(config.get("stop_min_support", 25)),
                stop_min_stability_fraction=float(config.get("stop_min_stability_fraction", 0.6)),
                stage_cpu_ms_estimate=float(config.get("stage_cpu_ms_estimate", 400.0)),
                anchor_cpu_ms_estimate=float(config.get("anchor_cpu_ms_estimate", 0.0)),
                stage_gpu_ms_estimate=float(config.get("stage_gpu_ms_estimate", 0.0)),
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
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise RoutingError(f"{name} must be a non-negative, finite duration")

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
    ) -> None:
        self.envelope = envelope
        self.policy = policy
        self.calibration = calibration
        self.calibration_source = calibration_source
        self._clock = clock
        self.ledger = BudgetLedger(envelope, clock=clock)
        self.audit: RouteAudit | None = None
        self._reservations: dict[str, list[Reservation]] = {}
        self._anchor_reservation: Reservation | None = None
        self._fallback = False
        self._anchor_bound: tuple[bool, str] = (False, "not evaluated")
        self._anchor_reserved = False

    @property
    def checkpoint_interval_s(self) -> float:
        return max(0.005, self.policy.checkpoint_interval_ms / 1000.0)

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
        anchor_cost = self.policy.anchor_cpu_ms_estimate or self.envelope.wall_ms
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
        for owner in list(self._reservations):
            self._settle_owner(context, owner)
        if self._anchor_reservation is not None:
            # The anchor is charged its full declared reservation, not a
            # measured value: shadow finalization happens before the anchor
            # completes, so the controller cannot observe the real figure here.
            # Charging the reservation errs toward over-counting, which is the
            # safe direction for an envelope claim.
            self.ledger.settle(self._anchor_reservation)
            self._anchor_reservation = None

        payload = {
            "schema_version": ROUTE_SCHEMA_VERSION,
            "run_id": audit.run_id,
            "policy": audit.policy,
            "thresholds": self.policy.as_dict(),
            "envelope": self.envelope.as_dict(),
            "calibration": self._calibration_provenance(),
            "budget": self.ledger.snapshot(),
            "envelope_claim": {
                # Reservation accounting staying inside B is necessary but not
                # sufficient: if the outward request itself is not bounded by the
                # envelope, the search as a whole was not either.
                "anchor_request_bounded": self._anchor_bound[0],
                "anchor_request_reason": self._anchor_bound[1],
                "anchor_cost_reserved": self._anchor_reserved,
                "gpu_accounted": self._gpu_accounted(),
                "reservations_within_envelope": self.ledger.within_envelope(),
                "claimed": (
                    self._anchor_bound[0]
                    and self._anchor_reserved
                    and self._gpu_accounted()
                    and self.ledger.within_envelope()
                ),
            },
            "decisions": audit.decisions,
            "denials": audit.denials,
            "notes": audit.notes,
            "authority": (
                "This record governs shadow observation compute only. The outward "
                "bestmove remained the unrestricted anchor's in every decision below."
            ),
        }
        try:
            (Path(context.run_dir) / "route.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError:  # pragma: no cover - routing evidence is best effort
            pass

    def _gpu_accounted(self) -> bool:
        """A declared GPU envelope with no per-stage estimate accounts nothing.

        Reserving only CPU would let a GPU-backed worker consume arbitrary
        accelerator time while the ledger reported itself inside the envelope.
        """
        if self.envelope.gpu_ms <= 0.0:
            return True
        return self.policy.stage_gpu_ms_estimate > 0.0

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
        """Charge measured stage time, falling back to the declared estimate."""
        measured = None
        try:
            measured = context.owner_last_stage_ms(owner)
        except AttributeError:  # pragma: no cover - defensive against older contexts
            measured = None
        for reservation in self._reservations.pop(owner, []):
            self.ledger.settle(reservation, actual_cpu_ms=measured)

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
                gates.append(
                    Gate(
                        "calibration_validated",
                        held_out > 0,
                        f"held-out rows {held_out}; a model with no out-of-sample "
                        "evaluation cannot authorize suppression",
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
            gates.append(
                Gate(
                    "minimum_observation",
                    (observation.work_value or 0.0) >= self.policy.min_observation_nodes,
                    f"work {observation.work_value} vs floor {self.policy.min_observation_nodes}",
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
            # The worker already burned CPU producing the observations that
            # authorized this stop. Releasing the whole reservation would record
            # none of it, free capacity that was in fact consumed, and let
            # route.json claim envelope compliance while omitting the work.
            consumed = self._consumed_ms(context, owner)
            reservations = self._reservations.pop(owner, [])
            for index, reservation in enumerate(reservations):
                if index == len(reservations) - 1 and consumed is not None:
                    # Unclamped on purpose: a stage that outran its estimate
                    # really did consume that CPU, and BudgetLedger.settle
                    # supports charging above the reservation. Clamping would
                    # free capacity that was spent and understate the run.
                    self.ledger.settle(reservation, actual_cpu_ms=consumed)
                else:
                    self.ledger.release(reservation)
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
    )
