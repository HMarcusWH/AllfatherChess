"""Calibrated reversal-risk models fitted from replay-derived features.

A calibration artifact is the only object in this repository allowed to turn an
observation into a risk estimate, and even then it produces evidence, never
authorization. Whether a risk is low enough to license an action is a policy
decision made in `controller/routing.py` against thresholds declared in config.

Discipline
----------
- features are past-only, so a fitted model is usable live;
- buckets below the declared support floor are reported out-of-domain;
- every model records the derived artifacts and hashes it was fitted from;
- evaluation is out-of-sample on a deterministic split;
- a model is never applied to features produced by a different extractor version.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from controller.residuals import EXTRACTOR_VERSION


CALIBRATION_SCHEMA_VERSION = 1
MODEL_KIND = "bucketed_reversal_risk_v1"

#: Feature names the model consumes. Changing this list changes the model kind.
FEATURE_NAMES = ("elapsed_fraction", "stable_run_fraction", "leader_flips")


class CalibrationError(RuntimeError):
    """Raised when a calibration artifact cannot be produced or used honestly."""


@dataclass(frozen=True)
class TrainingRow:
    """One past-only feature vector with its observed future label."""

    run_id: str
    instance: str
    elapsed_fraction: float
    stable_run_fraction: float
    leader_flips: int
    label: bool

    def features(self) -> dict[str, float]:
        return {
            "elapsed_fraction": self.elapsed_fraction,
            "stable_run_fraction": self.stable_run_fraction,
            "leader_flips": float(self.leader_flips),
        }


def bucket_key(features: dict[str, Any]) -> str:
    """Deterministic, auditable discretization of the past-only features."""
    missing = [name for name in FEATURE_NAMES if name not in features]
    if missing:
        raise CalibrationError(f"missing calibration features: {missing}")

    def quartile(value: Any) -> int:
        number = float(value)
        if number != number:  # NaN
            raise CalibrationError("calibration features must be finite")
        return int(max(0.0, min(0.999, number)) * 4)

    elapsed = quartile(features["elapsed_fraction"])
    stable = quartile(features["stable_run_fraction"])
    flips = int(features["leader_flips"])
    if flips < 0:
        raise CalibrationError("leader_flips must be non-negative")
    flip_bucket = 0 if flips == 0 else (1 if flips <= 2 else 2)
    return f"e{elapsed}|s{stable}|f{flip_bucket}"


@dataclass(frozen=True)
class CalibrationEvaluation:
    """A risk estimate that always states whether it may be relied upon."""

    risk: float
    support: int
    in_domain: bool
    bucket: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "support": self.support,
            "in_domain": self.in_domain,
            "bucket": self.bucket,
            "reason": self.reason,
        }


@dataclass
class ReversalRiskModel:
    """Empirical per-bucket probability that an engine's leader still reverses."""

    model_id: str
    created_utc: str
    extractor_version: str
    min_support: int
    smoothing_alpha: float
    horizon_fraction: float
    buckets: dict[str, dict[str, float]]
    sources: list[dict[str, Any]] = field(default_factory=list)
    evaluation: dict[str, Any] = field(default_factory=dict)
    prior_risk: float = 1.0

    schema_version: int = CALIBRATION_SCHEMA_VERSION
    model_kind: str = MODEL_KIND

    # -- serving -------------------------------------------------------------

    def evaluate(self, features: dict[str, Any]) -> CalibrationEvaluation:
        """Return a risk estimate, or an explicitly out-of-domain verdict.

        Out-of-domain returns the conservative prior, never an optimistic guess.
        """
        key = bucket_key(features)
        record = self.buckets.get(key)
        if record is None:
            return CalibrationEvaluation(
                risk=self.prior_risk,
                support=0,
                in_domain=False,
                bucket=key,
                reason="no calibrated bucket for these features",
            )
        support = int(record["support"])
        if support < self.min_support:
            return CalibrationEvaluation(
                risk=max(float(record["risk"]), self.prior_risk),
                support=support,
                in_domain=False,
                bucket=key,
                reason=f"bucket support {support} is below the declared floor {self.min_support}",
            )
        return CalibrationEvaluation(
            risk=float(record["risk"]),
            support=support,
            in_domain=True,
            bucket=key,
        )

    # -- fitting -------------------------------------------------------------

    @classmethod
    def fit(
        cls,
        rows: Sequence[TrainingRow],
        *,
        min_support: int = 25,
        smoothing_alpha: float = 1.0,
        horizon_fraction: float = 0.25,
        sources: Sequence[dict[str, Any]] = (),
        model_id: str | None = None,
        now: _dt.datetime | None = None,
    ) -> "ReversalRiskModel":
        if not rows:
            raise CalibrationError("cannot fit a calibration model from zero rows")
        if min_support < 1:
            raise CalibrationError("min_support must be a positive integer")
        if smoothing_alpha <= 0:
            raise CalibrationError("smoothing_alpha must be positive")

        train, test = _deterministic_split(rows)
        if not train:
            raise CalibrationError("deterministic split produced an empty training set")

        buckets = _fit_buckets(train, smoothing_alpha=smoothing_alpha)
        moment = now or _dt.datetime.now(_dt.timezone.utc)
        if model_id is None:
            digest = hashlib.sha256()
            for record in sources:
                digest.update(str(record.get("derived_id", "")).encode("utf-8"))
                digest.update(str(record.get("sha256", "")).encode("utf-8"))
            digest.update(MODEL_KIND.encode("ascii"))
            digest.update(EXTRACTOR_VERSION.encode("ascii"))
            digest.update(str(len(train)).encode("ascii"))
            model_id = f"calib-{digest.hexdigest()[:16]}"

        model = cls(
            model_id=model_id,
            created_utc=moment.isoformat().replace("+00:00", "Z"),
            extractor_version=EXTRACTOR_VERSION,
            min_support=min_support,
            smoothing_alpha=smoothing_alpha,
            horizon_fraction=horizon_fraction,
            buckets=buckets,
            sources=list(sources),
            prior_risk=_base_rate(train, smoothing_alpha),
        )
        model.evaluation = model._evaluate_out_of_sample(train, test)
        return model

    def _evaluate_out_of_sample(
        self, train: Sequence[TrainingRow], test: Sequence[TrainingRow]
    ) -> dict[str, Any]:
        """Brier score plus a reliability table on the held-out split."""
        if not test:
            return {
                "train_rows": len(train),
                "test_rows": 0,
                "brier_score": None,
                "in_domain_rate": None,
                "reliability": [],
                "note": "no held-out rows; this model is not validated out of sample",
            }
        squared = 0.0
        in_domain = 0
        reliability: dict[str, dict[str, float]] = {}
        for row in test:
            verdict = self.evaluate(row.features())
            squared += (verdict.risk - (1.0 if row.label else 0.0)) ** 2
            if verdict.in_domain:
                in_domain += 1
            entry = reliability.setdefault(
                verdict.bucket, {"predicted": verdict.risk, "observed": 0.0, "count": 0.0}
            )
            entry["observed"] += 1.0 if row.label else 0.0
            entry["count"] += 1.0
        table = [
            {
                "bucket": key,
                "predicted_risk": round(entry["predicted"], 6),
                "observed_rate": round(entry["observed"] / entry["count"], 6),
                "count": int(entry["count"]),
            }
            for key, entry in sorted(reliability.items())
        ]
        return {
            "train_rows": len(train),
            "test_rows": len(test),
            "brier_score": round(squared / len(test), 6),
            "in_domain_rate": round(in_domain / len(test), 6),
            "reliability": table,
        }

    # -- serialization -------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_kind": self.model_kind,
            "model_id": self.model_id,
            "created_utc": self.created_utc,
            "extractor_version": self.extractor_version,
            "feature_names": list(FEATURE_NAMES),
            "parameters": {
                "min_support": self.min_support,
                "smoothing_alpha": self.smoothing_alpha,
                "horizon_fraction": self.horizon_fraction,
            },
            "prior_risk": self.prior_risk,
            "buckets": {
                key: {
                    "risk": round(float(record["risk"]), 6),
                    "support": int(record["support"]),
                    "positives": int(record["positives"]),
                }
                for key, record in sorted(self.buckets.items())
            },
            "sources": self.sources,
            "evaluation": self.evaluation,
            "claim": (
                "CALIBRATED from replay observations. This estimates whether an "
                "engine's own leader changes later in its own search. It is not a "
                "statement about chess correctness and licenses no strength claim."
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReversalRiskModel":
        version = data.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != CALIBRATION_SCHEMA_VERSION:
            raise CalibrationError(f"unsupported calibration schema_version: {version!r}")
        if data.get("model_kind") != MODEL_KIND:
            raise CalibrationError(f"unsupported calibration model_kind: {data.get('model_kind')!r}")
        if data.get("extractor_version") != EXTRACTOR_VERSION:
            raise CalibrationError(
                f"calibration was fitted against {data.get('extractor_version')!r} features, "
                f"this build extracts {EXTRACTOR_VERSION!r}"
            )
        if list(data.get("feature_names", ())) != list(FEATURE_NAMES):
            raise CalibrationError("calibration feature set does not match this build")
        parameters = data.get("parameters", {})
        return cls(
            model_id=str(data["model_id"]),
            created_utc=str(data["created_utc"]),
            extractor_version=str(data["extractor_version"]),
            min_support=int(parameters.get("min_support", 25)),
            smoothing_alpha=float(parameters.get("smoothing_alpha", 1.0)),
            horizon_fraction=float(parameters.get("horizon_fraction", 0.25)),
            buckets={
                key: {
                    "risk": float(record["risk"]),
                    "support": int(record["support"]),
                    "positives": int(record["positives"]),
                }
                for key, record in data.get("buckets", {}).items()
            },
            sources=list(data.get("sources", [])),
            evaluation=dict(data.get("evaluation", {})),
            prior_risk=float(data.get("prior_risk", 1.0)),
        )


def _base_rate(rows: Sequence[TrainingRow], alpha: float) -> float:
    positives = sum(1 for row in rows if row.label)
    return (positives + alpha) / (len(rows) + 2 * alpha)


def _fit_buckets(
    rows: Sequence[TrainingRow], *, smoothing_alpha: float
) -> dict[str, dict[str, float]]:
    counts: dict[str, list[int]] = {}
    for row in rows:
        key = bucket_key(row.features())
        entry = counts.setdefault(key, [0, 0])
        entry[0] += 1
        if row.label:
            entry[1] += 1
    return {
        key: {
            "support": total,
            "positives": positives,
            "risk": (positives + smoothing_alpha) / (total + 2 * smoothing_alpha),
        }
        for key, (total, positives) in counts.items()
    }


def _deterministic_split(
    rows: Sequence[TrainingRow],
) -> tuple[list[TrainingRow], list[TrainingRow]]:
    """Split by run identity, never by row, so a run cannot straddle the split."""
    train: list[TrainingRow] = []
    test: list[TrainingRow] = []
    for row in rows:
        digest = hashlib.sha256(row.run_id.encode("utf-8")).digest()
        (test if digest[0] % 4 == 0 else train).append(row)
    return train, test


def training_rows_from_derived(derived: dict[str, Any]) -> list[TrainingRow]:
    """Build past-only training rows from a derived features artifact."""
    if derived.get("extractor_version") != EXTRACTOR_VERSION:
        raise CalibrationError(
            f"derived artifact extractor {derived.get('extractor_version')!r} "
            f"does not match this build ({EXTRACTOR_VERSION!r})"
        )
    rows: list[TrainingRow] = []
    for run in derived.get("runs", []):
        run_id = str(run["run_id"])
        leaders_by_instance: dict[str, list[tuple[float, str | None]]] = {}
        for instance, labels in run.get("counterfactual_labels", {}).items():
            leaders_by_instance[instance] = [
                (float(item["checkpoint_fraction"]), item["leader"]) for item in labels
            ]
        for instance, labels in run.get("counterfactual_labels", {}).items():
            history = leaders_by_instance[instance]
            for index, item in enumerate(labels):
                label = item.get("reversal_within_horizon")
                if label is None or item.get("leader") is None:
                    continue
                past = [leader for _, leader in history[: index + 1] if leader is not None]
                flips = sum(1 for a, b in zip(past, past[1:]) if a != b)
                stable_run = 0
                for leader in reversed(past[:-1]):
                    if leader != past[-1]:
                        break
                    stable_run += 1
                elapsed = float(item["checkpoint_fraction"])
                denominator = max(1, index)
                rows.append(
                    TrainingRow(
                        run_id=run_id,
                        instance=instance,
                        elapsed_fraction=elapsed,
                        stable_run_fraction=stable_run / denominator,
                        leader_flips=flips,
                        label=bool(label),
                    )
                )
    return rows


def write_calibration(model: ReversalRiskModel, calibration_root: Path) -> Path:
    target = Path(calibration_root) / model.model_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "model.json"
    path.write_text(json.dumps(model.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_calibration(path: Path) -> ReversalRiskModel:
    path = Path(path)
    if path.is_dir():
        path = path / "model.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot load calibration {path}: {exc}") from exc
    return ReversalRiskModel.from_dict(data)
