"""Support/domain calibration for M14-F search-regime observations.

This is not a truth classifier for regime names.  It only asks whether a
structural observation bucket has enough independent position-group support to
be treated as in-domain by a later router.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from controller.regimes import (
    REGIME_EXTRACTOR_VERSION,
    RegimeDomainAssessment,
    RegimeError,
    RegimeObservation,
)


REGIME_CALIBRATION_SCHEMA_VERSION = 1
REGIME_DATASET_SCHEMA_VERSION = 1
MODEL_KIND = "regime_support_v1"
FEATURE_SCHEMA = (
    "verify_pattern",
    "relock_status",
    "mate_alarm_mask",
    "candidate_count_bucket",
    "refinement_state",
    "request_mode",
)


class RegimeCalibrationError(RuntimeError):
    """Raised when support calibration cannot be fit or served honestly."""


def _canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def split_position_groups(groups: Iterable[str]) -> dict[str, str]:
    """Deterministic group split; repeated runs of one position never leak."""

    ordered = sorted(set(str(group) for group in groups if str(group)))
    result: dict[str, str] = {}
    if not ordered:
        return result
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
                "train" if index < 2 else "calibration" if index == 2 else "holdout"
            )
        return result
    for index, group in enumerate(ordered):
        slot = index % 5
        result[group] = (
            "train" if slot <= 2 else "calibration" if slot == 3 else "holdout"
        )
    return result


def regime_bucket_features(observation: RegimeObservation) -> dict[str, str]:
    count = len(observation.candidate_roots)
    candidate_bucket = "c1" if count <= 1 else "c2_3" if count <= 3 else "c4_plus"

    refinement = observation.refinement
    if not refinement.present:
        refinement_state = "none"
    elif refinement.expansion_count > 0:
        refinement_state = "recursive"
    elif refinement.completed_nonterminal_targets > 0:
        refinement_state = "root_nonterminal"
    elif refinement.target_count > 0:
        refinement_state = "root_terminal_or_incomplete"
    else:
        refinement_state = "no_targets"

    mask = "+".join(observation.native_mate_alarm_families) or "none"
    return {
        "verify_pattern": observation.verify_pattern,
        "relock_status": observation.relock_status,
        "mate_alarm_mask": mask,
        "candidate_count_bucket": candidate_bucket,
        "refinement_state": refinement_state,
        "request_mode": observation.timing.request_mode,
    }


def regime_bucket_key(observation: RegimeObservation) -> str:
    features = regime_bucket_features(observation)
    return "|".join(f"{name}={features[name]}" for name in FEATURE_SCHEMA)


@dataclass(frozen=True)
class RegimeDatasetRow:
    position_group: str
    observation: RegimeObservation

    def __post_init__(self) -> None:
        if not self.position_group:
            raise RegimeCalibrationError("position_group must be non-empty")

    @property
    def bucket(self) -> str:
        return regime_bucket_key(self.observation)

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_group": self.position_group,
            "observation_digest": self.observation.digest,
            "bucket": self.bucket,
            "observation": self.observation.as_dict(),
        }


def _dataset_address_payload(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": dataset.get("schema_version"),
        "extractor_version": dataset.get("extractor_version"),
        "rows": dataset.get("rows") or [],
    }


def _expected_dataset_id(dataset: dict[str, Any]) -> str:
    return f"regime-dataset-{_canonical_digest(_dataset_address_payload(dataset))[:16]}"


def build_regime_dataset(
    observations: Sequence[RegimeObservation],
    *,
    position_groups: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not observations:
        raise RegimeCalibrationError("at least one regime observation is required")
    seen: set[str] = set()
    rows: list[RegimeDatasetRow] = []
    mapping = position_groups or {}
    for observation in observations:
        if observation.run_id in seen:
            raise RegimeCalibrationError(
                f"duplicate regime observation for run_id {observation.run_id!r}"
            )
        seen.add(observation.run_id)
        group = str(mapping.get(observation.run_id) or observation.position_id)
        rows.append(RegimeDatasetRow(position_group=group, observation=observation))

    payload = {
        "schema_version": REGIME_DATASET_SCHEMA_VERSION,
        "extractor_version": REGIME_EXTRACTOR_VERSION,
        "rows": [row.as_dict() for row in sorted(rows, key=lambda item: item.observation.run_id)],
    }
    payload["dataset_id"] = _expected_dataset_id(payload)
    return payload


def write_regime_dataset(dataset: dict[str, Any], root: Path | str) -> Path:
    dataset_id = str(dataset.get("dataset_id") or "")
    if not dataset_id:
        raise RegimeCalibrationError("dataset has no dataset_id")
    expected_id = _expected_dataset_id(dataset)
    if dataset_id != expected_id:
        raise RegimeCalibrationError(
            f"dataset_id {dataset_id!r} does not match contents {expected_id!r}"
        )
    target = Path(root) / dataset_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "dataset.json"
    payload = json.dumps(dataset, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != payload:
        raise RegimeCalibrationError(f"dataset address collision at {path}")
    path.write_text(payload, encoding="utf-8")
    return path


def rows_from_dataset(dataset: dict[str, Any]) -> list[RegimeDatasetRow]:
    if dataset.get("schema_version") != REGIME_DATASET_SCHEMA_VERSION:
        raise RegimeCalibrationError("unsupported regime dataset schema")
    if dataset.get("extractor_version") != REGIME_EXTRACTOR_VERSION:
        raise RegimeCalibrationError("unsupported regime dataset extractor")
    if dataset.get("dataset_id") != _expected_dataset_id(dataset):
        raise RegimeCalibrationError("regime dataset_id does not match contents")
    rows: list[RegimeDatasetRow] = []
    for raw in dataset.get("rows") or ():
        if not isinstance(raw, dict):
            raise RegimeCalibrationError("dataset row must be an object")
        try:
            observation = RegimeObservation.from_dict(dict(raw["observation"]))
        except (KeyError, RegimeError, TypeError, ValueError) as exc:
            raise RegimeCalibrationError(f"invalid regime observation row: {exc}") from exc
        if raw.get("observation_digest") != observation.digest:
            raise RegimeCalibrationError("dataset observation digest mismatch")
        row = RegimeDatasetRow(
            position_group=str(raw.get("position_group") or ""),
            observation=observation,
        )
        if raw.get("bucket") != row.bucket:
            raise RegimeCalibrationError("dataset bucket differs from deterministic extractor")
        rows.append(row)
    return rows


@dataclass
class RegimeSupportModel:
    model_id: str
    created_utc: str
    dataset_id: str
    min_support: int
    min_position_groups: int
    buckets: dict[str, dict[str, Any]]
    split_by_position: dict[str, str]
    evaluation: dict[str, Any]
    source_sha256: str

    schema_version: int = REGIME_CALIBRATION_SCHEMA_VERSION
    model_kind: str = MODEL_KIND
    extractor_version: str = REGIME_EXTRACTOR_VERSION
    feature_schema: tuple[str, ...] = FEATURE_SCHEMA

    def __post_init__(self) -> None:
        for name, value in (
            ("min_support", self.min_support),
            ("min_position_groups", self.min_position_groups),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise RegimeCalibrationError(f"{name} must be a positive integer")
        if len(self.source_sha256) != 64:
            raise RegimeCalibrationError("source_sha256 must be a SHA-256 digest")
        for bucket, record in self.buckets.items():
            if not isinstance(bucket, str) or not bucket:
                raise RegimeCalibrationError("regime support bucket key must be non-empty")
            if not isinstance(record, dict):
                raise RegimeCalibrationError(f"bucket {bucket!r} must be an object")
            support = record.get("support")
            group_support = record.get("position_group_support")
            if (
                isinstance(support, bool)
                or not isinstance(support, int)
                or support < 0
                or isinstance(group_support, bool)
                or not isinstance(group_support, int)
                or group_support < 0
            ):
                raise RegimeCalibrationError(
                    f"bucket {bucket!r} has invalid support counters"
                )

    def evaluate(self, observation: RegimeObservation) -> RegimeDomainAssessment:
        bucket = regime_bucket_key(observation)
        record = self.buckets.get(bucket)
        if record is None:
            return RegimeDomainAssessment(
                bucket=bucket,
                support=0,
                position_group_support=0,
                in_domain=False,
                reason="unseen structural bucket",
            )
        support = int(record.get("support", 0))
        group_support = int(record.get("position_group_support", 0))
        if support < self.min_support:
            return RegimeDomainAssessment(
                bucket=bucket,
                support=support,
                position_group_support=group_support,
                in_domain=False,
                reason=f"support {support} below min_support {self.min_support}",
            )
        if group_support < self.min_position_groups:
            return RegimeDomainAssessment(
                bucket=bucket,
                support=support,
                position_group_support=group_support,
                in_domain=False,
                reason=(
                    f"position-group support {group_support} below "
                    f"min_position_groups {self.min_position_groups}"
                ),
            )
        return RegimeDomainAssessment(
            bucket=bucket,
            support=support,
            position_group_support=group_support,
            in_domain=True,
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
            "buckets": self.buckets,
            "split_by_position": self.split_by_position,
            "evaluation": self.evaluation,
        }
        core["evaluation_sha256"] = _canonical_digest(self.evaluation)
        return core


def _coverage(
    rows: Sequence[RegimeDatasetRow],
    *,
    buckets: dict[str, dict[str, Any]],
    min_support: int,
    min_position_groups: int,
) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "in_domain_rows": 0, "in_domain_rate": None}
    admitted = 0
    for row in rows:
        record = buckets.get(row.bucket)
        if record is None:
            continue
        if (
            int(record["support"]) >= min_support
            and int(record["position_group_support"]) >= min_position_groups
        ):
            admitted += 1
    return {
        "rows": len(rows),
        "in_domain_rows": admitted,
        "in_domain_rate": admitted / len(rows),
    }


def fit_regime_support_model(
    dataset: dict[str, Any],
    *,
    source_sha256: str,
    min_support: int = 2,
    min_position_groups: int = 2,
) -> RegimeSupportModel:
    for name, value in (
        ("min_support", min_support),
        ("min_position_groups", min_position_groups),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise RegimeCalibrationError(f"{name} must be a positive integer")
    if len(source_sha256) != 64:
        raise RegimeCalibrationError("source_sha256 must be a SHA-256 digest")

    rows = rows_from_dataset(dataset)
    if not rows:
        raise RegimeCalibrationError("cannot fit regime support from zero rows")
    split = split_position_groups(row.position_group for row in rows)
    train = [row for row in rows if split[row.position_group] == "train"]
    calibration = [row for row in rows if split[row.position_group] == "calibration"]
    holdout = [row for row in rows if split[row.position_group] == "holdout"]
    if not train:
        raise RegimeCalibrationError("deterministic split produced no training rows")

    buckets: dict[str, dict[str, Any]] = {}
    for row in train:
        record = buckets.setdefault(
            row.bucket,
            {"support": 0, "position_groups": set()},
        )
        record["support"] += 1
        record["position_groups"].add(row.position_group)
    serial_buckets = {
        key: {
            "support": int(record["support"]),
            "position_group_support": len(record["position_groups"]),
            "position_groups": sorted(record["position_groups"]),
        }
        for key, record in sorted(buckets.items())
    }

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
        "train": _coverage(
            train,
            buckets=serial_buckets,
            min_support=min_support,
            min_position_groups=min_position_groups,
        ),
        "calibration": _coverage(
            calibration,
            buckets=serial_buckets,
            min_support=min_support,
            min_position_groups=min_position_groups,
        ),
        "holdout": _coverage(
            holdout,
            buckets=serial_buckets,
            min_support=min_support,
            min_position_groups=min_position_groups,
        ),
    }

    core = {
        "schema_version": REGIME_CALIBRATION_SCHEMA_VERSION,
        "model_kind": MODEL_KIND,
        "extractor_version": REGIME_EXTRACTOR_VERSION,
        "feature_schema": list(FEATURE_SCHEMA),
        "dataset_id": str(dataset.get("dataset_id") or ""),
        "source_sha256": source_sha256,
        "min_support": min_support,
        "min_position_groups": min_position_groups,
        "buckets": serial_buckets,
        "split_by_position": split,
    }
    model_id = f"regime-support-{_canonical_digest(core)[:16]}"
    return RegimeSupportModel(
        model_id=model_id,
        created_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        dataset_id=str(dataset.get("dataset_id") or ""),
        min_support=min_support,
        min_position_groups=min_position_groups,
        buckets=serial_buckets,
        split_by_position=split,
        evaluation=evaluation,
        source_sha256=source_sha256,
    )


def write_regime_support_model(
    model: RegimeSupportModel,
    root: Path | str,
) -> Path:
    target = Path(root) / model.model_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "model.json"
    payload = json.dumps(model.as_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing.pop("created_utc", None)
        current = model.as_dict()
        current.pop("created_utc", None)
        if existing != current:
            raise RegimeCalibrationError(f"model address collision at {path}")
        return path
    path.write_text(payload, encoding="utf-8")
    return path


def load_regime_support_model(path: Path | str) -> RegimeSupportModel:
    path = Path(path)
    if path.is_dir():
        path = path / "model.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegimeCalibrationError(f"cannot load regime support model {path}: {exc}") from exc
    if payload.get("schema_version") != REGIME_CALIBRATION_SCHEMA_VERSION:
        raise RegimeCalibrationError("unsupported regime support schema")
    if payload.get("model_kind") != MODEL_KIND:
        raise RegimeCalibrationError("unexpected regime support model kind")
    if payload.get("extractor_version") != REGIME_EXTRACTOR_VERSION:
        raise RegimeCalibrationError("regime support extractor mismatch")
    if tuple(payload.get("feature_schema") or ()) != FEATURE_SCHEMA:
        raise RegimeCalibrationError("regime support feature schema mismatch")

    model = RegimeSupportModel(
        model_id=str(payload["model_id"]),
        created_utc=str(payload["created_utc"]),
        dataset_id=str(payload["dataset_id"]),
        min_support=int(payload["min_support"]),
        min_position_groups=int(payload["min_position_groups"]),
        buckets=dict(payload.get("buckets") or {}),
        split_by_position={
            str(key): str(value)
            for key, value in (payload.get("split_by_position") or {}).items()
        },
        evaluation=dict(payload.get("evaluation") or {}),
        source_sha256=str(payload["source_sha256"]),
    )
    expected = model.as_dict()
    if payload.get("evaluation_sha256") != expected["evaluation_sha256"]:
        raise RegimeCalibrationError("regime support evaluation digest mismatch")
    if model.model_id != f"regime-support-{_canonical_digest({
        'schema_version': REGIME_CALIBRATION_SCHEMA_VERSION,
        'model_kind': MODEL_KIND,
        'extractor_version': REGIME_EXTRACTOR_VERSION,
        'feature_schema': list(FEATURE_SCHEMA),
        'dataset_id': model.dataset_id,
        'source_sha256': model.source_sha256,
        'min_support': model.min_support,
        'min_position_groups': model.min_position_groups,
        'buckets': model.buckets,
        'split_by_position': model.split_by_position,
    })[:16]}":
        raise RegimeCalibrationError("regime support model_id does not match contents")
    return model
