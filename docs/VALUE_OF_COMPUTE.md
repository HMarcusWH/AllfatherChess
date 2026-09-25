# Prospective VERIFY value-of-compute calibration

> **M14-G1 distinction:** this document describes the PR #23 whole-run paired
> intervention family. M14-G1 adds a separate serve-compatible family in
> [STAGED_VERIFY.md](STAGED_VERIFY.md): one completed base VERIFY round followed
> by a fresh larger-budget `go` on the same managed processes and candidate
> universe. The two intervention families intentionally have different
> extractor/model identities and must not be substituted for one another.

## Status

PR #23 adds a new calibration family that asks a decision-relevant but still
strictly descriptive question:

> Under the same upstream experimental condition, did buying additional VERIFY
> budget change the frozen counterfactual decision?

This is intentionally narrower than chess correctness or strength.

```text
compute changed decision
        !=
compute improved decision
        !=
compute improved Elo
```

The current outward authority remains the Stockfish anchor.

## Why a new calibration family is required

The existing `controller.calibration.py` model estimates an engine's own
future leader-reversal risk from past-only features. That remains useful
historical routing evidence, but it is not the same target as:

```text
will another block of common-support VERIFY work change
the hybrid counterfactual proposal?
```

PR #23 therefore leaves `bucketed_reversal_risk_v3` unchanged and introduces
a separate model family:

```text
bucketed_verify_decision_change_v1
```

The two model kinds are deliberately incompatible at load time.

## Intervention design

The experiment uses actual stopped searches rather than simulated checkpoints.

For one position and one upstream state:

```text
VERIFY @ 64 nodes
  -> real search.complete / terminal bestmoves
  -> real counterfactual proposal

VERIFY @ 128 nodes
  -> real search.complete / terminal bestmoves
  -> real counterfactual proposal

VERIFY @ 256 nodes
  -> ...

VERIFY @ 512 nodes
  -> ...
```

The default research ladder is:

```text
64 -> 128 -> 256 -> 512 VERIFY nodes per verifier
```

The implementation does **not** take the first N nodes of a longer search and
pretend they are the bestmove that engine would have returned if stopped there.
That equivalence has not been established.

## Isolating VERIFY

`config/allfather.value.validation.json` is derived from the counterfactual
profile but intentionally disables REFINE.

The intervention is therefore:

```text
same EXPLORE
same candidates
same decision policy
different VERIFY node budget
```

rather than:

```text
different VERIFY budget
+ disagreement-dependent REFINE
+ possibly different downstream compute
```

A future dedicated REFINE experiment can measure that separate intervention.

The LC0 profile is still backend-light/random and remains non-strength-qualified.

## Upstream fingerprint

Two arms may be compared only when the system state upstream of VERIFY is the
same.

Each `VerifyBudgetPoint` records a SHA-256 over:

- position identity, variant, FEN/move history and position command;
- external UCI request;
- engine identities, binaries, arguments and options;
- partition method;
- EXPLORE owner-root allocation;
- EXPLORE stage commands/root restrictions;
- VERIFY nomination method;
- nominees by owner;
- common candidate roots;
- VERIFY participants;
- cross-feed policy;
- decision policy.

The fingerprint deliberately excludes:

- run id;
- timestamps;
- VERIFY node limit;
- later VERIFY terminal results;
- anchor result;
- future intervention labels.

If two arms have different fingerprints, `build_transition()` refuses to pair
them.

This matters because a changed EXPLORE nominee means the experiment changed
*before* the intended VERIFY-budget intervention.

## Budget point

`controller/value_of_compute.py` reconstructs one completed arm from the
sealed replay/VERIFY/cross-feed/counterfactual evidence.

The lower-arm feature payload includes only facts available when that arm
finished:

- declared VERIFY nodes;
- exact common candidate roots;
- EXPLORE nominees by owner;
- current terminal VERIFY vector;
- current counterfactual disposition/move/source owner;
- PRE_ANCHOR versus POST_ANCHOR;
- per-verifier observation count;
- per-verifier leader flips;
- per-verifier stable-run fraction;
- per-verifier PV persistence;
- whether each verifier retained its own EXPLORE nominee;
- stage elapsed time;
- source-native work value **with its semantics tag**;
- evidence-completeness state.

The existing shared `past_only_features()` definition is reused for
observation count, leader flips and stable-run fraction. PR #23 does not invent
a second training-only version.

## Heterogeneous work firewall

Source-native work counters remain per engine.

The implementation does not create:

```text
stockfish_nodes + reckless_nodes + lc0_work
```

or call such a sum "total compute".

The experimental intervention can truthfully say that the controller requested
another N VERIFY nodes from each verifier. Actual heterogeneous work remains
source-tagged until the measured resource-accounting milestone adds a common
physical CPU/GPU basis.

## Transition labels

For a causally eligible lower/upper arm pair, `ComputeTransition` records:

### decision_changed

Whether:

```text
(lower disposition, lower proposal move)
!=
(upper disposition, upper proposal move)
```

### proposal_emerged

The lower arm had no proposal and the upper arm had one.

### proposal_disappeared

The lower arm had a proposal and the upper arm had none.

### proposal_move_changed

Both arms proposed a move, but the move changed.

### terminal_vector_changed

At least one of:

```text
Stockfish VERIFY terminal bestmove
Reckless VERIFY terminal bestmove
LC0 VERIFY terminal bestmove
```

changed.

That label can be true even when the policy-level decision stayed
`NO_PROPOSAL_NONUNANIMOUS`.

## Full-budget labels

When a position/replicate has a complete ladder, later arms may attach:

### candidate_survived_full_budget

If the checkpoint already had a proposal, whether that same proposal move is
present at the highest observed budget.

When the checkpoint has no proposal this value is `null`, not `false`.

### decision_stabilized

Whether the checkpoint decision signature already equals every later observed
decision signature.

This is a future-facing label and may never be used as a lower-arm feature.

## Labels intentionally not claimed in PR #23

The extended build plan listed several future label families. They are not
silently filled with false values here.

Not implemented yet:

- `deep_reference_agrees`;
- `refine_changed_decision`;
- `crossfeed_changed_decision`;
- `game_outcome_delta`.

The current decision policy does not consume REFINE to select its proposal;
there is no qualified deep-reference adjudicator; there is no meaningful
"without cross-feed" decision arm; and paired game outcome experiments have not
yet been built.

## Feature / label firewall

Every transition carries two different content addresses:

```text
feature_digest = SHA256(lower-arm past-only feature payload)

label_digest   = SHA256(upper-arm outcome labels)
```

Changing a later arm can change the label digest while the lower feature digest
must stay byte-identical.

Tests exercise this explicitly.

## Right-censoring / missing arms

A missing upper arm is not:

```text
decision_changed = false
```

It is simply an unobserved transition and does not become a training row.

Likewise, full-budget survival labels are absent when the declared later budget
evidence does not exist.

## Dataset artifact

The sweep writes a content-addressed artifact:

```text
build/value-of-compute/
  voc-<digest>/
    dataset.json
```

The dataset binds:

- every source run id;
- position group / replicate;
- VERIFY node budget;
- upstream fingerprint;
- parent manifest hash;
- VERIFY manifest hash;
- cross-feed manifest hash;
- counterfactual artifact hash;
- lower-arm feature digest;
- future-label digest.

## Data split firewall

Multiple VERIFY budgets of the same chess position are near-identical examples.

Therefore PR #23 does **not** split by run id.

All repeats and budget arms associated with one `position_group` are assigned
to one partition.

With five or more distinct groups, deterministic modulo-5 assignment gives:

```text
60% train
20% calibration
20% holdout
```

For the ten-position default validation corpus this is exactly:

```text
6 train groups
2 calibration groups
2 holdout groups
```

No position group may straddle partitions.

A later larger strength corpus may require an even coarser opening-family split.

## Decision-change model

`controller/decision_calibration.py` fits an auditable empirical bucket model.

The v1 serving feature schema is deliberately small:

```text
VERIFY transition              n64->n128, n128->n256, ...
current proposal disposition
minimum verifier observation-count bucket
maximum verifier leader-flip bucket
minimum verifier stability bucket
```

The rest of the dataset remains available for later analysis.

The smaller serving schema is intentional: the validation corpus is too small
to support a high-dimensional interaction model honestly.

## Out-of-domain behavior

The model is fail-closed.

Unknown buckets and buckets below `min_support` return:

```text
in_domain = false
change_probability = 1.0
```

for routing purposes.

That conservative prior means missing evidence cannot be interpreted as
"additional compute probably will not matter".

## Evaluation

The calibration artifact records separately:

- train rows;
- calibration rows;
- holdout rows;
- Brier score;
- empirical change rate;
- mean predicted change rate;
- in-domain rate;
- reliability by bucket;
- support by VERIFY budget transition;
- exact position groups in each partition.

The calibration partition is currently diagnostic only; PR #23 does not tune a
hyperparameter against it and then re-fit before holdout. This keeps the first
model easy to audit.

## Real-engine contract

`scripts/value-of-compute-contract.py` runs one nonterminal position at two
real VERIFY budgets:

```text
64 nodes
128 nodes
```

It requires:

- two valid counterfactual artifacts;
- Stockfish anchor remains outward authority in both arms;
- identical upstream fingerprint;
- one causally eligible transition;
- exact additional node accounting;
- lower feature digest distinct from future label digest.

It does **not** require `decision_changed = true`. Whether more compute changes
the decision is evidence, not a test fixture expectation.

## Research sweep

`scripts/value-of-compute-sweep.py` defaults to:

```text
10 nonterminal frozen corpus positions
x 4 VERIFY budgets
x 3 repeats
= 120 controller runs
```

The full sweep is research data collection and is not normal CI.

Groups where EXPLORE/candidate state changes across budget arms are reported as
upstream-ineligible and are never cross-paired.

## Claim boundary

PR #23 may support a statement of the form:

> In held-out positions within the calibrated domain, increasing VERIFY from N
> to M nodes changed the frozen counterfactual decision at an observed rate R.

It does **not** support:

- that the later decision was better;
- that the earlier decision was wrong;
- that the calibrated change probability equals expected Elo value;
- that the current LC0 profile represents real neural inference;
- that VERIFY reserve fractions are optimal;
- that the new model is authorized for live stopping/routing yet.

Promotion into live resource routing remains a later, separately gated step.
