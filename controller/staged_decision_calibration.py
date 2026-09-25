"""Calibration for same-process staged VERIFY decision change.

The model estimates one descriptive intervention outcome:

    P(a configured same-process VERIFY extension changes the shared frozen
      unanimous-VERIFY policy result | base-round past-only evidence)

It does not estimate correctness, Elo, move quality, or whether buying compute
is strategically optimal.  Unseen and under-supported buckets fail closed.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from controller.staged_value_of_compute import (
    STAGED_VALUE_EXTRACTOR_VERSION,
    StagedValueOfComputeError,
    load_dataset,
)


STAGED_DECISION_CALIBRATION_SCHEMA_VERSION = 1
MODEL_KIND = "bucketed_staged_verify_decision_change_v1"
FEATURE_SCHEMA = (
    "transition",
    "base_decision_disposition",
    "min_observation_count",
    "max_leader_flips",
    "min_stable_run_fraction",
)


class StagedDecisionCalibrationError(RuntimeError):
    """Raised when staged decision-change calibration is invalid."""


@dataclass(frozen=True)
class StagedDecisionChangeRow:
    position_group: str
    replicate: int
    transition: str
    base_decision_disposition: str
    min_observation_count: int
    max_leader_flips: int
    min_stable_run_fraction: float
    label: bool
    feature_digest: str
    label_digest: str

    def __post_init__(self) -> None:
        if not self.position_group:
            raise StagedDecisionCalibrationError(
                "position_group must be non-empty"
            )
        if (
            isinstance(self.replicate, bool)
            or not isinstance(self.replicate, int)
            or self.replicate < 0
        ):
            raise StagedDecisionCalibrationError(
                "replicate must be a non-negative integer"
            )
        if not self.transition.startswith("same-process:n"):
            raise StagedDecisionCalibrationError(
                "transition must be a same-process staged VERIFY key"
            )
        if not self.base_decision_disposition:
            raise StagedDecisionCalibrationError(
                "base decision disposition must be non-empty"
            )
        if self.min_observation_count < 0 or self.max_leader_flips < 0:
            raise StagedDecisionCalibrationError(
                "summary counts must be non-negative"
            )
        if not 0.0 <= float(self.min_stable_run_fraction) <= 1.0:
            raise StagedDecisionCalibrationError(
                "min_stable_run_fraction must be in [0,1]"
            )
        if len(self.feature_digest) != 64 or len(self.label_digest) != 64:
            raise StagedDecisionCalibrationError(
                "row digests must be SHA-256"
            )

    def features(self) -> dict[str, Any]:
        return {
            "transition": self.transition,
            "base_decision_disposition": self.base_decision_disposition,
            "min_observation_count": self.min_observation_count,
            "max_leader_flips": self.max_leader_flips,
            "min_stable_run_fraction": self.min_stable_run_fraction,
        }

    def bucket(self) -> str:
        return bucket_key(self.features())


@dataclass(frozen=True)
class StagedValueEstimate:
    change_probability: float
    support: int
    position_group_support: int
    in_domain: bool
    bucket: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_probability": self.change_probability,
            "support": self.support,
            "position_group_support": self.position_group_support,
            "in_domain": self.in_domain,
            "bucket": self.bucket,
            "reason": self.reason,
        }


def _probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StagedDecisionCalibrationError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise StagedDecisionCalibrationError(
            f"{label} must be finite and in [0,1]"
        )
    return number


def bucket_key(features: dict[str, Any]) -> str:
    missing = [name for name in FEATURE_SCHEMA if name not in features]
    if missing:
        raise StagedDecisionCalibrationError(
            f"missing staged decision-change features: {missing}"
        )
    transition = features["transition"]
    disposition = features["base_decision_disposition"]
    if not isinstance(transition, str) or not transition.startswith("same-process:n"):
        raise StagedDecisionCalibrationError("transition feature is malformed")
    if not isinstance(disposition, str) or not disposition:
        raise StagedDecisionCalibrationError(
            "base_decision_disposition feature is malformed"
        )
    observations = int(features["min_observation_count"])
    flips = int(features["max_leader_flips"])
    stability = float(features["min_stable_run_fraction"])
    if observations < 0 or flips < 0:
        raise StagedDecisionCalibrationError(
            "count features must be non-negative"
        )
    if not math.isfinite(stability) or not 0.0 <= stability <= 1.0:
        raise StagedDecisionCalibrationError(
            "stability feature must be in [0,1]"
        )

    obs_bucket = (
        0 if observations <= 2
        else 1 if observations <= 5
        else 2 if observations <= 11
        else 3
    )
    flip_bucket = 0 if flips == 0 else 1 if flips <= 2 else 2
    stable_bucket = min(3, int(stability * 4.0))
    return (
        f"{transition}|{disposition}|n{obs_bucket}|"
        f"s{stable_bucket}|f{flip_bucket}"
    )


def rows_from_dataset(dataset: dict[str, Any]) -> list[StagedDecisionChangeRow]:
    if dataset.get("extractor_version") != STAGED_VALUE_EXTRACTOR_VERSION:
        raise StagedDecisionCalibrationError(
            "dataset extractor does not match staged calibration"
        )
    rows: list[StagedDecisionChangeRow] = []
    for raw in dataset.get("rows") or []:
        if not isinstance(raw, dict):
            raise StagedDecisionCalibrationError(
                "staged dataset row must be an object"
            )
        features = raw.get("features") or {}
        labels = raw.get("labels") or {}
        verifiers = features.get("verifiers") or []
        if len(verifiers) != 3:
            raise StagedDecisionCalibrationError(
                "staged base feature vector must contain three verifiers"
            )
        observations: list[int] = []
        flips: list[int] = []
        stability: list[float] = []
        for record in verifiers:
            if not isinstance(record, dict):
                raise StagedDecisionCalibrationError(
                    "verifier feature row must be an object"
                )
            observations.append(int(record["observation_count"]))
            flips.append(int(record["leader_flips"]))
            stability.append(float(record["stable_run_fraction"]))

        base_decision = features.get("base_decision") or {}
        disposition = base_decision.get("disposition") or {}
        label = labels.get("decision_changed")
        if labels.get("label_observed") is not True or not isinstance(label, bool):
            continue
        rows.append(
            StagedDecisionChangeRow(
                position_group=str(raw.get("position_group") or ""),
                replicate=int(raw.get("replicate", 0)),
                transition=str(features.get("transition") or ""),
                base_decision_disposition=str(disposition.get("code") or ""),
                min_observation_count=min(observations),
                max_leader_flips=max(flips),
                min_stable_run_fraction=min(stability),
                label=label,
                feature_digest=str(raw.get("feature_digest") or ""),
                label_digest=str(raw.get("label_digest") or ""),
            )
        )
    return rows


def split_position_groups(groups: Iterable[str]) -> dict[str, str]:
    """Deterministic 60/20/20 split by position group."""

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
            result[group] = (
                "train" if index < 2
                else "calibration" if index == 2
                else "holdout"
            )
        return result
    for index, group in enumerate(ordered):
        slot = index % 5
        result[group] = (
            "train" if slot <= 2
            else "calibration" if slot == 3
            else "holdout"
        )
    return result


def _fit_buckets(
    rows: Sequence[StagedDecisionChangeRow],
    *,
    smoothing_alpha: float,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.bucket()
        record = counts.setdefault(
            key,
            {"support": 0, "positive": 0, "position_groups": set()},
        )
        record["support"] += 1
        if row.label:
            record["positive"] += 1
        record["position_groups"].add(row.position_group)

    out: dict[str, dict[str, Any]] = {}
    for key, record in counts.items():
        support = int(record["support"])
        positive = int(record["positive"])
        probability = (
            positive + smoothing_alpha
        ) / (
            support + 2.0 * smoothing_alpha
        )
        out[key] = {
            "support": support,
            "positive": positive,
            "position_group_support": len(record["position_groups"]),
            "change_probability": probability,
        }
    return out


def _evaluate_rows(
    rows: Sequence[StagedDecisionChangeRow],
    buckets: dict[str, dict[str, Any]],
    *,
    prior: float,
    min_support: int,
    min_position_groups: int,
) -> dict[str, Any]:
    if not rows:
        return {
            "test_rows": 0,
            "in_domain_rows": 0,
            "brier_score": None,
            "observed_change_rate": None,
            "mean_probability": None,
        }
    predictions: list[float] = []
    labels: list[float] = []
    in_domain = 0
    for row in rows:
        record = buckets.get(row.bucket())
        if (
            record is not None
            and int(record["support"]) >= min_support
            and int(record["position_group_support"]) >= min_position_groups
        ):
            p = _probability(record["change_probability"], "bucket probability")
            in_domain += 1
        else:
            p = prior
        predictions.append(p)
        labels.append(1.0 if row.label else 0.0)
    brier = sum((p - y) ** 2 for p, y in zip(predictions, labels)) / len(labels)
    return {
        "test_rows": len(rows),
        "in_domain_rows": in_domain,
        "brier_score": brier,
        "observed_change_rate": sum(labels) / len(labels),
        "mean_probability": sum(predictions) / len(predictions),
    }


@dataclass(frozen=True)
class StagedDecisionChangeModel:
    model_id: str
    created_utc: str
    dataset_id: str
    min_support: int
    min_position_groups: int
    smoothing_alpha: float
    prior_change_probability: float
    buckets: dict[str, dict[str, Any]]
    split_by_position: dict[str, str]
    evaluation: dict[str, Any]
    source_sha256: str

    schema_version: int = STAGED_DECISION_CALIBRATION_SCHEMA_VERSION
    model_kind: str = MODEL_KIND
    extractor_version: str = STAGED_VALUE_EXTRACTOR_VERSION
    feature_schema: tuple[str, ...] = FEATURE_SCHEMA

    def evaluate(self, features: dict[str, Any]) -> StagedValueEstimate:
        key = bucket_key(features)
        record = self.buckets.get(key)
        if record is None:
            return StagedValueEstimate(
                change_probability=self.prior_change_probability,
                support=0,
                position_group_support=0,
                in_domain=False,
                bucket=key,
                reason="unseen bucket",
            )
        support = int(record["support"])
        group_support = int(record["position_group_support"])
        probability = _probability(
            record["change_probability"],
            f"bucket {key} probability",
        )
        if support < self.min_support:
            return StagedValueEstimate(
                change_probability=max(
                    probability,
                    self.prior_change_probability,
                ),
                support=support,
                position_group_support=group_support,
                in_domain=False,
                bucket=key,
                reason=(
                    f"support {support} below min_support {self.min_support}"
                ),
            )
        if group_support < self.min_position_groups:
            return StagedValueEstimate(
                change_probability=max(
                    probability,
                    self.prior_change_probability,
                ),
                support=support,
                position_group_support=group_support,
                in_domain=False,
                bucket=key,
                reason=(
                    "position-group support "
                    f"{group_support} below minimum {self.min_position_groups}"
                ),
            )
        return StagedValueEstimate(
            change_probability=probability,
            support=support,
            position_group_support=group_support,
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
            "min_position_groups": self.min_position_groups,
            "smoothing_alpha": self.smoothing_alpha,
            "prior_change_probability": self.prior_change_probability,
            "buckets": self.buckets,
            "split_by_position": self.split_by_position,
            "evaluation": self.evaluation,
            "claim_boundary": (
                "Probability of staged VERIFY decision change only; not "
                "correctness, Elo, strength, or strategic utility."
            ),
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
    min_position_groups: int,
    smoothing_alpha: float,
    prior_change_probability: float,
    buckets: dict[str, dict[str, Any]],
    split_by_position: dict[str, str],
    evaluation: dict[str, Any],
) -> str:
    payload = {
        "schema_version": STAGED_DECISION_CALIBRATION_SCHEMA_VERSION,
        "model_kind": MODEL_KIND,
        "extractor_version": STAGED_VALUE_EXTRACTOR_VERSION,
        "feature_schema": list(FEATURE_SCHEMA),
        "dataset_id": dataset_id,
        "source_sha256": source_sha256,
        "min_support": min_support,
        "min_position_groups": min_position_groups,
        "smoothing_alpha": smoothing_alpha,
        "prior_change_probability": prior_change_probability,
        "buckets": buckets,
        "split_by_position": split_by_position,
        "evaluation": evaluation,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"staged-decision-change-v1:{digest[:16]}"


def fit_staged_decision_change_model(
    dataset: dict[str, Any],
    *,
    source_sha256: str,
    min_support: int = 5,
    min_position_groups: int = 3,
    smoothing_alpha: float = 1.0,
    prior_change_probability: float = 1.0,
) -> StagedDecisionChangeModel:
    rows = rows_from_dataset(dataset)
    if not rows:
        raise StagedDecisionCalibrationError(
            "cannot fit staged calibration without observed rows"
        )
    if min_support < 1 or min_position_groups < 1:
        raise StagedDecisionCalibrationError(
            "support thresholds must be positive"
        )
    if not math.isfinite(float(smoothing_alpha)) or smoothing_alpha <= 0:
        raise StagedDecisionCalibrationError(
            "smoothing_alpha must be positive and finite"
        )
    prior = _probability(
        prior_change_probability,
        "prior_change_probability",
    )
    split = split_position_groups(row.position_group for row in rows)
    train = [
        row for row in rows
        if split.get(row.position_group) == "train"
    ]
    calibration = [
        row for row in rows
        if split.get(row.position_group) == "calibration"
    ]
    holdout = [
        row for row in rows
        if split.get(row.position_group) == "holdout"
    ]
    if not train:
        raise StagedDecisionCalibrationError(
            "position split produced no training rows"
        )
    buckets = _fit_buckets(train, smoothing_alpha=float(smoothing_alpha))
    evaluation = {
        "row_count": len(rows),
        "train_rows": len(train),
        "calibration_rows": len(calibration),
        "holdout_rows": len(holdout),
        "position_groups": {
            "train": sorted(
                group for group, part in split.items() if part == "train"
            ),
            "calibration": sorted(
                group for group, part in split.items() if part == "calibration"
            ),
            "holdout": sorted(
                group for group, part in split.items() if part == "holdout"
            ),
        },
        "train": _evaluate_rows(
            train,
            buckets,
            prior=prior,
            min_support=min_support,
            min_position_groups=min_position_groups,
        ),
        "calibration": _evaluate_rows(
            calibration,
            buckets,
            prior=prior,
            min_support=min_support,
            min_position_groups=min_position_groups,
        ),
        "holdout": _evaluate_rows(
            holdout,
            buckets,
            prior=prior,
            min_support=min_support,
            min_position_groups=min_position_groups,
        ),
    }

    dataset_id = str(dataset.get("dataset_id") or "")
    model_id = _model_address(
        dataset_id=dataset_id,
        source_sha256=source_sha256,
        min_support=min_support,
        min_position_groups=min_position_groups,
        smoothing_alpha=float(smoothing_alpha),
        prior_change_probability=prior,
        buckets=buckets,
        split_by_position=split,
        evaluation=evaluation,
    )
    return StagedDecisionChangeModel(
        model_id=model_id,
        created_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        dataset_id=dataset_id,
        min_support=min_support,
        min_position_groups=min_position_groups,
        smoothing_alpha=float(smoothing_alpha),
        prior_change_probability=prior,
        buckets=buckets,
        split_by_position=split,
        evaluation=evaluation,
        source_sha256=source_sha256,
    )


def write_staged_decision_calibration(
    model: StagedDecisionChangeModel,
    root: Path | str,
) -> Path:
    root = Path(root)
    target = root / model.model_id.replace(":", "-") / "model.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(model.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_staged_decision_calibration(
    path: Path | str,
) -> StagedDecisionChangeModel:
    path = Path(path)
    if path.is_dir():
        path = path / "model.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagedDecisionCalibrationError(
            f"cannot load staged calibration {path}: {exc}"
        ) from exc
    if raw.get("model_kind") != MODEL_KIND:
        raise StagedDecisionCalibrationError(
            f"unexpected staged model kind: {raw.get('model_kind')!r}"
        )
    if raw.get("schema_version") != STAGED_DECISION_CALIBRATION_SCHEMA_VERSION:
        raise StagedDecisionCalibrationError("staged model schema mismatch")
    if raw.get("extractor_version") != STAGED_VALUE_EXTRACTOR_VERSION:
        raise StagedDecisionCalibrationError(
            "staged model extractor version mismatch"
        )
    if tuple(raw.get("feature_schema") or ()) != FEATURE_SCHEMA:
        raise StagedDecisionCalibrationError(
            "staged model feature schema mismatch"
        )
    evaluation = raw.get("evaluation")
    if not isinstance(evaluation, dict):
        raise StagedDecisionCalibrationError(
            "staged model evaluation block is malformed"
        )
    expected_eval = hashlib.sha256(
        json.dumps(
            evaluation,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if raw.get("evaluation_sha256") != expected_eval:
        raise StagedDecisionCalibrationError(
            "staged model evaluation was modified"
        )
    try:
        model = StagedDecisionChangeModel(
            model_id=str(raw["model_id"]),
            created_utc=str(raw["created_utc"]),
            dataset_id=str(raw["dataset_id"]),
            min_support=int(raw["min_support"]),
            min_position_groups=int(raw["min_position_groups"]),
            smoothing_alpha=float(raw["smoothing_alpha"]),
            prior_change_probability=float(raw["prior_change_probability"]),
            buckets=dict(raw["buckets"]),
            split_by_position=dict(raw["split_by_position"]),
            evaluation=evaluation,
            source_sha256=str(raw["source_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StagedDecisionCalibrationError(
            f"staged model is malformed: {exc}"
        ) from exc
    expected_id = _model_address(
        dataset_id=model.dataset_id,
        source_sha256=model.source_sha256,
        min_support=model.min_support,
        min_position_groups=model.min_position_groups,
        smoothing_alpha=model.smoothing_alpha,
        prior_change_probability=model.prior_change_probability,
        buckets=model.buckets,
        split_by_position=model.split_by_position,
        evaluation=model.evaluation,
    )
    if model.model_id != expected_id:
        raise StagedDecisionCalibrationError(
            f"model declares {model.model_id!r} but contents address to {expected_id!r}"
        )
    return model


def fit_from_path(
    dataset_path: Path | str,
    **kwargs: Any,
) -> StagedDecisionChangeModel:
    dataset_path = Path(dataset_path)
    if dataset_path.is_dir():
        dataset_path = dataset_path / "dataset.json"
    dataset = load_dataset(dataset_path)
    source_sha = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    return fit_staged_decision_change_model(
        dataset,
        source_sha256=source_sha,
        **kwargs,
    )
