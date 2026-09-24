"""Decision-inert search-regime classification over sealed typed evidence.

M14-F is deliberately offline.  It describes search state; it does not route,
reserve resources, dispatch engines, or grant move authority.

The supported v1 regimes are multi-label hypotheses.  A position may therefore
be both CROSS_ENGINE_DISAGREEMENT and TACTICAL_RUPTURE.  Unsupported hypotheses
are reported explicitly instead of being guessed from unrelated proxies.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from adapters.crossfeed import (
    CrossFeedAdapterEvidence,
    build_adapter_evidence_from_run,
)
from common.residuals import past_only_features, pv_persistence
from controller.refinement import (
    RefinementError,
    load_refinement_manifest,
    verify_refinement_integrity,
)
from controller.replay import load_manifest, sha256_file
from controller.verification_analysis import (
    OWNER_ORDER,
    VerificationAnalysisError,
    derive_relock,
    load_verification_bundle,
)


REGIME_SCHEMA_VERSION = 1
REGIME_EXTRACTOR_VERSION = "search-regime-v1"


class RegimeError(RuntimeError):
    """Raised when a regime observation cannot be derived honestly."""


class SearchRegime(str, Enum):
    STABLE_CONVERGENT = "stable_convergent"
    TACTICAL_RUPTURE = "tactical_rupture"
    CROSS_ENGINE_DISAGREEMENT = "cross_engine_disagreement"
    POLICY_DIFFUSE = "policy_diffuse"
    ENDGAME_EXACT = "endgame_exact"
    TIME_CRITICAL = "time_critical"
    REFINEMENT_PRODUCTIVE = "refinement_productive"
    REFINEMENT_STALLED = "refinement_stalled"
    OUT_OF_DOMAIN = "out_of_domain"


REGIME_ORDER = tuple(SearchRegime)


class RegimeStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNSUPPORTED = "unsupported"
    OUT_OF_DOMAIN = "out_of_domain"


def _canonical_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite_fraction(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegimeError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise RegimeError(f"{label} must be finite and in [0,1]")
    return number


@dataclass(frozen=True)
class VerifierRegimeFeatures:
    owner: str
    terminal_move: str | None
    observation_count: int
    leader_flips: int
    stable_run_fraction: float
    pv_persistence: float | None

    def __post_init__(self) -> None:
        if self.owner not in OWNER_ORDER:
            raise RegimeError(f"unknown verifier owner: {self.owner!r}")
        if isinstance(self.observation_count, bool) or self.observation_count < 0:
            raise RegimeError("observation_count must be a non-negative integer")
        if isinstance(self.leader_flips, bool) or self.leader_flips < 0:
            raise RegimeError("leader_flips must be a non-negative integer")
        _finite_fraction(self.stable_run_fraction, "stable_run_fraction")
        if self.pv_persistence is not None:
            _finite_fraction(self.pv_persistence, "pv_persistence")

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "terminal_move": self.terminal_move,
            "observation_count": self.observation_count,
            "leader_flips": self.leader_flips,
            "stable_run_fraction": self.stable_run_fraction,
            "pv_persistence": self.pv_persistence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerifierRegimeFeatures":
        return cls(
            owner=str(payload["owner"]),
            terminal_move=(
                str(payload["terminal_move"])
                if isinstance(payload.get("terminal_move"), str)
                else None
            ),
            observation_count=int(payload["observation_count"]),
            leader_flips=int(payload["leader_flips"]),
            stable_run_fraction=float(payload["stable_run_fraction"]),
            pv_persistence=(
                float(payload["pv_persistence"])
                if isinstance(payload.get("pv_persistence"), (int, float))
                and not isinstance(payload.get("pv_persistence"), bool)
                else None
            ),
        )


@dataclass(frozen=True)
class RefinementRegimeFeatures:
    present: bool
    run_disposition: str | None
    max_depth: int | None
    max_expansions: int | None
    target_count: int
    completed_nonterminal_targets: int
    expansion_count: int
    completed_expansions: int
    terminal_expansions: int
    max_observed_depth: int
    boundary_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "run_disposition": self.run_disposition,
            "max_depth": self.max_depth,
            "max_expansions": self.max_expansions,
            "target_count": self.target_count,
            "completed_nonterminal_targets": self.completed_nonterminal_targets,
            "expansion_count": self.expansion_count,
            "completed_expansions": self.completed_expansions,
            "terminal_expansions": self.terminal_expansions,
            "max_observed_depth": self.max_observed_depth,
            "boundary_reasons": list(self.boundary_reasons),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefinementRegimeFeatures":
        return cls(
            present=bool(payload.get("present")),
            run_disposition=(
                str(payload["run_disposition"])
                if isinstance(payload.get("run_disposition"), str)
                else None
            ),
            max_depth=(
                int(payload["max_depth"])
                if isinstance(payload.get("max_depth"), int)
                and not isinstance(payload.get("max_depth"), bool)
                else None
            ),
            max_expansions=(
                int(payload["max_expansions"])
                if isinstance(payload.get("max_expansions"), int)
                and not isinstance(payload.get("max_expansions"), bool)
                else None
            ),
            target_count=int(payload.get("target_count", 0)),
            completed_nonterminal_targets=int(
                payload.get("completed_nonterminal_targets", 0)
            ),
            expansion_count=int(payload.get("expansion_count", 0)),
            completed_expansions=int(payload.get("completed_expansions", 0)),
            terminal_expansions=int(payload.get("terminal_expansions", 0)),
            max_observed_depth=int(payload.get("max_observed_depth", 0)),
            boundary_reasons=tuple(
                str(value) for value in payload.get("boundary_reasons") or ()
            ),
        )


@dataclass(frozen=True)
class TimingRegimeFeatures:
    request_mode: str
    limits: tuple[tuple[str, int | bool], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_mode": self.request_mode,
            "limits": [
                {"name": name, "value": value}
                for name, value in self.limits
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TimingRegimeFeatures":
        rows = []
        for item in payload.get("limits") or ():
            if not isinstance(item, dict):
                raise RegimeError("timing limit row must be an object")
            value = item.get("value")
            if isinstance(value, bool):
                parsed: int | bool = value
            elif isinstance(value, int):
                parsed = value
            else:
                raise RegimeError("timing limit value must be integer/bool")
            rows.append((str(item.get("name") or ""), parsed))
        return cls(request_mode=str(payload.get("request_mode") or "other"), limits=tuple(rows))


@dataclass(frozen=True)
class RegimeObservation:
    run_id: str
    generation: int
    position_id: str
    candidate_roots: tuple[str, ...]
    adapter_evidence_digest: str
    source_hashes: tuple[tuple[str, str], ...]
    verify_terminal_by_owner: tuple[tuple[str, str | None], ...]
    verify_pattern: str
    relock_status: str
    relock_fraction: float | None
    verifiers: tuple[VerifierRegimeFeatures, ...]
    native_mate_alarm_families: tuple[str, ...]
    refinement: RefinementRegimeFeatures
    timing: TimingRegimeFeatures

    def __post_init__(self) -> None:
        if not self.run_id or not self.position_id:
            raise RegimeError("run_id and position_id must be non-empty")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise RegimeError("generation must be an integer")
        if len(self.adapter_evidence_digest) != 64:
            raise RegimeError("adapter_evidence_digest must be SHA-256")
        if len({move for move in self.candidate_roots}) != len(self.candidate_roots):
            raise RegimeError("candidate roots must be duplicate-free")
        owners = tuple(owner for owner, _ in self.verify_terminal_by_owner)
        if owners != OWNER_ORDER:
            raise RegimeError("VERIFY terminal vector must use frozen owner order")
        verifier_owners = tuple(item.owner for item in self.verifiers)
        if verifier_owners != OWNER_ORDER:
            raise RegimeError("verifier features must use frozen owner order")
        if self.relock_fraction is not None:
            _finite_fraction(self.relock_fraction, "relock_fraction")

    @property
    def digest(self) -> str:
        return _canonical_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGIME_SCHEMA_VERSION,
            "extractor_version": REGIME_EXTRACTOR_VERSION,
            "run_id": self.run_id,
            "generation": self.generation,
            "position_id": self.position_id,
            "candidate_roots": list(self.candidate_roots),
            "adapter_evidence_digest": self.adapter_evidence_digest,
            "source_hashes": [
                {"name": name, "sha256": digest}
                for name, digest in self.source_hashes
            ],
            "verify_terminal_by_owner": [
                {"owner": owner, "move": move}
                for owner, move in self.verify_terminal_by_owner
            ],
            "verify_pattern": self.verify_pattern,
            "relock_status": self.relock_status,
            "relock_fraction": self.relock_fraction,
            "verifiers": [item.as_dict() for item in self.verifiers],
            "native_mate_alarm_families": list(self.native_mate_alarm_families),
            "refinement": self.refinement.as_dict(),
            "timing": self.timing.as_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RegimeObservation":
        if payload.get("schema_version") != REGIME_SCHEMA_VERSION:
            raise RegimeError("unsupported regime observation schema")
        if payload.get("extractor_version") != REGIME_EXTRACTOR_VERSION:
            raise RegimeError("unsupported regime observation extractor")
        return cls(
            run_id=str(payload["run_id"]),
            generation=int(payload["generation"]),
            position_id=str(payload["position_id"]),
            candidate_roots=tuple(str(move) for move in payload.get("candidate_roots") or ()),
            adapter_evidence_digest=str(payload["adapter_evidence_digest"]),
            source_hashes=tuple(
                (str(item["name"]), str(item["sha256"]))
                for item in payload.get("source_hashes") or ()
            ),
            verify_terminal_by_owner=tuple(
                (
                    str(item["owner"]),
                    str(item["move"]) if isinstance(item.get("move"), str) else None,
                )
                for item in payload.get("verify_terminal_by_owner") or ()
            ),
            verify_pattern=str(payload.get("verify_pattern") or "undefined"),
            relock_status=str(payload.get("relock_status") or "RELOCK_UNDEFINED"),
            relock_fraction=(
                float(payload["relock_fraction"])
                if isinstance(payload.get("relock_fraction"), (int, float))
                and not isinstance(payload.get("relock_fraction"), bool)
                else None
            ),
            verifiers=tuple(
                VerifierRegimeFeatures.from_dict(item)
                for item in payload.get("verifiers") or ()
            ),
            native_mate_alarm_families=tuple(
                str(value) for value in payload.get("native_mate_alarm_families") or ()
            ),
            refinement=RefinementRegimeFeatures.from_dict(
                dict(payload.get("refinement") or {})
            ),
            timing=TimingRegimeFeatures.from_dict(dict(payload.get("timing") or {})),
        )


@dataclass(frozen=True)
class RegimeDomainAssessment:
    bucket: str
    support: int
    position_group_support: int
    in_domain: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "support": self.support,
            "position_group_support": self.position_group_support,
            "in_domain": self.in_domain,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RegimeAssessment:
    regime: SearchRegime
    status: RegimeStatus
    reason: str
    evidence: tuple[tuple[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "status": self.status.value,
            "reason": self.reason,
            "evidence": {key: value for key, value in self.evidence},
        }


@dataclass(frozen=True)
class RegimeClassification:
    observation: RegimeObservation
    assessments: tuple[RegimeAssessment, ...]
    domain: RegimeDomainAssessment | None = None

    @property
    def digest(self) -> str:
        return _canonical_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGIME_SCHEMA_VERSION,
            "extractor_version": REGIME_EXTRACTOR_VERSION,
            "observation_digest": self.observation.digest,
            "observation": self.observation.as_dict(),
            "domain": None if self.domain is None else self.domain.as_dict(),
            "regimes": [item.as_dict() for item in self.assessments],
        }

    def status_for(self, regime: SearchRegime) -> RegimeStatus:
        return next(item.status for item in self.assessments if item.regime is regime)


def _terminal_pattern(terminals: tuple[tuple[str, str | None], ...]) -> str:
    values = [move for _, move in terminals]
    if any(move is None for move in values):
        return "undefined"
    unique = set(values)
    if len(unique) == 1:
        return "unanimous"
    if len(unique) == 2:
        return "two_one"
    return "all_different"


def _timing_features(parent: dict[str, Any]) -> TimingRegimeFeatures:
    request = ((parent.get("external_request") or {}).get("request") or {})
    limits = []
    for item in request.get("limits") or ():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str):
            continue
        if isinstance(value, bool) or isinstance(value, int):
            limits.append((name, value))
    names = {name for name, _ in limits}
    if "movetime" in names:
        mode = "movetime"
    elif names.intersection({"wtime", "btime", "winc", "binc", "movestogo"}):
        mode = "clock"
    elif "nodes" in names:
        mode = "nodes"
    elif names:
        mode = "other"
    else:
        mode = "unbounded_or_unknown"
    return TimingRegimeFeatures(request_mode=mode, limits=tuple(limits))


_BOUNDARY_MARKERS = (
    "depth cap",
    "expansion cap",
    "decision/cancellation boundary",
    "denied by active budget",
    "resource",
    "cancel",
    "anchor",
)


def _refinement_features(run_dir: Path) -> RefinementRegimeFeatures:
    path = run_dir / "refinement" / "manifest.json"
    if not path.is_file():
        return RefinementRegimeFeatures(
            present=False,
            run_disposition=None,
            max_depth=None,
            max_expansions=None,
            target_count=0,
            completed_nonterminal_targets=0,
            expansion_count=0,
            completed_expansions=0,
            terminal_expansions=0,
            max_observed_depth=0,
            boundary_reasons=(),
        )
    problems = verify_refinement_integrity(run_dir)
    if problems:
        raise RegimeError("REFINE integrity failed: " + "; ".join(problems))
    try:
        manifest = load_refinement_manifest(run_dir)
    except RefinementError as exc:
        raise RegimeError(str(exc)) from exc

    recursive = manifest.get("recursive_policy") or {}
    max_depth = recursive.get("max_depth")
    max_expansions = recursive.get("max_expansions")
    targets = [item for item in manifest.get("targets") or () if isinstance(item, dict)]
    expansions = [
        item for item in manifest.get("expansions") or () if isinstance(item, dict)
    ]

    completed_nonterminal_targets = 0
    for target in targets:
        disposition = target.get("disposition") or {}
        oracle = target.get("child_oracle") or {}
        if (
            disposition.get("target") == "completed"
            and oracle.get("terminal") is False
        ):
            completed_nonterminal_targets += 1

    completed_expansions = 0
    terminal_expansions = 0
    max_observed_depth = 0
    for expansion in expansions:
        disposition = expansion.get("disposition") or {}
        state = disposition.get("expansion")
        if state == "completed":
            completed_expansions += 1
        if state == "terminal":
            terminal_expansions += 1
        depth = expansion.get("depth")
        if isinstance(depth, int) and not isinstance(depth, bool):
            max_observed_depth = max(max_observed_depth, depth)

    raw_reasons: list[str] = []
    run_disposition = manifest.get("disposition") or {}
    if isinstance(run_disposition.get("stop_reason"), str):
        raw_reasons.append(run_disposition["stop_reason"])
    raw_reasons.extend(
        str(note) for note in manifest.get("notes") or () if isinstance(note, str)
    )
    for row in [*targets, *expansions]:
        disposition = row.get("disposition") or {}
        reason = disposition.get("stop_reason")
        if isinstance(reason, str):
            raw_reasons.append(reason)
    boundary_reasons = tuple(
        reason
        for reason in raw_reasons
        if any(marker in reason.lower() for marker in _BOUNDARY_MARKERS)
    )

    return RefinementRegimeFeatures(
        present=True,
        run_disposition=(
            str(run_disposition.get("run"))
            if isinstance(run_disposition.get("run"), str)
            else None
        ),
        max_depth=(
            int(max_depth)
            if isinstance(max_depth, int) and not isinstance(max_depth, bool)
            else None
        ),
        max_expansions=(
            int(max_expansions)
            if isinstance(max_expansions, int)
            and not isinstance(max_expansions, bool)
            else None
        ),
        target_count=len(targets),
        completed_nonterminal_targets=completed_nonterminal_targets,
        expansion_count=len(expansions),
        completed_expansions=completed_expansions,
        terminal_expansions=terminal_expansions,
        max_observed_depth=max_observed_depth,
        boundary_reasons=boundary_reasons,
    )


def _mate_alarm_families(evidence: CrossFeedAdapterEvidence) -> tuple[str, ...]:
    families: set[str] = set()
    for hint in evidence.hints:
        if hint.stage_disposition != "completed":
            continue
        if any(
            evaluation.kind == "mate"
            and evaluation.semantics.startswith(f"{hint.source_family}.")
            for evaluation in hint.evaluations
        ):
            families.add(hint.source_family)
    return tuple(owner for owner in OWNER_ORDER if owner in families)


def _source_hashes(run_dir: Path) -> tuple[tuple[str, str], ...]:
    paths = [
        ("parent_manifest", run_dir / "manifest.json"),
        ("verification_manifest", run_dir / "verification" / "manifest.json"),
        ("crossfeed_manifest", run_dir / "crossfeed" / "manifest.json"),
    ]
    refine = run_dir / "refinement" / "manifest.json"
    if refine.is_file():
        paths.append(("refinement_manifest", refine))
    missing = [name for name, path in paths if not path.is_file()]
    if missing:
        raise RegimeError(f"regime source is missing required artifact(s): {missing}")
    return tuple((name, sha256_file(path)) for name, path in paths)


def build_regime_observation_from_run(run_dir: Path | str) -> RegimeObservation:
    """Build one immutable observation from sealed, integrity-checked evidence."""

    run_dir = Path(run_dir)
    try:
        parent = load_manifest(run_dir)
        adapter_evidence = build_adapter_evidence_from_run(run_dir)
        verification = load_verification_bundle(run_dir)
    except (OSError, VerificationAnalysisError, Exception) as exc:
        # Cross-feed/refinement helpers expose their own typed runtime errors;
        # normalize them at this offline boundary without weakening the reason.
        if isinstance(exc, RegimeError):
            raise
        raise RegimeError(f"cannot build regime source evidence: {exc}") from exc

    if not verification.analysis_eligible:
        raise RegimeError(
            "M14-F v1 requires one complete three-way VERIFY bundle"
        )
    if adapter_evidence.evidence_faults:
        raise RegimeError(
            "adapter evidence is explicitly faulted: "
            + "; ".join(adapter_evidence.evidence_faults)
        )

    terminals = tuple(
        (
            owner,
            None
            if verification.by_owner(owner) is None
            else verification.by_owner(owner).final_leader,
        )
        for owner in OWNER_ORDER
    )
    verifier_features: list[VerifierRegimeFeatures] = []
    for owner in OWNER_ORDER:
        trajectory = verification.by_owner(owner)
        assert trajectory is not None
        history = past_only_features(
            trajectory.primary_moves_until(trajectory.span_ms)
        )
        pvs = [
            item.pv
            for item in trajectory.observations
            if item.multipv_index == 1 and item.pv
        ]
        verifier_features.append(
            VerifierRegimeFeatures(
                owner=owner,
                terminal_move=trajectory.final_leader,
                observation_count=history.observation_count,
                leader_flips=history.leader_flips,
                stable_run_fraction=history.stable_run_fraction,
                pv_persistence=pv_persistence(pvs),
            )
        )

    relock = derive_relock(verification)
    position = parent.get("position") or {}
    return RegimeObservation(
        run_id=str(parent.get("run_id") or ""),
        generation=int(parent.get("generation", 0)),
        position_id=str(position.get("position_id") or ""),
        candidate_roots=tuple(adapter_evidence.candidate_roots),
        adapter_evidence_digest=adapter_evidence.digest,
        source_hashes=_source_hashes(run_dir),
        verify_terminal_by_owner=terminals,
        verify_pattern=_terminal_pattern(terminals),
        relock_status=relock.status,
        relock_fraction=relock.relock_fraction,
        verifiers=tuple(verifier_features),
        native_mate_alarm_families=_mate_alarm_families(adapter_evidence),
        refinement=_refinement_features(run_dir),
        timing=_timing_features(parent),
    )


def _assessment(
    regime: SearchRegime,
    status: RegimeStatus,
    reason: str,
    **evidence: Any,
) -> RegimeAssessment:
    return RegimeAssessment(
        regime=regime,
        status=status,
        reason=reason,
        evidence=tuple(sorted(evidence.items())),
    )


def classify_regimes(
    observation: RegimeObservation,
    *,
    domain: RegimeDomainAssessment | None = None,
) -> RegimeClassification:
    """Return deterministic multi-label regime hypotheses.

    No assessment in this function grants work or move authority.
    """

    assessments: dict[SearchRegime, RegimeAssessment] = {}

    if observation.relock_status == "RELOCK_OBSERVED":
        assessments[SearchRegime.STABLE_CONVERGENT] = _assessment(
            SearchRegime.STABLE_CONVERGENT,
            RegimeStatus.ACTIVE,
            "completed three-way VERIFY has an observed terminal RELOCK",
            relock_fraction=observation.relock_fraction,
        )
    else:
        assessments[SearchRegime.STABLE_CONVERGENT] = _assessment(
            SearchRegime.STABLE_CONVERGENT,
            RegimeStatus.INACTIVE,
            "completed VERIFY does not satisfy the terminal RELOCK definition",
            relock_status=observation.relock_status,
        )

    if observation.native_mate_alarm_families:
        assessments[SearchRegime.TACTICAL_RUPTURE] = _assessment(
            SearchRegime.TACTICAL_RUPTURE,
            RegimeStatus.ACTIVE,
            "at least one completed source emitted a native mate-semantic alarm",
            families=list(observation.native_mate_alarm_families),
        )
    else:
        assessments[SearchRegime.TACTICAL_RUPTURE] = _assessment(
            SearchRegime.TACTICAL_RUPTURE,
            RegimeStatus.INACTIVE,
            "no completed source emitted a native mate-semantic alarm",
        )

    if observation.verify_pattern in ("two_one", "all_different"):
        distinct = len(
            {
                move
                for _, move in observation.verify_terminal_by_owner
                if move is not None
            }
        )
        assessments[SearchRegime.CROSS_ENGINE_DISAGREEMENT] = _assessment(
            SearchRegime.CROSS_ENGINE_DISAGREEMENT,
            RegimeStatus.ACTIVE,
            "completed VERIFY terminal vector contains more than one move",
            pattern=observation.verify_pattern,
            distinct_terminal_moves=distinct,
        )
    else:
        assessments[SearchRegime.CROSS_ENGINE_DISAGREEMENT] = _assessment(
            SearchRegime.CROSS_ENGINE_DISAGREEMENT,
            RegimeStatus.INACTIVE,
            "completed VERIFY terminal vector is unanimous",
            pattern=observation.verify_pattern,
        )

    assessments[SearchRegime.POLICY_DIFFUSE] = _assessment(
        SearchRegime.POLICY_DIFFUSE,
        RegimeStatus.UNSUPPORTED,
        "current typed evidence exposes ordinal ranks, not a qualified native policy distribution",
    )
    assessments[SearchRegime.ENDGAME_EXACT] = _assessment(
        SearchRegime.ENDGAME_EXACT,
        RegimeStatus.UNSUPPORTED,
        "current controller evidence contains no qualified tablebase-exactness fact",
    )
    assessments[SearchRegime.TIME_CRITICAL] = _assessment(
        SearchRegime.TIME_CRITICAL,
        RegimeStatus.UNSUPPORTED,
        "request timing facts are preserved, but no calibrated time-critical threshold exists",
        request_mode=observation.timing.request_mode,
    )

    refinement = observation.refinement
    if refinement.expansion_count > 0 and (
        refinement.completed_expansions + refinement.terminal_expansions > 0
    ):
        assessments[SearchRegime.REFINEMENT_PRODUCTIVE] = _assessment(
            SearchRegime.REFINEMENT_PRODUCTIVE,
            RegimeStatus.ACTIVE,
            "at least one clean recursive nomination instantiated a recorded expansion",
            expansion_count=refinement.expansion_count,
            max_observed_depth=refinement.max_observed_depth,
        )
    else:
        assessments[SearchRegime.REFINEMENT_PRODUCTIVE] = _assessment(
            SearchRegime.REFINEMENT_PRODUCTIVE,
            RegimeStatus.INACTIVE,
            "no completed/terminal recursive expansion was recorded",
            expansion_count=refinement.expansion_count,
        )

    stalled_supported = (
        refinement.present
        and refinement.max_depth is not None
        and refinement.max_depth > 2
        and refinement.run_disposition == "completed"
    )
    if not stalled_supported:
        assessments[SearchRegime.REFINEMENT_STALLED] = _assessment(
            SearchRegime.REFINEMENT_STALLED,
            RegimeStatus.UNSUPPORTED,
            "stall classification requires a completed recursive REFINE profile with max_depth > 2",
        )
    elif refinement.boundary_reasons:
        assessments[SearchRegime.REFINEMENT_STALLED] = _assessment(
            SearchRegime.REFINEMENT_STALLED,
            RegimeStatus.INACTIVE,
            "recursive work stopped at an explicit depth/cap/resource/decision boundary",
            boundary_reasons=list(refinement.boundary_reasons),
        )
    elif (
        refinement.completed_nonterminal_targets > 0
        and refinement.expansion_count == 0
    ):
        assessments[SearchRegime.REFINEMENT_STALLED] = _assessment(
            SearchRegime.REFINEMENT_STALLED,
            RegimeStatus.ACTIVE,
            "clean non-terminal REFINE targets produced no recursive expansion despite available depth",
            completed_nonterminal_targets=refinement.completed_nonterminal_targets,
        )
    else:
        assessments[SearchRegime.REFINEMENT_STALLED] = _assessment(
            SearchRegime.REFINEMENT_STALLED,
            RegimeStatus.INACTIVE,
            "available recursive evidence does not meet the narrow stall definition",
        )

    if domain is None:
        assessments[SearchRegime.OUT_OF_DOMAIN] = _assessment(
            SearchRegime.OUT_OF_DOMAIN,
            RegimeStatus.UNSUPPORTED,
            "no regime-support calibration was supplied",
        )
    elif domain.in_domain:
        assessments[SearchRegime.OUT_OF_DOMAIN] = _assessment(
            SearchRegime.OUT_OF_DOMAIN,
            RegimeStatus.INACTIVE,
            "regime-support calibration recognizes this structural bucket",
            bucket=domain.bucket,
            support=domain.support,
            position_group_support=domain.position_group_support,
        )
    else:
        assessments[SearchRegime.OUT_OF_DOMAIN] = _assessment(
            SearchRegime.OUT_OF_DOMAIN,
            RegimeStatus.OUT_OF_DOMAIN,
            domain.reason or "regime-support calibration rejected this bucket",
            bucket=domain.bucket,
            support=domain.support,
            position_group_support=domain.position_group_support,
        )

    return RegimeClassification(
        observation=observation,
        assessments=tuple(assessments[regime] for regime in REGIME_ORDER),
        domain=domain,
    )


def write_regime_classification(
    classification: RegimeClassification,
    output_dir: Path | str,
) -> Path:
    target = Path(output_dir) / f"regime-{classification.digest[:16]}"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "classification.json"
    payload = json.dumps(
        classification.as_dict(),
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != payload:
        raise RegimeError(f"classification address collision at {path}")
    path.write_text(payload, encoding="utf-8")
    return path
