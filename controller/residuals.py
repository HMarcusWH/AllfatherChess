"""Derived residual features extracted from raw replay bundles.

Layering
--------
```text
raw telemetry -> replay reconstruction -> residual features -> calibration -> policy
```
This module owns the third step only. It reads bundles, writes derived
artifacts, and consults no policy.

Support discipline
------------------
Under the shadow-mode observation partition the three shadow workers own
*disjoint* root regions, so a direct Stockfish-vs-Reckless leader comparison has
**empty shared support** inside a single run. Rather than fabricate a comparison,
every cross-engine feature carries its shared-support size and reports `None`
with an explicit reason when the support is empty. The comparisons that do have
support in v1 are each shadow worker against the unrestricted anchor over that
worker's own region.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from common.residuals import (
    jaccard,
    leader_agreement,
    pv_divergence,
    rank_agreement,
    top_k_overlap,
)
from controller.replay import sha256_file, verify_bundle_integrity
from controller.replay_analysis import (
    DEFAULT_CHECKPOINT_FRACTIONS,
    ReplayBundle,
    SearchTrajectory,
    counterfactual_labels,
    load_bundle,
    summarize_trajectory,
)


FEATURES_SCHEMA_VERSION = 1

#: Bump when a feature's definition changes, so calibrated models cannot be
#: silently applied to features that no longer mean the same thing.
#: v2: truncated reversal horizons are unlabelled rather than negative, each
#: checkpoint carries the shared past-only feature vector, and per-stage
#: evidence is keyed by search id so a multi-stage worker cannot overwrite
#: itself.
EXTRACTOR_VERSION = "residuals-v2"

DEFAULT_TOP_K = 3


class FeatureExtractionError(RuntimeError):
    """Raised when features cannot be extracted deterministically."""


@dataclass(frozen=True)
class PairwiseComparison:
    """One cross-engine comparison with its shared support made explicit."""

    left: str
    right: str
    support: int
    leader_agree: bool | None = None
    top_k_overlap: float | None = None
    rank_agreement: float | None = None
    pv_divergence: float | None = None
    candidate_jaccard: float | None = None
    undefined_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "support": self.support,
            "leader_agree": self.leader_agree,
            "top_k_overlap": self.top_k_overlap,
            "rank_agreement": self.rank_agreement,
            "pv_divergence": self.pv_divergence,
            "candidate_jaccard": self.candidate_jaccard,
            "undefined_reason": self.undefined_reason,
        }


def _authorized(trajectory: SearchTrajectory) -> frozenset[str] | None:
    """`None` means unrestricted (the anchor), which shares support with anyone."""
    if trajectory.role == "anchor" or not trajectory.authorized_roots:
        return None
    return frozenset(trajectory.authorized_roots)


def shared_support(left: SearchTrajectory, right: SearchTrajectory) -> frozenset[str] | None:
    """Root moves both searches were authorized to explore.

    `None` means both sides were unrestricted, so the support is the whole
    legal-root universe.
    """
    a, b = _authorized(left), _authorized(right)
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a & b


def compare_at(
    left: SearchTrajectory,
    right: SearchTrajectory,
    checkpoint_ms: float,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> PairwiseComparison:
    """Scale-free structural comparison restricted to the shared support."""
    support = shared_support(left, right)
    support_size = -1 if support is None else len(support)

    if support is not None and not support:
        return PairwiseComparison(
            left=left.instance,
            right=right.instance,
            support=0,
            undefined_reason=(
                "disjoint exploration ownership: these workers were never authorized "
                "to search a common root, so no direct comparison is defined"
            ),
        )

    def restrict(moves: Sequence[str]) -> tuple[str, ...]:
        if support is None:
            return tuple(moves)
        return tuple(move for move in moves if move in support)

    left_rank = restrict(left.ranking_at(checkpoint_ms))
    right_rank = restrict(right.ranking_at(checkpoint_ms))
    left_leader = left_rank[0] if left_rank else None
    right_leader = right_rank[0] if right_rank else None

    left_pv = left.pv_at(checkpoint_ms)
    right_pv = right.pv_at(checkpoint_ms)
    if support is not None:
        if left_pv and left_pv[0] not in support:
            left_pv = ()
        if right_pv and right_pv[0] not in support:
            right_pv = ()

    reason: str | None = None
    if left_leader is None or right_leader is None:
        reason = "at least one side reported no candidate inside the shared support yet"

    return PairwiseComparison(
        left=left.instance,
        right=right.instance,
        support=support_size,
        leader_agree=leader_agreement(left_leader, right_leader),
        top_k_overlap=top_k_overlap(left_rank, right_rank, top_k),
        rank_agreement=rank_agreement(left_rank, right_rank),
        pv_divergence=pv_divergence(left_pv, right_pv),
        candidate_jaccard=jaccard(left_rank, right_rank),
        undefined_reason=reason,
    )


@dataclass(frozen=True)
class CheckpointFeatures:
    """All derived features for one run at one controller-clock checkpoint."""

    checkpoint_ms: float
    checkpoint_fraction: float
    anchor_leader: str | None
    per_owner: dict[str, dict[str, Any]]
    intra_alpha_beta: list[PairwiseComparison]
    cross_paradigm: list[PairwiseComparison]
    anchor_vs_owner: list[PairwiseComparison]

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_ms": self.checkpoint_ms,
            "checkpoint_fraction": self.checkpoint_fraction,
            "anchor_leader": self.anchor_leader,
            "per_owner": self.per_owner,
            "intra_alpha_beta": [item.as_dict() for item in self.intra_alpha_beta],
            "cross_paradigm": [item.as_dict() for item in self.cross_paradigm],
            "anchor_vs_owner": [item.as_dict() for item in self.anchor_vs_owner],
        }


ALPHA_BETA_FAMILIES = ("stockfish", "reckless")


def extract_features(
    bundle: ReplayBundle,
    *,
    fractions: Sequence[float] = DEFAULT_CHECKPOINT_FRACTIONS,
    top_k: int = DEFAULT_TOP_K,
    horizon_fraction: float = 0.25,
) -> dict[str, Any]:
    """Deterministically derive the residual geometry of one replay bundle."""
    checkpoints = bundle.checkpoints(fractions)
    anchor = bundle.anchor
    shadows = bundle.shadows()

    # Keyed by search id: active routing can dispatch several stages to one
    # instance, and keying by instance would let a later stage overwrite every
    # earlier stage's evidence.
    per_stage_summary = {
        trajectory.search_id: summarize_trajectory(trajectory).as_dict()
        for trajectory in bundle.trajectories
    }
    per_stage_labels = {
        trajectory.search_id: [
            label.as_dict()
            for label in counterfactual_labels(
                trajectory,
                checkpoints,
                fractions=fractions,
                horizon_fraction=horizon_fraction,
            )
        ]
        for trajectory in bundle.trajectories
    }
    latest_by_instance: dict[str, str] = {}
    for trajectory in bundle.trajectories:
        latest_by_instance[trajectory.instance] = trajectory.search_id
    summaries_by_instance = {
        instance: per_stage_summary[search_id]
        for instance, search_id in sorted(latest_by_instance.items())
    }
    labels_by_instance = {
        instance: per_stage_labels[search_id]
        for instance, search_id in sorted(latest_by_instance.items())
    }

    checkpoint_rows: list[CheckpointFeatures] = []
    for point, fraction in zip(checkpoints, fractions):
        per_owner: dict[str, dict[str, Any]] = {}
        for trajectory in shadows:
            owner = trajectory.owner or trajectory.family
            margin = trajectory.within_engine_margin_at(point)
            ranking = trajectory.ranking_at(point)
            per_owner[owner] = {
                "instance": trajectory.instance,
                "engine": trajectory.family,
                "leader": trajectory.leader_at(point),
                "ranked_moves": list(ranking),
                "authorized_root_count": len(trajectory.authorized_roots),
                "observation_count": len(trajectory.observations_until(point)),
                # A within-engine margin is meaningless outside its own scale,
                # so it is always carried together with its semantics tag.
                "within_engine_margin": None if margin is None else margin.value,
                "within_engine_margin_semantics": None if margin is None else margin.semantics,
                "work": None if trajectory.work_at(point) is None else trajectory.work_at(point)[0],
                "work_semantics": (
                    None if trajectory.work_at(point) is None else trajectory.work_at(point)[1]
                ),
            }

        intra: list[PairwiseComparison] = []
        cross: list[PairwiseComparison] = []
        for index, left in enumerate(shadows):
            for right in shadows[index + 1 :]:
                comparison = compare_at(left, right, point, top_k=top_k)
                pair = {left.family, right.family}
                if pair <= set(ALPHA_BETA_FAMILIES):
                    intra.append(comparison)
                else:
                    cross.append(comparison)

        anchor_pairs: list[PairwiseComparison] = []
        if anchor is not None:
            for trajectory in shadows:
                anchor_pairs.append(compare_at(anchor, trajectory, point, top_k=top_k))

        checkpoint_rows.append(
            CheckpointFeatures(
                checkpoint_ms=point,
                checkpoint_fraction=fraction,
                anchor_leader=None if anchor is None else anchor.leader_at(point),
                per_owner=per_owner,
                intra_alpha_beta=intra,
                cross_paradigm=cross,
                anchor_vs_owner=anchor_pairs,
            )
        )

    return {
        "run_id": bundle.run_id,
        "variant": bundle.variant,
        "terminal": bundle.terminal,
        "span_ms": bundle.span_ms,
        "disposition": bundle.manifest["disposition"]["run"],
        "owner_roots": {owner: list(moves) for owner, moves in bundle.owner_roots.items()},
        "missing_streams": list(bundle.missing_streams),
        "load_errors": list(bundle.load_errors),
        "anchor_instance": None if anchor is None else anchor.instance,
        "anchor_bestmove": None if anchor is None else anchor.final_leader,
        "summaries": per_stage_summary,
        "summaries_by_instance": summaries_by_instance,
        "counterfactual_labels": per_stage_labels,
        "counterfactual_labels_by_instance": labels_by_instance,
        "checkpoints": [row.as_dict() for row in checkpoint_rows],
        "first_discoverer_of_anchor_move": first_discoverer(bundle),
    }


def first_discoverer(bundle: ReplayBundle) -> dict[str, Any]:
    """Which engine's stream first showed the anchor's final move as its leader.

    Only engines authorized to search that root can qualify, so the result
    records the eligible set explicitly rather than implying a race everyone ran.
    """
    anchor = bundle.anchor
    if anchor is None or anchor.final_leader is None:
        return {"move": None, "instance": None, "observed_ms": None, "eligible": []}
    move = anchor.final_leader
    eligible: list[str] = []
    best: tuple[float, str] | None = None
    for trajectory in bundle.trajectories:
        authorized = _authorized(trajectory)
        if authorized is not None and move not in authorized:
            continue
        eligible.append(trajectory.instance)
        for item in trajectory.observations:
            if item.multipv_index == 1 and item.move == move:
                if best is None or item.observed_ms < best[0]:
                    best = (item.observed_ms, trajectory.instance)
                break
    return {
        "move": move,
        "instance": None if best is None else best[1],
        "observed_ms": None if best is None else best[0],
        "eligible": sorted(eligible),
    }


@dataclass
class DerivedArtifact:
    """A versioned derived-feature artifact traceable to exact raw inputs."""

    derived_id: str
    created_utc: str
    extractor_version: str
    fractions: tuple[float, ...]
    top_k: int
    horizon_fraction: float
    sources: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FEATURES_SCHEMA_VERSION,
            "derived_id": self.derived_id,
            "created_utc": self.created_utc,
            "extractor_version": self.extractor_version,
            "parameters": {
                "checkpoint_fractions": list(self.fractions),
                "top_k": self.top_k,
                "horizon_fraction": self.horizon_fraction,
            },
            "sources": self.sources,
            "runs": self.runs,
        }


def _source_record(run_dir: Path, bundle: ReplayBundle) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    return {
        "run_id": bundle.run_id,
        "run_dir": run_dir.name,
        "manifest_sha256": sha256_file(manifest_path),
        "streams": {
            record["instance"]: record["sha256"] for record in bundle.manifest.get("streams", [])
        },
    }


def build_derived_artifact(
    run_dirs: Sequence[Path],
    *,
    fractions: Sequence[float] = DEFAULT_CHECKPOINT_FRACTIONS,
    top_k: int = DEFAULT_TOP_K,
    horizon_fraction: float = 0.25,
    derived_id: str | None = None,
    now: _dt.datetime | None = None,
) -> DerivedArtifact:
    """Extract features from raw bundles into one traceable derived artifact."""
    if not run_dirs:
        raise FeatureExtractionError("at least one replay run is required")
    moment = now or _dt.datetime.now(_dt.timezone.utc)

    sources: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for run_dir in sorted(Path(item) for item in run_dirs):
        # Provenance is only meaningful if the bytes still match the manifest
        # the provenance points at. Extracting first and recording the declared
        # hashes afterwards would let altered evidence masquerade as original.
        problems = verify_bundle_integrity(run_dir)
        if problems:
            raise FeatureExtractionError(
                f"refusing to derive features from {run_dir.name}: "
                f"bundle integrity failed: {problems}"
            )
        bundle = load_bundle(run_dir)
        sources.append(_source_record(run_dir, bundle))
        runs.append(
            extract_features(
                bundle,
                fractions=fractions,
                top_k=top_k,
                horizon_fraction=horizon_fraction,
            )
        )

    if derived_id is None:
        # Every input that can change the artifact's contents must enter its
        # content address, or two different artifacts collide on one directory.
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                {
                    "extractor_version": EXTRACTOR_VERSION,
                    "schema_version": FEATURES_SCHEMA_VERSION,
                    "checkpoint_fractions": list(fractions),
                    "top_k": top_k,
                    "horizon_fraction": horizon_fraction,
                    "sources": [record["manifest_sha256"] for record in sources],
                    "streams": [record["streams"] for record in sources],
                },
                sort_keys=True,
            ).encode("utf-8")
        )
        derived_id = f"derived-{digest.hexdigest()[:16]}"

    return DerivedArtifact(
        derived_id=derived_id,
        created_utc=moment.isoformat().replace("+00:00", "Z"),
        extractor_version=EXTRACTOR_VERSION,
        fractions=tuple(fractions),
        top_k=top_k,
        horizon_fraction=horizon_fraction,
        sources=sources,
        runs=runs,
    )


def write_derived_artifact(artifact: DerivedArtifact, derived_root: Path) -> Path:
    """Write the derived artifact beside, never inside, the raw bundles."""
    target = Path(derived_root) / artifact.derived_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "features.json"
    path.write_text(
        json.dumps(artifact.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def load_derived_artifact(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "features.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != FEATURES_SCHEMA_VERSION:
        raise FeatureExtractionError(f"unsupported derived schema_version: {version!r}")
    if data.get("extractor_version") != EXTRACTOR_VERSION:
        raise FeatureExtractionError(
            f"derived artifact was produced by {data.get('extractor_version')!r}, "
            f"this build extracts {EXTRACTOR_VERSION!r}"
        )
    return data
