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
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from controller.residuals import EXTRACTOR_VERSION


CALIBRATION_SCHEMA_VERSION = 1
#: v3: buckets are scoped to one solver family and fitted from shadow workers
#: only, so evidence from a different engine -- or from the unrestricted anchor,
#: about which the router never decides -- cannot supply another worker's
#: support.
MODEL_KIND = "bucketed_reversal_risk_v3"

#: Feature names the model consumes. Changing this list changes the model kind.
#: All three are computed by `common.residuals.past_only_features` from one
#: input, the primary-line history, so there is no train/serve skew to manage.
FEATURE_NAMES = ("observation_count", "leader_flips", "stable_run_fraction")


class CalibrationError(RuntimeError):
    """Raised when a calibration artifact cannot be produced or used honestly."""


@dataclass(frozen=True)
class TrainingRow:
    """One past-only feature vector with its observed future label."""

    run_id: str
    instance: str
    #: Solver family this row describes. Buckets are scoped by it: alpha-beta
    #: and MCTS evidence are not interchangeable.
    owner: str
    observation_count: int
    leader_flips: int
    stable_run_fraction: float
    label: bool

    def bucket(self) -> str:
        return bucket_key(self.features(), scope=self.owner)

    def features(self) -> dict[str, float]:
        return {
            "observation_count": float(self.observation_count),
            "leader_flips": float(self.leader_flips),
            "stable_run_fraction": self.stable_run_fraction,
        }


def bucket_key(features: dict[str, Any], *, scope: str) -> str:
    """Deterministic, auditable discretization, scoped to one solver family.

    The scope is not a feature; it is the population the bucket describes.
    Without it, observations of an unrestricted Stockfish anchor could satisfy
    the support floor and supply a low risk estimate for stopping an LC0 worker
    that has almost no evidence of its own.
    """
    if not isinstance(scope, str) or not scope:
        raise CalibrationError("bucket scope must be a non-empty solver family")
    missing = [name for name in FEATURE_NAMES if name not in features]
    if missing:
        raise CalibrationError(f"missing calibration features: {missing}")

    def quartile(value: Any) -> int:
        number = float(value)
        if number != number:  # NaN
            raise CalibrationError("calibration features must be finite")
        return int(max(0.0, min(0.999, number)) * 4)

    observations = int(features["observation_count"])
    if observations < 0:
        raise CalibrationError("observation_count must be non-negative")
    if observations <= 2:
        observed = 0
    elif observations <= 5:
        observed = 1
    elif observations <= 11:
        observed = 2
    else:
        observed = 3

    stable = quartile(features["stable_run_fraction"])
    flips = int(features["leader_flips"])
    if flips < 0:
        raise CalibrationError("leader_flips must be non-negative")
    flip_bucket = 0 if flips == 0 else (1 if flips <= 2 else 2)
    return f"{scope}|n{observed}|s{stable}|f{flip_bucket}"


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

    def evaluate(self, features: dict[str, Any], *, scope: str) -> CalibrationEvaluation:
        """Return a risk estimate, or an explicitly out-of-domain verdict.

        Out-of-domain returns the conservative prior, never an optimistic guess.
        """
        key = bucket_key(features, scope=scope)
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
            # Address the model by everything that determines it: provenance,
            # every hyperparameter, and the fitted contents themselves. Hashing
            # only sources plus a row count lets two different models -- even
            # ones with opposite labels -- collide on one model.json.
            model_id = content_address(
                min_support=min_support,
                smoothing_alpha=smoothing_alpha,
                horizon_fraction=horizon_fraction,
                train_rows=len(train),
                test_rows=len(test),
                sources=sources,
                buckets=buckets,
            )

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
            verdict = self.evaluate(row.features(), scope=row.owner)
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
            # Full precision on purpose. Rounding to six decimals persists a
            # value different from the one the evaluation was computed with, and
            # the difference is in the permissive direction: 1/21 serializes as
            # 0.047619, which passes a threshold of 0.047619 that the fitted
            # value 0.0476190476... does not. The served model must be the model
            # that was measured.
            "buckets": {
                key: {
                    "risk": float(record["risk"]),
                    "support": int(record["support"]),
                    "positives": int(record["positives"]),
                }
                for key, record in sorted(self.buckets.items())
            },
            "sources": self.sources,
            "evaluation": self.evaluation,
            "evaluation_sha256": evaluation_address(self.evaluation),
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
            buckets=_validated_buckets(data.get("buckets", {})),
            sources=list(data.get("sources", [])),
            evaluation=dict(data.get("evaluation", {})),
            prior_risk=_validated_probability(data.get("prior_risk", 1.0), "prior_risk"),
        )


def _validated_probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise CalibrationError(f"{label} must be a probability in [0, 1], got {value!r}")
    return number


def _validated_buckets(raw: Any) -> dict[str, dict[str, float]]:
    """Refuse a calibration whose stored buckets cannot be true.

    A file can be syntactically valid and still describe an impossible model --
    a negative risk, a fabricated support count, more positives than
    observations. Every one of those passes each suppression gate unchallenged,
    so they are rejected at load rather than trusted at decision time.
    """
    if not isinstance(raw, dict):
        raise CalibrationError("calibration buckets must be an object")
    validated: dict[str, dict[str, float]] = {}
    for key, record in raw.items():
        if not isinstance(key, str) or not key:
            raise CalibrationError("calibration bucket keys must be non-empty strings")
        if not isinstance(record, dict):
            raise CalibrationError(f"calibration bucket {key!r} must be an object")
        risk = _validated_probability(record.get("risk"), f"bucket {key!r} risk")
        support = record.get("support")
        positives = record.get("positives")
        for name, value in (("support", support), ("positives", positives)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CalibrationError(
                    f"bucket {key!r} {name} must be a non-negative integer, got {value!r}"
                )
        if positives > support:
            raise CalibrationError(
                f"bucket {key!r} claims {positives} positives from {support} observations"
            )
        validated[key] = {"risk": risk, "support": int(support), "positives": int(positives)}
    return validated


def evaluation_address(evaluation: dict[str, Any]) -> str:
    """Content address of the evaluation block.

    Round six addressed buckets, parameters and sources. Round seven then made
    authorization read `test_rows` and the reliability bucket list out of
    `evaluation`, which that address does not cover -- so a fabricated
    reliability entry could license a stop for a bucket with no held-out
    evidence while `model_id` still verified and `route.json` still reported the
    original identity. The evaluation is now addressed as well.
    """
    return hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def content_address(
    *,
    min_support: int,
    smoothing_alpha: float,
    horizon_fraction: float,
    train_rows: int,
    test_rows: int,
    sources: Sequence[dict[str, Any]],
    buckets: dict[str, dict[str, float]],
) -> str:
    """Address a model by everything that determines it.

    Provenance, every hyperparameter, and the fitted contents themselves.
    Hashing only sources plus a row count lets two different models -- even
    ones with opposite labels -- collide on one `model.json`.
    """
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "model_kind": MODEL_KIND,
                "schema_version": CALIBRATION_SCHEMA_VERSION,
                "extractor_version": EXTRACTOR_VERSION,
                "feature_names": list(FEATURE_NAMES),
                "min_support": min_support,
                "smoothing_alpha": smoothing_alpha,
                "horizon_fraction": horizon_fraction,
                "train_rows": train_rows,
                "test_rows": test_rows,
                "sources": [
                    [str(record.get("derived_id", "")), str(record.get("sha256", ""))]
                    for record in sources
                ],
                "buckets": {
                    key: [
                        int(record["support"]),
                        int(record["positives"]),
                        round(float(record["risk"]), 9),
                    ]
                    for key, record in sorted(buckets.items())
                },
            },
            sort_keys=True,
        ).encode("utf-8")
    )
    return f"calib-{digest.hexdigest()[:16]}"


def _base_rate(rows: Sequence[TrainingRow], alpha: float) -> float:
    positives = sum(1 for row in rows if row.label)
    return (positives + alpha) / (len(rows) + 2 * alpha)


def _fit_buckets(
    rows: Sequence[TrainingRow], *, smoothing_alpha: float
) -> dict[str, dict[str, float]]:
    counts: dict[str, list[int]] = {}
    for row in rows:
        key = row.bucket()
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


#: Every `HOLDOUT_STRIDE`-th run, by sorted run id, is held out.
HOLDOUT_STRIDE = 4


def holdout_run_ids(run_ids: Iterable[str]) -> frozenset[str]:
    """Choose the held-out runs deterministically, without hash luck.

    Hashing each run id independently makes the size of the held-out set a
    random variable: with ten runs it leaves *nothing* held out about 5.6% of
    the time, which would silently make a fitted model unusable for any decision
    that requires out-of-sample validation. Striding over the sorted ids removes
    that randomness and guarantees a non-empty split whenever there are at least
    two distinct runs. Run ids are timestamp-prefixed, so the stride also
    interleaves the holdout across the collection period instead of clustering
    it at one end.

    A single run still yields no holdout, which is correct: there is nothing to
    hold out from one run.
    """
    ordered = sorted(set(run_ids))
    if len(ordered) < 2:
        return frozenset()
    return frozenset(ordered[index] for index in range(1, len(ordered), HOLDOUT_STRIDE))


def _deterministic_split(
    rows: Sequence[TrainingRow],
) -> tuple[list[TrainingRow], list[TrainingRow]]:
    """Split by run identity, never by row, so a run cannot straddle the split."""
    holdout = holdout_run_ids(row.run_id for row in rows)
    train: list[TrainingRow] = []
    test: list[TrainingRow] = []
    for row in rows:
        (test if row.run_id in holdout else train).append(row)
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
        # Keyed by search id, so every dispatched stage contributes its own rows
        # instead of a later stage overwriting an earlier one.
        for labels in run.get("counterfactual_labels", {}).values():
            for item in labels:
                label = item.get("reversal_within_horizon")
                # A None label is either "no leader yet" or a right-censored
                # horizon. Neither is an observation, so neither becomes a row.
                if label is None or item.get("leader") is None:
                    continue
                owner = item.get("owner")
                if not owner:
                    # The unrestricted anchor. The router never decides whether
                    # to stop the anchor, so anchor rows would train a decision
                    # that is never made -- and would pool their support with
                    # the shadow workers the router does decide about.
                    continue
                if item.get("calibration_eligible") is False:
                    # The stream this came from is not a valid telemetry v1
                    # stream: it dropped events or failed adapter translation.
                    # A dropped leader flip reads as stability, which is exactly
                    # the direction that authorizes suppression.
                    continue
                features = item.get("features")
                if not isinstance(features, dict):
                    raise CalibrationError(
                        "derived checkpoint is missing its past-only feature vector; "
                        "re-derive the bundle with this build"
                    )
                missing = [name for name in FEATURE_NAMES if name not in features]
                if missing:
                    raise CalibrationError(f"derived features are missing {missing}")
                rows.append(
                    TrainingRow(
                        run_id=run_id,
                        instance=str(item.get("instance", "")),
                        owner=str(owner),
                        observation_count=int(features["observation_count"]),
                        leader_flips=int(features["leader_flips"]),
                        stable_run_fraction=float(features["stable_run_fraction"]),
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
    model = ReversalRiskModel.from_dict(data)
    evaluation = model.evaluation or {}
    train_rows = evaluation.get("train_rows")
    test_rows = evaluation.get("test_rows")
    if isinstance(train_rows, int) and isinstance(test_rows, int):
        # Recompute the address from what is actually on disk. A `model.json`
        # whose buckets, support counts, parameters or sources were edited while
        # keeping its old `model_id` would otherwise be served as if it were the
        # artifact that was evaluated -- `route.json` would report the stale
        # identity and a source path, and different stop behaviour would
        # masquerade as the calibrated model.
        expected = content_address(
            min_support=model.min_support,
            smoothing_alpha=model.smoothing_alpha,
            horizon_fraction=model.horizon_fraction,
            train_rows=train_rows,
            test_rows=test_rows,
            sources=model.sources,
            buckets=model.buckets,
        )
        if expected != model.model_id:
            raise CalibrationError(
                f"calibration {path} declares model_id {model.model_id!r} but its contents "
                f"address to {expected!r}; it was modified after it was evaluated"
            )
    declared = data.get("evaluation_sha256")
    if declared is not None:
        actual = evaluation_address(model.evaluation)
        if declared != actual:
            raise CalibrationError(
                f"calibration {path} declares evaluation_sha256 {declared!r} but its "
                f"evaluation addresses to {actual!r}; the held-out evidence this model's "
                "authorization depends on was modified"
            )
    return model
