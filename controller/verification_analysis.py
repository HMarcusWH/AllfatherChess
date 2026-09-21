"""Derived COMPARE / RELOCK analysis for explicit VERIFY evidence.

This module is deliberately offline. It reads the immutable parent replay plus
its raw `verification/` child artifact and derives scale-free structural
comparisons, EXPLORE->VERIFY preference changes, and a descriptive terminal
RELOCK state. It never starts an engine, mutates a raw bundle, changes routing,
or grants decision authority.

RELOCK discipline
------------------
`RELOCK_OBSERVED` means only that all three completed VERIFY trajectories end
on the same candidate and each trajectory entered a terminal uninterrupted
suffix on that candidate. It is not a correctness certificate.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from controller.replay import sha256_file, verify_bundle_integrity
from controller.replay_analysis import (
    DEFAULT_CHECKPOINT_FRACTIONS,
    ReplayBundle,
    SearchTrajectory,
    load_bundle,
    reconstruct_stream,
)
from controller.residuals import DEFAULT_TOP_K, PairwiseComparison, compare_at
from controller.verification import (
    VerificationError,
    load_verification_manifest,
    verify_verification_integrity,
)


ANALYSIS_SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "verification-analysis-v1"
RELOCK_DEFINITION = "terminal-suffix-v1"
OWNER_ORDER = ("stockfish", "reckless", "lc0")
PAIR_ORDER = (
    ("stockfish", "reckless"),
    ("stockfish", "lc0"),
    ("reckless", "lc0"),
)


class VerificationAnalysisError(RuntimeError):
    """Raised when VERIFY evidence cannot be analyzed without inventing facts."""


@dataclass
class VerificationBundle:
    """Parent EXPLORE evidence plus reconstructed VERIFY trajectories."""

    run_dir: Path
    parent: ReplayBundle
    manifest: dict[str, Any]
    trajectories: tuple[SearchTrajectory, ...]
    load_errors: tuple[str, ...] = ()

    @property
    def run_id(self) -> str:
        return self.parent.run_id

    @property
    def candidate_roots(self) -> tuple[str, ...]:
        return tuple(self.manifest["nomination"]["candidate_roots"])

    @property
    def nominees_by_owner(self) -> dict[str, str]:
        return {
            str(owner): str(move)
            for owner, move in self.manifest["nomination"]["nominees_by_owner"].items()
        }

    @property
    def completed(self) -> bool:
        return (self.manifest.get("disposition") or {}).get("run") == "completed"

    def by_owner(self, owner: str) -> SearchTrajectory | None:
        for trajectory in self.trajectories:
            if trajectory.owner == owner:
                return trajectory
        return None

    @property
    def analysis_eligible(self) -> bool:
        if not self.completed or self.load_errors:
            return False
        if len(self.trajectories) != 3:
            return False
        for owner in OWNER_ORDER:
            trajectory = self.by_owner(owner)
            if trajectory is None or not trajectory.complete or trajectory.parse_errors:
                return False
            if trajectory.final_leader not in self.candidate_roots:
                return False
        return True

    @property
    def common_active_window(self) -> tuple[float, float] | None:
        if not self.analysis_eligible:
            return None
        trajectories = [self.by_owner(owner) for owner in OWNER_ORDER]
        assert all(item is not None for item in trajectories)
        starts = [float(item.started_ms) for item in trajectories if item is not None]
        ends = [
            float(item.completed_ms if item.completed_ms is not None else item.span_ms)
            for item in trajectories
            if item is not None
        ]
        start = max(starts)
        end = min(ends)
        return (start, end) if end > start else None


@dataclass(frozen=True)
class ExploreVerifyTransition:
    owner: str
    explore_nominee: str
    verify_final_leader: str | None
    changed: bool | None
    selected_nominee_owner: str | None
    self_retained: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "explore_nominee": self.explore_nominee,
            "verify_final_leader": self.verify_final_leader,
            "changed": self.changed,
            "selected_nominee_owner": self.selected_nominee_owner,
            "self_retained": self.self_retained,
        }


@dataclass(frozen=True)
class TriadCheckpoint:
    checkpoint_ms: float
    checkpoint_fraction: float
    leaders: dict[str, str | None]
    pattern: str
    unanimous_move: str | None
    pairwise_agreement_count: int | None
    pairwise: tuple[PairwiseComparison, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_ms": self.checkpoint_ms,
            "checkpoint_fraction": self.checkpoint_fraction,
            "leaders": self.leaders,
            "pattern": self.pattern,
            "unanimous_move": self.unanimous_move,
            "pairwise_agreement_count": self.pairwise_agreement_count,
            "pairwise": [item.as_dict() for item in self.pairwise],
        }


@dataclass(frozen=True)
class RelockOutcome:
    status: str
    move: str | None
    relock_at_ms: float | None
    relock_fraction: float | None
    terminal_lock_start_by_owner: dict[str, float | None]
    post_lock_observed_ms_by_owner: dict[str, float | None]
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "move": self.move,
            "relock_at_ms": self.relock_at_ms,
            "relock_fraction": self.relock_fraction,
            "terminal_lock_start_by_owner": self.terminal_lock_start_by_owner,
            "post_lock_observed_ms_by_owner": self.post_lock_observed_ms_by_owner,
            "reason": self.reason,
        }


@dataclass
class VerificationAnalysisArtifact:
    analysis_id: str
    created_utc: str
    checkpoint_fractions: tuple[float, ...]
    top_k: int
    sources: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_id": self.analysis_id,
            "extractor_version": EXTRACTOR_VERSION,
            "relock_definition": RELOCK_DEFINITION,
            "created_utc": self.created_utc,
            "parameters": {
                "checkpoint_fractions": list(self.checkpoint_fractions),
                "top_k": self.top_k,
            },
            "sources": self.sources,
            "runs": self.runs,
        }


def _read_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_no}: malformed JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_no}: event must be an object")
            continue
        events.append(value)
    return events, errors


def _parent_explore_nominees(parent: ReplayBundle) -> dict[str, str]:
    nominees: dict[str, str] = {}
    for owner in OWNER_ORDER:
        trajectory = parent.by_owner(owner)
        if trajectory is None or not trajectory.complete or trajectory.final_leader is None:
            raise VerificationAnalysisError(
                f"parent replay has no completed EXPLORE nominee for {owner}"
            )
        nominees[owner] = trajectory.final_leader
    return nominees


def load_verification_bundle(run_dir: Path | str) -> VerificationBundle:
    """Load and provenance-check parent + VERIFY evidence before deriving meaning."""

    run_dir = Path(run_dir)
    parent_problems = verify_bundle_integrity(run_dir)
    if parent_problems:
        raise VerificationAnalysisError(
            f"parent replay integrity failed: {parent_problems}"
        )
    child_problems = verify_verification_integrity(run_dir)
    if child_problems:
        raise VerificationAnalysisError(
            f"verification integrity failed: {child_problems}"
        )

    try:
        manifest = load_verification_manifest(run_dir)
    except VerificationError as exc:
        raise VerificationAnalysisError(str(exc)) from exc
    parent = load_bundle(run_dir)

    child_nominees = {
        str(owner): str(move)
        for owner, move in (manifest.get("nomination") or {}).get(
            "nominees_by_owner", {}
        ).items()
    }
    parent_nominees = _parent_explore_nominees(parent)
    if child_nominees != parent_nominees:
        raise VerificationAnalysisError(
            "verification nominees disagree with completed parent EXPLORE bestmoves: "
            f"parent={parent_nominees}, child={child_nominees}"
        )

    candidates = tuple((manifest.get("nomination") or {}).get("candidate_roots", ()))
    if candidates != tuple(parent_nominees[owner] for owner in OWNER_ORDER):
        raise VerificationAnalysisError(
            "verification candidate order does not match owner-ordered EXPLORE nominees"
        )

    parent_engines = parent.manifest.get("engines", {})
    participants = manifest.get("participants") or {}
    trajectories: list[SearchTrajectory] = []
    load_errors: list[str] = []

    for record in manifest.get("streams", []):
        instance = str(record.get("instance"))
        owner = next(
            (name for name, participant in participants.items() if participant == instance),
            None,
        )
        if owner is None:
            raise VerificationAnalysisError(
                f"verification stream {instance!r} has no declared owner"
            )
        engine_record = parent_engines.get(instance) or {}
        family = str(record.get("engine") or engine_record.get("engine") or owner)
        role = str(record.get("role") or "shadow")
        path = run_dir / "verification" / str(record.get("path"))
        events, errors = _read_events(path)
        load_errors.extend(errors)
        reconstructed = reconstruct_stream(
            events,
            instance=instance,
            family=family,
            role=role,
            owner_roots={owner: candidates},
        )
        for trajectory in reconstructed:
            if trajectory.owner != owner:
                raise VerificationAnalysisError(
                    f"{instance}: reconstructed owner {trajectory.owner!r} "
                    f"does not match declared owner {owner!r}"
                )
            if trajectory.authorized_roots != candidates:
                raise VerificationAnalysisError(
                    f"{instance}: reconstructed roots {trajectory.authorized_roots} "
                    f"do not match VERIFY candidates {candidates}"
                )
        trajectories.extend(reconstructed)

    return VerificationBundle(
        run_dir=run_dir,
        parent=parent,
        manifest=manifest,
        trajectories=tuple(trajectories),
        load_errors=tuple(load_errors),
    )


def _triad_pattern(leaders: dict[str, str | None]) -> tuple[str, str | None, int | None]:
    values = [leaders.get(owner) for owner in OWNER_ORDER]
    if any(value is None for value in values):
        return "undefined", None, None
    unique = set(values)
    if len(unique) == 1:
        return "unanimous", values[0], 3
    if len(unique) == 2:
        return "two_one", None, 1
    return "all_different", None, 0


def _pairwise(bundle: VerificationBundle, checkpoint_ms: float, top_k: int) -> tuple[PairwiseComparison, ...]:
    out: list[PairwiseComparison] = []
    for left_owner, right_owner in PAIR_ORDER:
        left = bundle.by_owner(left_owner)
        right = bundle.by_owner(right_owner)
        if left is None or right is None:
            continue
        comparison = compare_at(left, right, checkpoint_ms, top_k=top_k)
        if comparison.support != 3:
            raise VerificationAnalysisError(
                f"VERIFY pair {left_owner}<->{right_owner} has support "
                f"{comparison.support}, expected 3"
            )
        out.append(comparison)
    return tuple(out)


def _checkpoints(
    bundle: VerificationBundle,
    fractions: Sequence[float],
    top_k: int,
) -> tuple[TriadCheckpoint, ...]:
    window = bundle.common_active_window
    if window is None:
        return ()
    start, end = window
    span = end - start
    checkpoints: list[TriadCheckpoint] = []
    for fraction in fractions:
        if not 0.0 < float(fraction) <= 1.0:
            raise VerificationAnalysisError(
                f"checkpoint fraction must be in (0,1], got {fraction!r}"
            )
        point = start + float(fraction) * span
        leaders = {
            owner: (
                None
                if bundle.by_owner(owner) is None
                else bundle.by_owner(owner).leader_at(point)
            )
            for owner in OWNER_ORDER
        }
        pattern, unanimous, agreement_count = _triad_pattern(leaders)
        checkpoints.append(
            TriadCheckpoint(
                checkpoint_ms=round(point, 6),
                checkpoint_fraction=float(fraction),
                leaders=leaders,
                pattern=pattern,
                unanimous_move=unanimous,
                pairwise_agreement_count=agreement_count,
                pairwise=_pairwise(bundle, point, top_k),
            )
        )
    return tuple(checkpoints)


def _terminal_lock_start(trajectory: SearchTrajectory) -> tuple[float | None, float | None]:
    """Earliest time of the final uninterrupted suffix on the final leader."""

    if not trajectory.complete or trajectory.final_leader is None:
        return None, None
    end = float(
        trajectory.completed_ms
        if trajectory.completed_ms is not None
        else trajectory.span_ms
    )
    final = trajectory.final_leader
    primary = [
        (float(item.observed_ms), item.move)
        for item in trajectory.observations
        if item.multipv_index == 1
    ]
    if not primary:
        return end, 0.0

    # If bestmove changed after the last primary observation, the terminal lock
    # is only observed at completion.
    if primary[-1][1] != final:
        return end, 0.0

    suffix_start = primary[-1][0]
    for observed_ms, move in reversed(primary[:-1]):
        if move != final:
            break
        suffix_start = observed_ms
    return suffix_start, max(0.0, end - suffix_start)


def derive_relock(bundle: VerificationBundle) -> RelockOutcome:
    empty_starts = {owner: None for owner in OWNER_ORDER}
    empty_post = {owner: None for owner in OWNER_ORDER}
    if not bundle.analysis_eligible:
        return RelockOutcome(
            status="RELOCK_UNDEFINED",
            move=None,
            relock_at_ms=None,
            relock_fraction=None,
            terminal_lock_start_by_owner=empty_starts,
            post_lock_observed_ms_by_owner=empty_post,
            reason="verification evidence is incomplete or not three-way analysis eligible",
        )

    trajectories = {owner: bundle.by_owner(owner) for owner in OWNER_ORDER}
    final_leaders = {
        owner: trajectories[owner].final_leader if trajectories[owner] else None
        for owner in OWNER_ORDER
    }
    pattern, unanimous, _ = _triad_pattern(final_leaders)
    starts: dict[str, float | None] = {}
    post: dict[str, float | None] = {}
    for owner in OWNER_ORDER:
        trajectory = trajectories[owner]
        assert trajectory is not None
        starts[owner], post[owner] = _terminal_lock_start(trajectory)

    if pattern != "unanimous" or unanimous is None:
        return RelockOutcome(
            status="RELOCK_FAILED",
            move=None,
            relock_at_ms=None,
            relock_fraction=None,
            terminal_lock_start_by_owner=starts,
            post_lock_observed_ms_by_owner=post,
            reason="completed VERIFY final leaders are not unanimous",
        )
    if any(starts[owner] is None for owner in OWNER_ORDER):
        return RelockOutcome(
            status="RELOCK_UNDEFINED",
            move=unanimous,
            relock_at_ms=None,
            relock_fraction=None,
            terminal_lock_start_by_owner=starts,
            post_lock_observed_ms_by_owner=post,
            reason="terminal lock start is undefined for at least one verifier",
        )

    relock_at = max(float(starts[owner]) for owner in OWNER_ORDER)
    analysis_start = max(
        float(trajectories[owner].started_ms) for owner in OWNER_ORDER  # type: ignore[union-attr]
    )
    analysis_end = max(
        float(
            trajectories[owner].completed_ms
            if trajectories[owner].completed_ms is not None
            else trajectories[owner].span_ms
        )
        for owner in OWNER_ORDER
    )
    denominator = analysis_end - analysis_start
    fraction = None if denominator <= 0 else (relock_at - analysis_start) / denominator
    return RelockOutcome(
        status="RELOCK_OBSERVED",
        move=unanimous,
        relock_at_ms=round(relock_at, 6),
        relock_fraction=None if fraction is None else round(fraction, 9),
        terminal_lock_start_by_owner=starts,
        post_lock_observed_ms_by_owner=post,
        reason=None,
    )


def _transitions(bundle: VerificationBundle) -> tuple[ExploreVerifyTransition, ...]:
    candidate_owner = {
        move: owner for owner, move in bundle.nominees_by_owner.items()
    }
    rows: list[ExploreVerifyTransition] = []
    for owner in OWNER_ORDER:
        explore = bundle.nominees_by_owner[owner]
        trajectory = bundle.by_owner(owner)
        final = None if trajectory is None or not trajectory.complete else trajectory.final_leader
        rows.append(
            ExploreVerifyTransition(
                owner=owner,
                explore_nominee=explore,
                verify_final_leader=final,
                changed=None if final is None else final != explore,
                selected_nominee_owner=None if final is None else candidate_owner.get(final),
                self_retained=None if final is None else final == explore,
            )
        )
    return tuple(rows)


def analyze_verification_bundle(
    bundle: VerificationBundle,
    *,
    fractions: Sequence[float] = DEFAULT_CHECKPOINT_FRACTIONS,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    checkpoints = _checkpoints(bundle, fractions, top_k)
    final_leaders = {
        owner: (
            None
            if bundle.by_owner(owner) is None or not bundle.by_owner(owner).complete
            else bundle.by_owner(owner).final_leader
        )
        for owner in OWNER_ORDER
    }
    if bundle.analysis_eligible:
        final_pattern, final_unanimous, _ = _triad_pattern(final_leaders)
    else:
        final_pattern, final_unanimous = "undefined", None
    final_time = max(
        (
            trajectory.completed_ms
            if trajectory.completed_ms is not None
            else trajectory.span_ms
        )
        for trajectory in bundle.trajectories
    ) if bundle.trajectories else 0.0
    final_pairwise = (
        _pairwise(bundle, float(final_time), top_k)
        if bundle.analysis_eligible
        else ()
    )
    relock = derive_relock(bundle)

    anchor = bundle.parent.anchor
    anchor_final = None if anchor is None else anchor.final_leader
    matches_anchor = sum(
        1
        for move in final_leaders.values()
        if anchor_final is not None and move == anchor_final
    )
    source_owner = (
        None
        if final_unanimous is None
        else next(
            (
                owner
                for owner, move in bundle.nominees_by_owner.items()
                if move == final_unanimous
            ),
            None,
        )
    )

    window = bundle.common_active_window
    return {
        "run_id": bundle.run_id,
        "analysis_eligible": bundle.analysis_eligible,
        "eligibility_reason": (
            None
            if bundle.analysis_eligible
            else "verification incomplete, malformed, or missing a complete three-way trajectory"
        ),
        "candidate_roots": list(bundle.candidate_roots),
        "explore_nominees": dict(bundle.nominees_by_owner),
        "common_active_window": (
            None
            if window is None
            else {"start_ms": window[0], "end_ms": window[1]}
        ),
        "checkpoints": [item.as_dict() for item in checkpoints],
        "final_pairwise": [item.as_dict() for item in final_pairwise],
        "final_leaders": final_leaders,
        "final_pattern": final_pattern,
        "final_unanimous_move": final_unanimous,
        "unanimous_source_owner": source_owner,
        "explore_to_verify": [item.as_dict() for item in _transitions(bundle)],
        "relock": relock.as_dict(),
        "anchor_relation": {
            "anchor_final_move": anchor_final,
            "anchor_in_verify_candidate_set": (
                None if anchor_final is None else anchor_final in bundle.candidate_roots
            ),
            "verifiers_matching_anchor_final": (
                None if anchor_final is None else matches_anchor
            ),
            "unanimous_matches_anchor": (
                None
                if anchor_final is None or final_unanimous is None
                else final_unanimous == anchor_final
            ),
        },
        "load_errors": list(bundle.load_errors),
    }


def _source_record(run_dir: Path, bundle: VerificationBundle) -> dict[str, Any]:
    child = bundle.manifest
    return {
        "run_id": bundle.run_id,
        "run_dir": run_dir.name,
        "parent_manifest_sha256": sha256_file(run_dir / "manifest.json"),
        "parent_streams": {
            record["instance"]: record["sha256"]
            for record in bundle.parent.manifest.get("streams", [])
        },
        "verification_manifest_sha256": sha256_file(
            run_dir / "verification" / "manifest.json"
        ),
        "verification_streams": {
            record["instance"]: record["sha256"]
            for record in child.get("streams", [])
        },
    }


def build_verification_analysis_artifact(
    run_dirs: Sequence[Path],
    *,
    fractions: Sequence[float] = DEFAULT_CHECKPOINT_FRACTIONS,
    top_k: int = DEFAULT_TOP_K,
    analysis_id: str | None = None,
    now: _dt.datetime | None = None,
) -> VerificationAnalysisArtifact:
    if not run_dirs:
        raise VerificationAnalysisError(
            "at least one replay run with a verification child is required"
        )
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    sources: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()

    for run_dir in sorted(Path(item) for item in run_dirs):
        bundle = load_verification_bundle(run_dir)
        if bundle.run_id in seen_run_ids:
            raise VerificationAnalysisError(
                f"refusing duplicate verification evidence for run_id {bundle.run_id!r}"
            )
        seen_run_ids.add(bundle.run_id)
        sources.append(_source_record(run_dir, bundle))
        runs.append(
            analyze_verification_bundle(bundle, fractions=fractions, top_k=top_k)
        )

    if analysis_id is None:
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                {
                    "schema_version": ANALYSIS_SCHEMA_VERSION,
                    "extractor_version": EXTRACTOR_VERSION,
                    "relock_definition": RELOCK_DEFINITION,
                    "checkpoint_fractions": list(fractions),
                    "top_k": top_k,
                    "sources": [
                        {
                            "run_id": record["run_id"],
                            "parent_manifest_sha256": record["parent_manifest_sha256"],
                            "parent_streams": record["parent_streams"],
                            "verification_manifest_sha256": record[
                                "verification_manifest_sha256"
                            ],
                            "verification_streams": record["verification_streams"],
                        }
                        for record in sources
                    ],
                },
                sort_keys=True,
            ).encode("utf-8")
        )
        analysis_id = f"verification-derived-{digest.hexdigest()[:16]}"

    return VerificationAnalysisArtifact(
        analysis_id=analysis_id,
        created_utc=moment.isoformat().replace("+00:00", "Z"),
        checkpoint_fractions=tuple(float(value) for value in fractions),
        top_k=int(top_k),
        sources=sources,
        runs=runs,
    )


def write_verification_analysis_artifact(
    artifact: VerificationAnalysisArtifact,
    derived_root: Path | str,
) -> Path:
    target = Path(derived_root) / artifact.analysis_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "analysis.json"
    payload = artifact.as_dict()
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        comparable_existing = dict(existing)
        comparable_new = dict(payload)
        comparable_existing.pop("created_utc", None)
        comparable_new.pop("created_utc", None)
        if comparable_existing != comparable_new:
            raise VerificationAnalysisError(
                f"analysis_id collision at {path}: existing contents differ"
            )
        return path
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_verification_analysis_artifact(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "analysis.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise VerificationAnalysisError(
            f"unsupported verification analysis schema_version: {data.get('schema_version')!r}"
        )
    if data.get("extractor_version") != EXTRACTOR_VERSION:
        raise VerificationAnalysisError(
            f"foreign verification extractor: {data.get('extractor_version')!r}"
        )
    if data.get("relock_definition") != RELOCK_DEFINITION:
        raise VerificationAnalysisError(
            f"foreign RELOCK definition: {data.get('relock_definition')!r}"
        )
    return data
