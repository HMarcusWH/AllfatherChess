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
| `horizon_observed` | whether the requested horizon fitted inside the observed trajectory |
| `later_pv_changed` | its principal variation differed at the end |
| `stable_to_end` | its leader never changed again |
| `reversal_within_horizon` | its leader changed within the next horizon fraction |
| `work_at_checkpoint` | engine-native work spent, with its semantics tag |
| `first_discoverer_of_anchor_move` | which authorized stream first named the anchor's final move |

`later_leader_changed = true` means the engine changed its mind. It does not
mean the earlier move was wrong.

### Buckets are scoped to one solver family, and the anchor never trains

A bucket key carries the solver family it describes: `lc0|n3|s3|f0`. Without
that scope, support pooled across every worker, so observations of an
unrestricted Stockfish anchor could satisfy the support floor and supply a low
risk estimate for stopping an LC0 worker that had almost no evidence of its own.

Anchor rows are excluded from training entirely. The router only ever decides
whether to stop a *shadow worker*; an anchor row trains a decision that is never
made. Streams the manifest marks `contract_validatable: false` are likewise
ineligible: dropped events and failed adapter translation both remove
observations, and a removed leader flip reads as stability.

**What this exposed.** Once pooling stopped, a 36-run sweep fits only ~150-180 rows
across 8 buckets, and every well-supported bucket is `lc0|…`. The alpha-beta
shadow workers finish their node-limited stages almost immediately and
contribute very few labelled checkpoints, while the backend-light LC0 profile
produces many. So this repository currently has **no meaningful calibration
evidence for Stockfish or Reckless workers at all** — and previously it was
authorizing stops for them from LC0-derived buckets. The router now refuses
those, which is correct, and closing that gap is a data-collection problem, not
a threshold problem.

**And it inverted the fitted direction.** Under the scoped fit, the two buckets
with enough support to serve a decision order the opposite way to the one the
routing gate assumes:

| bucket | meaning | support | fitted risk |
| --- | --- | ---: | ---: |
| `lc0\|n3\|s3\|f0` | has never flipped its leader | 32 | 0.265 |
| `lc0\|n3\|s0\|f1` | flipped recently, current run < 25% of history | 28 | 0.033 |

A worker that has never flipped is measured as roughly 8x *more* likely to flip
within the next horizon than one that just flipped. The v2 pooled fit read the
other way round, and that reading did not survive scoping.

This is why the sweep authorizes zero stops, and the reason is structural rather
than marginal: `s0|f1` clears `stop_max_reversal_risk = 0.05` but fails
`stop_min_stability_fraction = 0.6`, and `s3|f0` clears the stability gate but
fails the risk gate. No well-supported bucket can satisfy the conjunction. The
thresholds were deliberately left alone; moving either one to manufacture stops
would be fitting the policy to the evidence it is supposed to be tested against.

Whether that inverted ordering is a real property of MCTS leader dynamics or an
artifact of *when* each bucket is populated is recorded as open in
`docs/CLAIM_LEDGER.md`; the past-only feature vector carries no checkpoint
position, so this fit cannot distinguish them.

### Right-censored horizons are unlabelled

If the requested horizon runs past the end of the observed trajectory, the
search simply stopped: "no reversal happened" is not an observation. v1 labelled
those rows `False`, which filled exactly the late, settled buckets — the ones
that authorize live suppression — with guaranteed negatives and understated real
reversal risk. v2 emits `None` and `horizon_observed: false`, and those rows
never become training rows.

The correction is visible in the numbers. On the 36-run sweep whose artifact
this tree carries, the pipeline produces 1260 checkpoints, of which only 360
survive right-censoring as labelled observations. Scoping then removes the 210
anchor rows (the router never decides whether to stop the anchor), leaving 150
training rows. The in-domain rate falls with the row count, because the
remaining evidence is thinner and more of it is honestly out of domain. That is
the calibration getting smaller and more conservative, not worse.

The `contract_validatable` eligibility filter removes 0 further rows on this
sweep: every stream in it translated cleanly. The guard is there for the sweeps
where that is not true, and on this evidence it is inert rather than load-bearing.

## Calibration model

`bucketed_reversal_risk_v3`, extracted by `residuals-v3`, estimates the probability that an engine's own
leader still reverses within the horizon, from **past-only** features:

```text
observation_count     how many primary-line updates have been seen
leader_flips          how often the leader has changed so far
stable_run_fraction   how long the current leader has held
```

All three come from `common.residuals.past_only_features`, which takes one
input: the ordered primary-line moves observed so far. Offline extraction and
live routing call that same function on the same input, so an online decision
and a later offline audit cannot land in different buckets.

### Why there is no time-relative feature

v1 used `elapsed_fraction`, and it was broken in a way that is worth recording.
Offline its denominator was the observed replay span; online it was the declared
wall envelope. `stable_run_fraction` and `leader_flips` had the same problem from
the other direction: training computed them over ten sampled checkpoint leaders,
serving over every raw primary update. A search with forty updates therefore
produced a flip count over ten points during training and over forty points
during serving. The buckets were not comparable, so an "in-domain, low-risk"
verdict was not in fact calibrated for the state it was being asked about.

A feature that cannot be computed identically in both paths cannot serve a
calibration, so the time-relative feature was removed rather than patched.
`elapsed_fraction` is still recorded on each routing observation for the audit
trail; it is simply not a model input.

Bucketing is deliberately coarse and auditable. `stable_run_fraction` is cut
into quartiles, `observation_count` into `{<=2, <=5, <=11, 12+}`, and
`leader_flips` into `{0, 1-2, 3+}`. The key is prefixed with the owning solver
family, giving keys like `lc0|n3|s3|f0`. Risk is the Laplace-smoothed empirical
rate in the bucket.

The family prefix is a **scope, not a feature**: it names the population the
bucket describes rather than a property being regressed on. Without it, an
alpha-beta worker's observations could satisfy the support floor and supply a
low risk estimate for stopping an MCTS worker that has almost no evidence of
its own — which is exactly what v2 did.

Fail-closed behavior:

- an unknown bucket returns the conservative prior and `in_domain: false`;
- a bucket below `min_support` returns `max(bucket risk, prior)` and
  `in_domain: false`;
- a model fitted against a different `extractor_version` or feature set is
  refused at load;
- fitting from zero rows is refused.

Evaluation is out of sample on a split **by run identity**, so a run cannot
straddle the split. Every fourth run, by sorted run id, is held out. A stride
rather than a per-run hash is used deliberately: hashing makes the *size* of the
holdout a random variable, and at ten runs it leaves nothing held out about 5.6%
of the time — which, given the `calibration_validated` routing gate, would
silently make an otherwise fine model unable to authorize anything. The stride
guarantees a non-empty holdout from two runs upward and, because run ids are
timestamp-prefixed, interleaves it across the collection period. A single run
still yields no holdout, which is correct: there is nothing to hold out from one
run.

The artifact records the Brier score, the in-domain rate, and a reliability
table of predicted versus observed rates per bucket.

## Provenance

A derived artifact records each source run id, the manifest sha256, and each
stream sha256. Before extracting anything, `build_derived_artifact` re-verifies
every bundle: provenance is only meaningful if the bytes still match the
manifest the provenance points at.

Both ids are content addresses over everything that determines the artifact. The
derived id covers the extractor version, the checkpoint fractions, `top_k`,
`horizon_fraction`, and the source manifest and stream hashes. The model id
covers its provenance, every hyperparameter, the train/test sizes, and the
fitted bucket contents — so two models with the same row count but opposite
labels cannot collide on one `model.json`.

Per-stage evidence is keyed by search id, because active routing can dispatch
several stages to one worker and keying by instance would let a later stage
overwrite every earlier one. `summaries_by_instance` and
`counterfactual_labels_by_instance` expose the latest stage for worker-level
views.

## Claim status

- **MEASURED**: the trajectories and labels observed in the recorded runs.
- **CALIBRATED**: the bucketed reversal-risk relationship fitted from them.
- **OPEN**: whether that relationship generalizes beyond the positions, time
  controls, hardware, and engine builds it was fitted on; whether reversal risk
  predicts anything about move quality; and whether the LC0 validation profile,
  which is backend-light and explicitly not strength-qualified, produces
  trajectories representative of real LC0 inference.
