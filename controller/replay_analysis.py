"""Reconstruct replay bundles and answer counterfactual stopping questions.

This is the **derived** layer. It reads raw replay bundles and produces analysis
objects. It never writes into a raw bundle and never makes a routing decision.

Label discipline
----------------
Every label here is descriptive. `later_leader_changed` means exactly that: the
engine's own preference changed later in its own search. It does not mean the
earlier move was wrong. This repository has no independent chess truth source,
so no label may be named or interpreted as correctness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.residuals import (
    Margin,
    ResidualError,
    TaggedValue,
    leader_flip_count,
    pv_persistence,
    stabilization_index,
    within_engine_margin,
)
from controller.replay import ReplayError, load_manifest


DEFAULT_CHECKPOINT_FRACTIONS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


class ReplayAnalysisError(ResidualError):
    """Raised when a replay bundle cannot be reconstructed for analysis."""


@dataclass(frozen=True)
class Observation:
    """One reconstructed `candidate.update`."""

    sequence: int
    observed_ms: float
    multipv_index: int
    move: str
    pv: tuple[str, ...]
    evaluations: tuple[TaggedValue, ...]
    work: tuple[tuple[float, str, str], ...]  # (value, unit, semantics)

    def primary_evaluation(self) -> TaggedValue | None:
        for evaluation in self.evaluations:
            if evaluation.kind in ("cp", "mate", "scalar"):
                return evaluation
        return None


@dataclass
class SearchTrajectory:
    """One engine instance's reconstructed observations for one dispatched stage."""

    instance: str
    family: str
    role: str
    search_id: str
    variant: str
    position_id: str
    authorized_roots: tuple[str, ...]
    execution_mode: str
    owner: str | None
    observations: tuple[Observation, ...]
    bestmove: str | None
    complete: bool
    parse_errors: tuple[str, ...] = ()

    # -- views ---------------------------------------------------------------

    @property
    def span_ms(self) -> float:
        if not self.observations:
            return 0.0
        return self.observations[-1].observed_ms

    def observations_until(self, observed_ms: float) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.observed_ms <= observed_ms)

    def leader_at(self, observed_ms: float) -> str | None:
        """Latest primary-line move observed at or before `observed_ms`.

        MultiPV frames are not atomic in telemetry v1, so the leader is defined
        as the most recent `multipv_index == 1` observation rather than as a
        synthesized ranking snapshot.
        """
        leader: str | None = None
        for item in self.observations:
            if item.observed_ms > observed_ms:
                break
            if item.multipv_index == 1:
                leader = item.move
        return leader

    def pv_at(self, observed_ms: float) -> tuple[str, ...]:
        pv: tuple[str, ...] = ()
        for item in self.observations:
            if item.observed_ms > observed_ms:
                break
            if item.multipv_index == 1:
                pv = item.pv
        return pv

    def ranking_at(self, observed_ms: float) -> tuple[str, ...]:
        """Latest observed move per MultiPV index, ordered by index.

        This is a reconstruction of what the engine most recently reported at
        each index. It is not a claim that the engine held this exact ranking
        atomically at any instant.
        """
        latest: dict[int, Observation] = {}
        for item in self.observations:
            if item.observed_ms > observed_ms:
                break
            previous = latest.get(item.multipv_index)
            if previous is None or item.sequence >= previous.sequence:
                latest[item.multipv_index] = item
        ordered: list[str] = []
        for index in sorted(latest):
            move = latest[index].move
            if move not in ordered:
                ordered.append(move)
        return tuple(ordered)

    def work_at(self, observed_ms: float) -> tuple[float, str] | None:
        """Latest engine-native work counter with its semantics tag."""
        result: tuple[float, str] | None = None
        for item in self.observations:
            if item.observed_ms > observed_ms:
                break
            for value, _unit, semantics in item.work:
                result = (float(value), semantics)
        return result

    def within_engine_margin_at(self, observed_ms: float) -> Margin | None:
        """Primary-vs-runner-up margin, inside this engine's own scale only."""
        latest: dict[int, Observation] = {}
        for item in self.observations:
            if item.observed_ms > observed_ms:
                break
            previous = latest.get(item.multipv_index)
            if previous is None or item.sequence >= previous.sequence:
                latest[item.multipv_index] = item
        primary = latest.get(1)
        runner_up = latest.get(2)
        if primary is None or runner_up is None:
            return None
        first = primary.primary_evaluation()
        second = runner_up.primary_evaluation()
        if first is None or second is None:
            return None
        return within_engine_margin(first, second)

    @property
    def final_leader(self) -> str | None:
        """The engine's own final preference.

        `bestmove` is authoritative when present; otherwise the last observed
        primary line is used and the caller can see `complete == False`.
        """
        if self.bestmove is not None:
            return self.bestmove
        return self.leader_at(self.span_ms)

    def leaders_over(self, checkpoints: Sequence[float]) -> tuple[str | None, ...]:
        return tuple(self.leader_at(point) for point in checkpoints)


@dataclass
class ReplayBundle:
    """A loaded raw bundle plus its reconstructed trajectories."""

    run_dir: Path
    manifest: dict[str, Any]
    trajectories: tuple[SearchTrajectory, ...]
    missing_streams: tuple[str, ...] = ()
    load_errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])

    @property
    def variant(self) -> str:
        return str(self.manifest["position"]["variant"])

    @property
    def terminal(self) -> bool:
        return bool(self.manifest["legal_root_oracle"].get("terminal_universe"))

    @property
    def owner_roots(self) -> dict[str, tuple[str, ...]]:
        return {
            owner: tuple(moves)
            for owner, moves in self.manifest["ledger"]["owner_roots"].items()
        }

    @property
    def anchor(self) -> SearchTrajectory | None:
        for trajectory in self.trajectories:
            if trajectory.role == "anchor":
                return trajectory
        return None

    def shadows(self) -> tuple[SearchTrajectory, ...]:
        return tuple(item for item in self.trajectories if item.role == "shadow")

    def by_owner(self, owner: str) -> SearchTrajectory | None:
        for trajectory in self.trajectories:
            if trajectory.owner == owner:
                return trajectory
        return None

    def by_family(self, family: str, *, role: str = "shadow") -> SearchTrajectory | None:
        for trajectory in self.trajectories:
            if trajectory.family == family and trajectory.role == role:
                return trajectory
        return None

    @property
    def span_ms(self) -> float:
        return max((item.span_ms for item in self.trajectories), default=0.0)

    def checkpoints(
        self, fractions: Sequence[float] = DEFAULT_CHECKPOINT_FRACTIONS
    ) -> tuple[float, ...]:
        """Controller-clock checkpoints shared by every stream in the run.

        `observed_ms` is controller-side observation time relative to run start.
        It is therefore comparable across instances, unlike any engine-native
        work counter.
        """
        span = self.span_ms
        return tuple(round(span * fraction, 6) for fraction in fractions)


def _parse_evaluations(raw: Iterable[dict[str, Any]]) -> tuple[TaggedValue, ...]:
    values: list[TaggedValue] = []
    for item in raw:
        kind = item.get("kind")
        if kind == "wdl":
            # WDL is a distinct evaluation channel; it is not folded into the
            # scalar channel and is not used for margins in v1.
            continue
        value = item.get("value")
        semantics = item.get("semantics")
        if value is None or not isinstance(semantics, str):
            continue
        try:
            values.append(
                TaggedValue(
                    value=float(value),
                    semantics=semantics,
                    kind=str(kind),
                    bound=str(item.get("bound", "none")),
                )
            )
        except ResidualError:
            continue
    return tuple(values)


def _reconstruct_stream(
    events: Sequence[dict[str, Any]],
    *,
    instance: str,
    family: str,
    role: str,
    owner_roots: dict[str, tuple[str, ...]],
) -> list[SearchTrajectory]:
    trajectories: list[SearchTrajectory] = []
    current: dict[str, Any] | None = None
    observations: list[Observation] = []
    errors: list[str] = []

    def flush(bestmove: str | None, complete: bool) -> None:
        nonlocal current, observations, errors
        if current is None:
            return
        owner = current["controller"].get("owner")
        trajectories.append(
            SearchTrajectory(
                instance=instance,
                family=family,
                role=role,
                search_id=current["search_id"],
                variant=current["variant"],
                position_id=current["position_id"],
                authorized_roots=tuple(current["request"].get("root_moves", ()))
                or owner_roots.get(owner, ()),
                execution_mode=current["controller"].get("execution_mode", "unknown"),
                owner=owner,
                observations=tuple(observations),
                bestmove=bestmove,
                complete=complete,
                parse_errors=tuple(errors),
            )
        )
        current = None
        observations = []
        errors = []

    for event in events:
        event_type = event.get("event_type")
        if event_type == "search.started":
            flush(None, False)
            current = {
                "search_id": event["search_id"],
                "variant": event.get("variant", "standard"),
                "position_id": event["position_id"],
                "request": event.get("request", {}),
                "controller": event.get("controller", {}),
            }
            continue
        if current is None:
            errors.append(f"event {event_type!r} observed before search.started")
            continue
        if event_type == "candidate.update":
            candidate = event.get("candidate", {})
            try:
                observations.append(
                    Observation(
                        sequence=int(event["sequence"]),
                        observed_ms=float(event["observed_ms"]),
                        multipv_index=int(candidate["multipv_index"]),
                        move=str(candidate["move"]),
                        pv=tuple(str(move) for move in candidate.get("pv", ())),
                        evaluations=_parse_evaluations(candidate.get("evaluations", ())),
                        work=tuple(
                            (float(item["value"]), str(item["unit"]), str(item["semantics"]))
                            for item in event.get("work", ())
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"malformed candidate.update: {type(exc).__name__}: {exc}")
            continue
        if event_type == "search.complete":
            flush(event.get("bestmove"), True)
            continue

    flush(None, False)
    return trajectories


def load_bundle(run_dir: Path | str) -> ReplayBundle:
    """Load a raw replay bundle and reconstruct every engine trajectory."""
    run_dir = Path(run_dir)
    manifest = load_manifest(run_dir)
    owner_roots = {
        owner: tuple(moves) for owner, moves in manifest["ledger"]["owner_roots"].items()
    }

    trajectories: list[SearchTrajectory] = []
    missing: list[str] = []
    load_errors: list[str] = []

    for record in manifest.get("streams", []):
        path = run_dir / record["path"]
        if not path.is_file():
            missing.append(record["instance"])
            continue
        events: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                load_errors.append(f"{record['path']}:{line_no}: malformed JSON: {exc}")
                continue
            if not isinstance(value, dict):
                load_errors.append(f"{record['path']}:{line_no}: event must be an object")
                continue
            events.append(value)
        trajectories.extend(
            _reconstruct_stream(
                events,
                instance=record["instance"],
                family=record["engine"],
                role=record["role"],
                owner_roots=owner_roots,
            )
        )

    declared = {record["instance"] for record in manifest.get("streams", [])}
    for stage in manifest.get("stages", []):
        if stage["instance"] not in declared:
            missing.append(stage["instance"])

    return ReplayBundle(
        run_dir=run_dir,
        manifest=manifest,
        trajectories=tuple(trajectories),
        missing_streams=tuple(sorted(set(missing))),
        load_errors=tuple(load_errors),
    )


@dataclass(frozen=True)
class CounterfactualLabels:
    """Descriptive stopping labels for one engine at one checkpoint.

    None of these is a correctness label. They describe what the engine's own
    later search did, not what the right move was.
    """

    instance: str
    owner: str | None
    checkpoint_ms: float
    checkpoint_fraction: float
    leader: str | None
    final_leader: str | None
    later_leader_changed: bool | None
    later_pv_changed: bool | None
    stable_to_end: bool | None
    reversal_within_horizon: bool | None
    work_at_checkpoint: float | None
    work_semantics: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "owner": self.owner,
            "checkpoint_ms": self.checkpoint_ms,
            "checkpoint_fraction": self.checkpoint_fraction,
            "leader": self.leader,
            "final_leader": self.final_leader,
            "later_leader_changed": self.later_leader_changed,
            "later_pv_changed": self.later_pv_changed,
            "stable_to_end": self.stable_to_end,
            "reversal_within_horizon": self.reversal_within_horizon,
            "work_at_checkpoint": self.work_at_checkpoint,
            "work_semantics": self.work_semantics,
        }


def counterfactual_labels(
    trajectory: SearchTrajectory,
    checkpoints: Sequence[float],
    *,
    fractions: Sequence[float] | None = None,
    horizon_fraction: float = 0.25,
) -> tuple[CounterfactualLabels, ...]:
    """Answer 'what if this engine had stopped here?' from its own later search."""
    if horizon_fraction <= 0:
        raise ReplayAnalysisError("horizon_fraction must be positive")
    if fractions is None:
        span = checkpoints[-1] if checkpoints else 0.0
        fractions = tuple((point / span) if span else 0.0 for point in checkpoints)
    if len(fractions) != len(checkpoints):
        raise ReplayAnalysisError("fractions and checkpoints must be the same length")

    final_leader = trajectory.final_leader
    final_pv = trajectory.pv_at(trajectory.span_ms)
    span = trajectory.span_ms
    labels: list[CounterfactualLabels] = []

    for point, fraction in zip(checkpoints, fractions):
        leader = trajectory.leader_at(point)
        pv = trajectory.pv_at(point)
        work = trajectory.work_at(point)

        if leader is None or final_leader is None:
            changed: bool | None = None
            stable: bool | None = None
            pv_changed: bool | None = None
        else:
            changed = leader != final_leader
            later = [
                item.move
                for item in trajectory.observations
                if item.observed_ms >= point and item.multipv_index == 1
            ]
            stable = all(move == leader for move in later) and not changed
            pv_changed = tuple(pv) != tuple(final_pv)

        horizon_end = min(span, point + span * horizon_fraction)
        if leader is None:
            reversal: bool | None = None
        else:
            within = [
                item.move
                for item in trajectory.observations
                if point <= item.observed_ms <= horizon_end and item.multipv_index == 1
            ]
            reversal = any(move != leader for move in within)

        labels.append(
            CounterfactualLabels(
                instance=trajectory.instance,
                owner=trajectory.owner,
                checkpoint_ms=point,
                checkpoint_fraction=fraction,
                leader=leader,
                final_leader=final_leader,
                later_leader_changed=changed,
                later_pv_changed=pv_changed,
                stable_to_end=stable,
                reversal_within_horizon=reversal,
                work_at_checkpoint=None if work is None else work[0],
                work_semantics=None if work is None else work[1],
            )
        )
    return tuple(labels)


@dataclass(frozen=True)
class TrajectorySummary:
    """Whole-search descriptive summary for one engine instance."""

    instance: str
    family: str
    role: str
    owner: str | None
    authorized_root_count: int
    observation_count: int
    complete: bool
    final_leader: str | None
    leader_flips: int
    stabilization_fraction: float | None
    pv_persistence: float | None
    work_after_stability_ratio: float | None
    work_semantics: str | None
    parse_errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "engine": self.family,
            "role": self.role,
            "owner": self.owner,
            "authorized_root_count": self.authorized_root_count,
            "observation_count": self.observation_count,
            "complete": self.complete,
            "final_leader": self.final_leader,
            "leader_flips": self.leader_flips,
            "stabilization_fraction": self.stabilization_fraction,
            "pv_persistence": self.pv_persistence,
            "work_after_stability_ratio": self.work_after_stability_ratio,
            "work_semantics": self.work_semantics,
            "parse_errors": list(self.parse_errors),
        }


def summarize_trajectory(trajectory: SearchTrajectory) -> TrajectorySummary:
    """Temporal and speculative-waste summary computed inside one engine only."""
    primary = [item for item in trajectory.observations if item.multipv_index == 1]
    leaders = [item.move for item in primary]
    pvs = [item.pv for item in primary]

    stabilization = stabilization_index(leaders)
    stabilization_frac: float | None = None
    waste: float | None = None
    work_semantics: str | None = None
    if stabilization is not None and len(primary) > 1:
        stabilization_frac = stabilization / (len(primary) - 1)
        stable_work = None
        final_work = None
        for index, item in enumerate(primary):
            for value, _unit, semantics in item.work:
                work_semantics = semantics
                final_work = float(value)
                if index == stabilization and stable_work is None:
                    stable_work = float(value)
        if stable_work is not None and final_work is not None and final_work > 0:
            # Engine-native work only: this ratio is never compared across
            # engines because the counters are not the same quantity.
            waste = max(0.0, (final_work - stable_work) / final_work)

    return TrajectorySummary(
        instance=trajectory.instance,
        family=trajectory.family,
        role=trajectory.role,
        owner=trajectory.owner,
        authorized_root_count=len(trajectory.authorized_roots),
        observation_count=len(trajectory.observations),
        complete=trajectory.complete,
        final_leader=trajectory.final_leader,
        leader_flips=leader_flip_count(leaders),
        stabilization_fraction=stabilization_frac,
        pv_persistence=pv_persistence(pvs),
        work_after_stability_ratio=waste,
        work_semantics=work_semantics,
        parse_errors=trajectory.parse_errors,
    )
