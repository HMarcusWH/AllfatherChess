"""Calibration for prospective VERIFY decision-change probability.

This is a separate model family from controller.calibration's reversal-risk
model.  It estimates one descriptive intervention outcome:

    P(additional VERIFY budget changes the frozen counterfactual decision
      | lower-budget past-only evidence)

It does not estimate move correctness, Elo gain, or whether buying the compute
is strategically optimal.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from controller.value_of_compute import (
    VALUE_EXTRACTOR_VERSION,
    ValueOfComputeError,
    load_dataset,
)


DECISION_CALIBRATION_SCHEMA_VERSION = 1
MODEL_KIND = "bucketed_verify_decision_change_v1"
FEATURE_SCHEMA = (
    "transition",
    "proposal_disposition",
    "min_observation_count",
    "max_leader_flips",
    "min_stable_run_fraction",
)


class DecisionCalibrationError(RuntimeError):
    """Raised when decision-change calibration cannot be fit or served honestly."""


@dataclass(frozen=True)
class DecisionChangeRow:
    position_group: str
    replicate: int
    transition: str
    proposal_disposition: str
    min_observation_count: int
    max_leader_flips: int
    min_stable_run_fraction: float
    label: bool
    feature_digest: str
    label_digest: str

    def __post_init__(self) -> None:
        if not self.position_group:
            raise DecisionCalibrationError("position_group must be non-empty")
        if isinstance(self.replicate, bool) or not isinstance(self.replicate, int) or self.replicate < 0:
            raise DecisionCalibrationError("replicate must be a non-negative integer")
        if not self.transition or "->" not in self.transition:
            raise DecisionCalibrationError("transition must be an explicit lower->upper key")
        if not self.proposal_disposition:
            raise DecisionCalibrationError("proposal disposition must be non-empty")
        if self.min_observation_count < 0 or self.max_leader_flips < 0:
            raise DecisionCalibrationError("summary counts must be non-negative")
        if not 0.0 <= float(self.min_stable_run_fraction) <= 1.0:
            raise DecisionCalibrationError("min_stable_run_fraction must be in [0,1]")
        if len(self.feature_digest) != 64 or len(self.label_digest) != 64:
            raise DecisionCalibrationError("row digests must be SHA-256")

    def features(self) -> dict[str, Any]:
        return {
            "transition": self.transition,
            "proposal_disposition": self.proposal_disposition,
            "min_observation_count": self.min_observation_count,
            "max_leader_flips": self.max_leader_flips,
            "min_stable_run_fraction": self.min_stable_run_fraction,
        }

    def bucket(self) -> str:
        return bucket_key(self.features())


@dataclass(frozen=True)
class ValueOfComputeEstimate:
    change_probability: float
    support: int
    in_domain: bool
    bucket: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_probability": self.change_probability,
            "support": self.support,
            "in_domain": self.in_domain,
            "bucket": self.bucket,
            "reason": self.reason,
        }


def _finite_probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionCalibrationError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise DecisionCalibrationError(f"{label} must be finite and in [0,1]")
    return number


def bucket_key(features: dict[str, Any]) -> str:
    missing = [name for name in FEATURE_SCHEMA if name not in features]
    if missing:
        raise DecisionCalibrationError(f"missing decision-change features: {missing}")
    transition = features["transition"]
    disposition = features["proposal_disposition"]
    if not isinstance(transition, str) or "->" not in transition:
        raise DecisionCalibrationError("transition feature is malformed")
    if not isinstance(disposition, str) or not disposition:
        raise DecisionCalibrationError("proposal_disposition feature is malformed")

    observations = int(features["min_observation_count"])
    flips = int(features["max_leader_flips"])
    stability = float(features["min_stable_run_fraction"])
    if observations < 0 or flips < 0:
        raise DecisionCalibrationError("count features must be non-negative")
    if not math.isfinite(stability) or not 0.0 <= stability <= 1.0:
        raise DecisionCalibrationError("stability feature must be in [0,1]")

    obs_bucket = 0 if observations <= 2 else 1 if observations <= 5 else 2 if observations <= 11 else 3
    flip_bucket = 0 if flips == 0 else 1 if flips <= 2 else 2
    stable_bucket = min(3, int(stability * 4.0))
    return (
        f"{transition}|{disposition}|n{obs_bucket}|"
        f"s{stable_bucket}|f{flip_bucket}"
    )


def rows_from_dataset(dataset: dict[str, Any]) -> list[DecisionChangeRow]:
    if dataset.get("extractor_version") != VALUE_EXTRACTOR_VERSION:
        raise DecisionCalibrationError(
            f"dataset extractor {dataset.get('extractor_version')!r} does not match "
            f"{VALUE_EXTRACTOR_VERSION!r}"
        )
    rows: list[DecisionChangeRow] = []
    for transition in dataset.get("transitions") or []:
        if not isinstance(transition, dict):
            raise DecisionCalibrationError("transition row must be an object")
        labels = transition.get("labels") or {}
        lower = transition.get("lower") or {}
        if labels.get("label_observed") is not True:
            continue
        label = labels.get("decision_changed")
        if not isinstance(label, bool):
            raise DecisionCalibrationError("observed decision_changed label must be boolean")
        verifiers = lower.get("verifiers") or []
        if len(verifiers) != 3:
            raise DecisionCalibrationError("lower feature vector must contain three verifiers")
        observations: list[int] = []
        flips: list[int] = []
        stability: list[float] = []
        for record in verifiers:
            if not isinstance(record, dict):
                raise DecisionCalibrationError("verifier feature row must be an object")
            observations.append(int(record["observation_count"]))
            flips.append(int(record["leader_flips"]))
            stability.append(float(record["stable_run_fraction"]))
        proposal = lower.get("proposal") or {}
        rows.append(
            DecisionChangeRow(
                position_group=str(transition.get("position_group") or ""),
                replicate=int(transition.get("replicate", 0)),
                transition=str(transition.get("transition") or ""),
                proposal_disposition=str(proposal.get("disposition") or ""),
                min_observation_count=min(observations),
                max_leader_flips=max(flips),
                min_stable_run_fraction=min(stability),
                label=label,
                feature_digest=str(transition.get("feature_digest") or ""),
                label_digest=str(transition.get("label_digest") or ""),
            )
        )
    return rows


def split_position_groups(groups: Iterable[str]) -> dict[str, str]:
    """Deterministic 60/20/20 split by position group when >=5 groups exist."""

    ordered = sorted(set(str(group) for group in groups if str(group)))
    result: dict[str, str] = {}
    if len(ordered) == 1:
        result[ordered[0]] = "train"
        return result
    if len(ordered) == 2:
        result[ordered[0]] = "train"
        result[ordered[1]] = "holdout"
        return result
    if len(ordered) == 3:
        result[ordered[0]] = "train"
        result[ordered[1]] = "calibration"
        result[ordered[2]] = "holdout"
        return result
    if len(ordered) == 4:
        for index, group in enumerate(ordered):
            result[group] = "train" if index < 2 else "calibration" if index == 2 else "holdout"
        return result
    for index, group in enumerate(ordered):
        slot = index % 5
        result[group] = "train" if slot <= 2 else "calibration" if slot == 3 else "holdout"
    return result


def _fit_buckets(
    rows: Sequence[DecisionChangeRow],
    *,
    smoothing_alpha: float,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, list[int]] = {}
    for row in rows:
        key = row.bucket()
        record = counts.setdefault(key, [0, 0])
        record[0] += 1
        if row.label:
            record[1] += 1
    return {
        key: {
            "support": total,
            "positives": positives,
            "change_probability": (
                positives + smoothing_alpha
            ) / (total + 2.0 * smoothing_alpha),
        }
        for key, (total, positives) in counts.items()
    }


def _reliability(
    rows: Sequence[DecisionChangeRow],
    *,
    buckets: dict[str, dict[str, Any]],
    min_support: int,
    prior: float,
) -> dict[str, Any]:
    if not rows:
        return {
            "rows": 0,
            "brier": None,
            "empirical_change_rate": None,
            "mean_predicted_change_rate": None,
            "in_domain_rate": None,
            "buckets": {},
        }
    squared: list[float] = []
    predicted: list[float] = []
    in_domain = 0
    by_bucket: dict[str, list[int]] = {}
    for row in rows:
        key = row.bucket()
        record = buckets.get(key)
        usable = record is not None and int(record["support"]) >= min_support
        probability = (
            float(record["change_probability"]) if usable else prior
        )
        if usable:
            in_domain += 1
        predicted.append(probability)
        squared.append((probability - (1.0 if row.label else 0.0)) ** 2)
        item = by_bucket.setdefault(key, [0, 0])
        item[0] += 1
        if row.label:
            item[1] += 1
    return {
        "rows": len(rows),
        "brier": sum(squared) / len(squared),
        "empirical_change_rate": sum(1 for row in rows if row.label) / len(rows),
        "mean_predicted_change_rate": sum(predicted) / len(predicted),
        "in_domain_rate": in_domain / len(rows),
        "buckets": {
            key: {
                "rows": total,
                "positives": positives,
                "empirical_change_rate": positives / total,
                "trained_support": int(buckets.get(key, {}).get("support", 0)),
                "trained_probability": (
                    None
                    if key not in buckets
                    else float(buckets[key]["change_probability"])
                ),
            }
            for key, (total, positives) in sorted(by_bucket.items())
        },
    }


@dataclass
class DecisionChangeModel:
    model_id: str
    created_utc: str
    dataset_id: str
    min_support: int
    smoothing_alpha: float
    buckets: dict[str, dict[str, Any]]
    split_by_position: dict[str, str]
    evaluation: dict[str, Any]
    source_sha256: str
    prior_change_probability: float = 1.0

    schema_version: int = DECISION_CALIBRATION_SCHEMA_VERSION
    model_kind: str = MODEL_KIND
    extractor_version: str = VALUE_EXTRACTOR_VERSION
    feature_schema: tuple[str, ...] = FEATURE_SCHEMA

    def evaluate(self, features: dict[str, Any]) -> ValueOfComputeEstimate:
        key = bucket_key(features)
        record = self.buckets.get(key)
        if record is None:
            return ValueOfComputeEstimate(
                change_probability=self.prior_change_probability,
                support=0,
                in_domain=False,
                bucket=key,
                reason="unseen bucket",
            )
        support = int(record["support"])
        probability = _finite_probability(
            record["change_probability"],
            f"bucket {key} probability",
        )
        if support < self.min_support:
            return ValueOfComputeEstimate(
                change_probability=max(probability, self.prior_change_probability),
                support=support,
                in_domain=False,
                bucket=key,
                reason=f"support {support} below min_support {self.min_support}",
            )
        return ValueOfComputeEstimate(
            change_probability=probability,
            support=support,
            in_domain=True,
            bucket=key,
            reason=None,
        )

    def as_dict(self) -> dict[str, Any]:
        core = {
            "schema_version": self.schema_version,
            "model_kind": self.model_kind,
            "extractor_version": self.extractor_version,
            "feature_schema": list(self.feature_schema),
            "model_id": self.model_id,
            "created_utc": self.created_utc,
            "dataset_id": self.dataset_id,
            "source_sha256": self.source_sha256,
            "min_support": self.min_support,
            "smoothing_alpha": self.smoothing_alpha,
            "prior_change_probability": self.prior_change_probability,
            "buckets": self.buckets,
            "split_by_position": self.split_by_position,
            "evaluation": self.evaluation,
        }
        core["evaluation_sha256"] = hashlib.sha256(
            json.dumps(
                self.evaluation,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return core


def _model_address(
    *,
    dataset_id: str,
    source_sha256: str,
    min_support: int,
    smoothing_alpha: float,
    buckets: dict[str, dict[str, Any]],
    split_by_position: dict[str, str],
) -> str:
    payload = {
        "schema_version": DECISION_CALIBRATION_SCHEMA_VERSION,
        "model_kind": MODEL_KIND,
        "extractor_version": VALUE_EXTRACTOR_VERSION,
        "feature_schema": list(FEATURE_SCHEMA),
        "dataset_id": dataset_id,
        "source_sha256": source_sha256,
        "min_support": min_support,
        "smoothing_alpha": smoothing_alpha,
        "buckets": buckets,
        "split_by_position": split_by_position,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"decision-voc-{digest[:16]}"


def fit_decision_change_model(
    dataset: dict[str, Any],
    *,
    source_sha256: str,
    min_support: int = 2,
    smoothing_alpha: float = 1.0,
) -> DecisionChangeModel:
    if isinstance(min_support, bool) or not isinstance(min_support, int) or min_support < 1:
        raise DecisionCalibrationError("min_support must be a positive integer")
    if (
        isinstance(smoothing_alpha, bool)
        or not isinstance(smoothing_alpha, (int, float))
        or not math.isfinite(float(smoothing_alpha))
        or float(smoothing_alpha) <= 0
    ):
        raise DecisionCalibrationError("smoothing_alpha must be finite and positive")

    rows = rows_from_dataset(dataset)
    if not rows:
        raise DecisionCalibrationError("cannot fit decision-change model from zero rows")
    split = split_position_groups(row.position_group for row in rows)
    train = [row for row in rows if split[row.position_group] == "train"]
    calibration = [
        row for row in rows if split[row.position_group] == "calibration"
    ]
    holdout = [row for row in rows if split[row.position_group] == "holdout"]
    if not train:
        raise DecisionCalibrationError("deterministic split produced no training rows")

    buckets = _fit_buckets(train, smoothing_alpha=float(smoothing_alpha))
    evaluation = {
        "row_count": len(rows),
        "train_rows": len(train),
        "calibration_rows": len(calibration),
        "holdout_rows": len(holdout),
        "position_groups": {
            "train": sorted(group for group, part in split.items() if part == "train"),
            "calibration": sorted(
                group for group, part in split.items() if part == "calibration"
            ),
            "holdout": sorted(
                group for group, part in split.items() if part == "holdout"
            ),
        },
        "train": _reliability(
            train,
            buckets=buckets,
            min_support=min_support,
            prior=1.0,
        ),
        "calibration": _reliability(
            calibration,
            buckets=buckets,
            min_support=min_support,
            prior=1.0,
        ),
        "holdout": _reliability(
            holdout,
            buckets=buckets,
            min_support=min_support,
            prior=1.0,
        ),
        "per_transition_support": {
            key: sum(1 for row in rows if row.transition == key)
            for key in sorted(set(row.transition for row in rows))
        },
    }
    dataset_id = str(dataset.get("dataset_id") or "")
    model_id = _model_address(
        dataset_id=dataset_id,
        source_sha256=source_sha256,
        min_support=min_support,
        smoothing_alpha=float(smoothing_alpha),
        buckets=buckets,
        split_by_position=split,
    )
    return DecisionChangeModel(
        model_id=model_id,
        created_utc=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        dataset_id=dataset_id,
        min_support=min_support,
        smoothing_alpha=float(smoothing_alpha),
        buckets=buckets,
        split_by_position=split,
        evaluation=evaluation,
        source_sha256=source_sha256,
        prior_change_probability=1.0,
    )


def write_decision_calibration(
    model: DecisionChangeModel,
    root: Path | str,
) -> Path:
    root = Path(root)
    target = root / model.model_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "model.json"
    path.write_text(
        json.dumps(model.as_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_decision_calibration(path: Path | str) -> DecisionChangeModel:
    path = Path(path)
    if path.is_dir():
        path = path / "model.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionCalibrationError(f"cannot load decision calibration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DecisionCalibrationError("decision calibration root must be an object")
    if data.get("model_kind") != MODEL_KIND:
        raise DecisionCalibrationError(
            f"expected model_kind {MODEL_KIND!r}, got {data.get('model_kind')!r}"
        )
    if data.get("schema_version") != DECISION_CALIBRATION_SCHEMA_VERSION:
        raise DecisionCalibrationError("decision calibration schema mismatch")
    if data.get("extractor_version") != VALUE_EXTRACTOR_VERSION:
        raise DecisionCalibrationError("decision calibration extractor mismatch")
    if tuple(data.get("feature_schema") or ()) != FEATURE_SCHEMA:
        raise DecisionCalibrationError("decision calibration feature schema mismatch")

    buckets = data.get("buckets")
    split = data.get("split_by_position")
    evaluation = data.get("evaluation")
    if not isinstance(buckets, dict) or not isinstance(split, dict) or not isinstance(evaluation, dict):
        raise DecisionCalibrationError("decision calibration contains malformed blocks")
    expected_eval = hashlib.sha256(
        json.dumps(
            evaluation,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if data.get("evaluation_sha256") != expected_eval:
        raise DecisionCalibrationError("decision calibration evaluation was modified")

    model = DecisionChangeModel(
        model_id=str(data.get("model_id") or ""),
        created_utc=str(data.get("created_utc") or ""),
        dataset_id=str(data.get("dataset_id") or ""),
        source_sha256=str(data.get("source_sha256") or ""),
        min_support=int(data.get("min_support")),
        smoothing_alpha=float(data.get("smoothing_alpha")),
        prior_change_probability=_finite_probability(
            data.get("prior_change_probability"),
            "prior_change_probability",
        ),
        buckets=buckets,
        split_by_position={str(k): str(v) for k, v in split.items()},
        evaluation=evaluation,
    )
    expected_id = _model_address(
        dataset_id=model.dataset_id,
        source_sha256=model.source_sha256,
        min_support=model.min_support,
        smoothing_alpha=model.smoothing_alpha,
        buckets=model.buckets,
        split_by_position=model.split_by_position,
    )
    if model.model_id != expected_id:
        raise DecisionCalibrationError(
            f"model declares {model.model_id!r} but contents address to {expected_id!r}"
        )
    return model


def fit_from_dataset_path(
    dataset_path: Path | str,
    *,
    min_support: int = 2,
    smoothing_alpha: float = 1.0,
) -> DecisionChangeModel:
    path = Path(dataset_path)
    if path.is_dir():
        path = path / "dataset.json"
    dataset = load_dataset(path)
    source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return fit_decision_change_model(
        dataset,
        source_sha256=source_sha,
        min_support=min_support,
        smoothing_alpha=smoothing_alpha,
    )
