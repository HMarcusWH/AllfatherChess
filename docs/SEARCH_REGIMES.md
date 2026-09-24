# Search-regime classifier

## Status

M14-F adds a decision-inert, offline classifier over already-sealed Allfather
evidence.

It describes the current search situation. It does **not** route work, reserve
resources, dispatch engines, or authorize a move.

## Architecture

```text
sealed EXPLORE / VERIFY / REFINE / cross-feed evidence
                         |
                         v
                  RegimeObservation
                         |
              +----------+----------+
              |                     |
       structural rules       support calibration
              |                     |
              +----------+----------+
                         v
                RegimeClassification

                         X
                   no live routing
                   no move authority
```

The classifier consumes only evidence that already exists before M14-F. It does
not add a new solver phase.

## Multi-label vocabulary

A search may satisfy more than one regime at the same time.

```text
STABLE_CONVERGENT
TACTICAL_RUPTURE
CROSS_ENGINE_DISAGREEMENT
POLICY_DIFFUSE
ENDGAME_EXACT
TIME_CRITICAL
REFINEMENT_PRODUCTIVE
REFINEMENT_STALLED
OUT_OF_DOMAIN
```

Every regime is returned with one of:

```text
ACTIVE
INACTIVE
UNSUPPORTED
OUT_OF_DOMAIN
```

M14-F does not force one winning regime.

## Supported v1 semantics

### STABLE_CONVERGENT

Active only when the existing VERIFY analysis reports:

```text
RELOCK_OBSERVED
```

That means all three completed verifier trajectories ended on one move and each
entered a terminal uninterrupted suffix on that move.

It is not a correctness certificate.

### CROSS_ENGINE_DISAGREEMENT

Active when the completed VERIFY terminal vector contains more than one move.

The evidence preserves whether the pattern is:

```text
two_one
all_different
```

### TACTICAL_RUPTURE

Active when at least one completed typed source contains a native
`kind=mate` evaluation whose semantics namespace matches that source family.

There is no cp/Q/WDL threshold conversion.

### REFINEMENT_PRODUCTIVE

Active when a clean recursive nomination actually instantiated at least one
recorded M14-D recursive REFINE expansion that reached a completed or terminal
state.

This means additional structurally admissible search work was produced. It does
not mean that the chess answer improved.

### REFINEMENT_STALLED

The v1 definition is intentionally narrow.

It is supported only for a completed recursive REFINE profile with
`max_depth > 2`.

It is active only when:

- at least one completed non-terminal root REFINE target existed;
- no recursive expansion was recorded;
- no explicit depth cap, expansion cap, resource denial,
  decision/cancellation boundary, or similar bounded-stop reason explains the
  absence of deeper work.

A depth-two profile is therefore never called stalled.

## Deliberately unsupported v1 hypotheses

### POLICY_DIFFUSE

Current typed evidence exposes ordinal MultiPV rank, not a qualified native
policy distribution.

Rank is not policy entropy.

M14-F therefore returns `UNSUPPORTED`.

### ENDGAME_EXACT

The current controller evidence plane contains no qualified tablebase-exactness
fact.

Piece count, evaluation size, or apparent engine confidence are not substitutes.

M14-F therefore returns `UNSUPPORTED`.

### TIME_CRITICAL

The external UCI request preserves `movetime`, clocks, increments,
`movestogo`, nodes, and other request facts.

No calibrated threshold currently establishes when those facts become
"time-critical".

M14-F therefore records request mode but returns `UNSUPPORTED`.

## RegimeObservation

The immutable observation contains:

- run / generation / position identity;
- candidate-root set;
- M14-E adapter-evidence digest;
- source manifest hashes;
- completed VERIFY terminal vector and structural pattern;
- existing RELOCK status/fraction;
- per-verifier past-only observation count, leader flips, stable-run fraction,
  and PV persistence;
- source-native mate-alarm family mask;
- M14-D recursive REFINE structure and bounded-stop reasons;
- external request mode/limits.

The observation does not contain:

- counterfactual decision output;
- `DecisionAuthorization`;
- outward anchor bestmove;
- game result;
- later higher-budget result;
- cross-engine numeric score.

This prevents a future router from becoming circular:

```text
decision -> regime -> router -> decision
```

## Support calibration

`controller/regime_calibration.py` implements:

```text
regime_support_v1
```

It is a support/domain model, not a supervised truth classifier.

The bucket uses only coarse structural facts:

- VERIFY terminal pattern;
- RELOCK status;
- native mate-alarm family mask;
- candidate-count bucket;
- REFINE structural state;
- request mode.

The model records row support and independent position-group support.

Unknown buckets, low row support, or insufficient independent position groups
fail closed as out-of-domain.

Repeated runs of one position are assigned to one partition. Splitting by
run-id is forbidden.

## Dataset and sweep

`scripts/regime-sweep.py` reads sealed replay directories and writes:

```text
build/regimes/
  regime-dataset-<digest>/
    dataset.json
```

With `--fit`, it also writes:

```text
build/regime-calibration/
  regime-support-<digest>/
    model.json
```

The sweep launches zero engines.

## Contract

`scripts/regime-contract.py` proves:

- multi-label structural classification;
- RELOCK-only stable convergence;
- disagreement detection;
- native mate-alarm tactical rupture;
- recursive REFINE productivity;
- unsupported hypotheses remain unsupported;
- unseen structural buckets fail closed as out-of-domain.

The sealed-source extraction path is additionally exercised in
`tests/controller/test_regimes.py`.

## Authority boundary

M14-F does not modify:

- `controller.routing`;
- `controller.budget`;
- `controller.shadow`;
- runtime configuration;
- `DecisionAuthorization`;
- UCI output authority;
- engine code;
- telemetry schema.

M14-G may later consume a regime classification as one typed input to route
valuation. A regime label alone never authorizes work or a move.

## Claim boundary

M14-F does not establish:

- chess correctness;
- Elo improvement;
- optimal routing;
- optimal thresholds;
- tactical-alarm predictive value;
- REFINE value;
- policy entropy;
- exact-endgame status;
- time-critical status;
- strength superiority.
