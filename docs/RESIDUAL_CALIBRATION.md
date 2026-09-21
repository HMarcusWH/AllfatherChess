# Residual geometry and calibration

## Status

Implemented as a **derived** layer. Nothing here is raw evidence, and nothing
here is authorization.

```text
raw telemetry -> replay bundle -> residual features -> calibrated model -> policy
                                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                  this document
```

Artifacts:

```text
<replay_root>/../derived/<derived_id>/features.json
<replay_root>/../calibration/<model_id>/model.json
```

Produced by `scripts/residual-calibration.py`. Validated by
`tests/controller/test_residuals.py` against ten synthetic scenarios.

## The scale firewall

Stockfish centipawns, Reckless centipawns, and a `ScoreType`-qualified LC0 score
are **not** calibrated onto a common latent scale. `common/residuals.py`
enforces this: combining two differently-tagged values raises `ScaleMixingError`.

Consequently every cross-engine feature in v1 is *structural*:

| Feature | Definition |
| --- | --- |
| `leader_agree` | do both sides name the same move as primary |
| `top_k_overlap` | shared fraction of the smaller top-k prefix |
| `rank_agreement` | Kendall tau-b over the moves both sides ranked |
| `pv_divergence` | `1 - shared_prefix / longest_pv` |
| `candidate_jaccard` | set overlap of observed candidates |

Within-engine quantities are allowed to be numeric, and always carry their
semantics tag so a consumer cannot forget which scale they live on:

| Feature | Definition |
| --- | --- |
| `within_engine_margin` | `(v1 - v2) / (|v1| + |v2| + 1)` between primary and runner-up |
| `leader_flips` | number of primary-line changes |
| `stabilization_fraction` | fraction of the trajectory spent before the leader last changed |
| `pv_persistence` | mean shared-prefix ratio between consecutive PVs |
| `work_after_stability_ratio` | engine-native work spent after the leader last changed |
| `unresolved_set` | candidates whose separation from the leader is not yet decisive |

Any future numeric cross-engine transformation must be explicit, fitted from
data, versioned, evaluated out of sample, and carry provenance. None exists yet.

## Support discipline, and a real architectural consequence

The shadow-mode observation partition is `root_index % owner_count`. It is
deliberately policy-free, and it makes the three shadow workers' regions
**pairwise disjoint**.

That has a consequence worth stating plainly: **inside a single run, a direct
Stockfish-vs-Reckless leader comparison has empty shared support.** The two
workers were never authorized to search a common root, so there is nothing to
compare. The implementation does not paper over this. Every cross-engine
comparison carries its shared-support size and reports `None` with an explicit
`undefined_reason` when the support is empty.

The comparisons that *do* have support in v1 are each shadow worker against the
unrestricted anchor, over that worker's own region. Those are recorded under
`anchor_vs_owner`.

The cross-engine library is nevertheless correct for overlapping regions: the
synthetic fixtures include scenarios where workers share a region, and the tests
exercise leader agreement, top-k overlap, rank agreement, and PV divergence
there. A later overlap-capable COMPARE/VERIFY phase can use them unchanged.

Note also that with the anchor at `MultiPV = 1`, the anchor only ever reports
one move, so `anchor_vs_owner` is defined only for the owner holding the
anchor's leader. Shadow workers run at `MultiPV = 3` because they hold no
decision authority; the anchor stays at `MultiPV = 1` so it remains the
unmodified baseline search.

## Checkpoints

Features are extracted at fractions of the run's controller-clock span (default
every tenth). `observed_ms` is controller-side and shared by every stream in a
run, which is why it — and not any engine-native counter — defines a checkpoint.

## Counterfactual labels

All labels are **descriptive**. This repository has no independent chess truth
source, so no label is named or usable as correctness.

| Label | Meaning |
| --- | --- |
| `later_leader_changed` | the engine's own preference changed after this checkpoint |
| `later_pv_changed` | its principal variation differed at the end |
| `stable_to_end` | its leader never changed again |
| `reversal_within_horizon` | its leader changed within the next horizon fraction |
| `work_at_checkpoint` | engine-native work spent, with its semantics tag |
| `first_discoverer_of_anchor_move` | which authorized stream first named the anchor's final move |

`later_leader_changed = true` means the engine changed its mind. It does not
mean the earlier move was wrong.

## Calibration model

`bucketed_reversal_risk_v1` estimates the probability that an engine's own
leader still reverses within the horizon, from **past-only** features:

```text
elapsed_fraction      how far into the declared wall budget we are
stable_run_fraction   how long the current leader has held
leader_flips          how often the leader has changed so far
```

Past-only matters: the same feature function serves training and live routing,
so an online decision and a later offline audit cannot disagree.

Bucketing is deliberately coarse and auditable: each continuous feature is cut
into quartiles, flips into `{0, 1-2, 3+}`, giving keys like `e2|s3|f0`. Risk is
the Laplace-smoothed empirical rate in the bucket.

Fail-closed behavior:

- an unknown bucket returns the conservative prior and `in_domain: false`;
- a bucket below `min_support` returns `max(bucket risk, prior)` and
  `in_domain: false`;
- a model fitted against a different `extractor_version` or feature set is
  refused at load;
- fitting from zero rows is refused.

Evaluation is out of sample on a split **by run identity**, so a run cannot
straddle the split. The artifact records the Brier score, the in-domain rate,
and a reliability table of predicted versus observed rates per bucket.

## Provenance

A derived artifact records each source run id, the manifest sha256, and each
stream sha256. A calibration records the derived artifact id and its sha256.
Every derived artifact is content-addressed, so re-deriving the same runs with
the same parameters yields the same id.

## Claim status

- **MEASURED**: the trajectories and labels observed in the recorded runs.
- **CALIBRATED**: the bucketed reversal-risk relationship fitted from them.
- **OPEN**: whether that relationship generalizes beyond the positions, time
  controls, hardware, and engine builds it was fitted on; whether reversal risk
  predicts anything about move quality; and whether the LC0 validation profile,
  which is backend-light and explicitly not strength-qualified, produces
  trajectories representative of real LC0 inference.
