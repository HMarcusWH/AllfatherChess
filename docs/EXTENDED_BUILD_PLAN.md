# AllfatherChess Extended Build Plan

**Status:** Post-PR #18 architecture and implementation plan  
**Date:** 2026-09-22  
**Scope:** Expand the existing `docs/BUILD_PLAN.md` from the current M12 control/resource milestone through cross-feed, decision authority, native integration, governed policy evolution, and the final equal-resource strength campaign.

This document is intentionally more detailed than `docs/BUILD_PLAN.md`. It does not replace the existing build plan, telemetry contracts, shard ledgers, replay contracts, or claim ledger. It specifies the proposed build sequence from the current repository state after PR #18.

---

## 1. Final objective

Build one externally visible UCI chess engine in which Stockfish, Reckless, and LC0 are heterogeneous solver backends under one controller that can allocate search, verification, refinement, and decision compute more effectively than any constituent engine receives under the same declared total resource envelope.

The target claim remains empirical:

> Allfather must beat each constituent engine in statistically credible head-to-head testing while staying inside the same measured resource envelope.

The controller is successful only if the complete system produces stronger chess per unit of compute. Running three strong engines at once and hiding their combined cost does not satisfy the objective.

The final product must therefore satisfy all of these simultaneously:

- one external UCI identity;
- one controller-wide resource envelope;
- explicit search-space ownership;
- no accidental EXPLORE overlap;
- deliberate duplicate work only under tagged VERIFY / REFINE / adjudication phases;
- engine-native scores remain source-typed;
- no raw cross-engine score averaging;
- replayable evidence and route history;
- a decision path that can eventually differ from the unrestricted Stockfish anchor;
- fail-closed fallback whenever evidence is insufficient;
- real backend qualification for Stockfish, Reckless, and LC0;
- measured rather than merely estimated resource accounting for the strength claim;
- isolated ablations for every proposed improvement;
- immutable experiment manifests and no hindsight leakage;
- offline, governed policy evolution rather than live self-mutation;
- external strength promotion only after reproducible statistical evidence.

---

## 2. Current repository baseline after PR #18

The current codebase already has the control substrate required to begin the actual hybrid-strength work.

### 2.1 Implemented control layers

The repository currently provides:

- a single Allfather UCI frontend;
- managed Stockfish, Reckless, and LC0 processes;
- a separate unrestricted Stockfish anchor and restricted Stockfish shadow worker;
- deterministic root legality from Stockfish `go perft 1`;
- `RootShardLedger` v1 for pairwise-disjoint root EXPLORE ownership;
- telemetry v1 and backend-specific adapters;
- replay manifests and raw search trajectories;
- residual and counterfactual analysis;
- active observation routing;
- explicit common-support VERIFY;
- derived COMPARE / descriptive RELOCK;
- `PrefixShardLedger` v2;
- one-level descendant REFINE;
- reservation-before-dispatch budgeting for VERIFY / REFINE / oracle work;
- controller overhead accounting;
- global CPU/GPU/wall envelope validation;
- separate raw, derived, routing, verification, and refinement artifacts.

### 2.2 Current authority boundary

The most important current limitation is deliberate:

> The unrestricted Stockfish anchor is still the sole outward bestmove authority.

Everything added through PR #18 can observe, partition, verify, refine, reserve, deny, charge, and audit compute, but it still cannot improve the outward move because specialist results do not yet have chess decision authority.

This is exactly where the next programme begins.

### 2.3 Current empirical limitations

The following remain open and must not be hidden by future work:

- LC0 validation remains backend-light/random and is not strength-qualified.
- CPU consumption is still derived from stage wall duration × configured thread count rather than independently measured process CPU time.
- GPU consumption is estimate-based when declared, not measured accelerator occupancy.
- current reversal-risk calibration measures self-reversal/stability, not objective move correctness;
- Stockfish-vs-Reckless and Stockfish-vs-LC0 evaluation scales are not interchangeable;
- VERIFY agreement is not correctness;
- descriptive RELOCK is not a chess certificate;
- current specialist reserve fractions are policy choices, not proven optima;
- REFINE is one descendant shell only;
- cross-feed does not yet exist;
- no hybrid decision has been prospectively frozen and scored against later outcomes;
- no equal-resource strength claim exists.

---

## 3. Architectural doctrine for the remaining build

The remaining build should preserve the pattern already established by the first eighteen PRs:

```text
OBSERVE
  ↓
NOMINATE
  ↓
AUTHORIZE
  ↓
RESERVE
  ↓
DISPATCH
  ↓
SETTLE
  ↓
COMPARE
  ↓
DECIDE OR ABSTAIN
  ↓
AUDIT
```

Three additional laws become mandatory once the controller is allowed to affect the outward move.

### 3.1 Resource authority != decision authority

A worker being allowed to spend compute does not mean its proposed move may become the outward move.

The code should keep separate objects for:

```text
ResourceAuthorization
DecisionEvidence
DecisionAuthorization
```

This prevents a scheduling decision from silently becoming a chess decision.

### 3.2 Candidate transfer != truth transfer

Cross-feed initially transfers only typed information such as:

- move identity;
- source engine;
- source search phase;
- source rank;
- PV prefix;
- depth / nodes / visits / time;
- engine-native confidence/evaluation with semantic tag;
- tactical-alarm or disagreement descriptor;
- provenance and budget identity.

It does **not** transfer a universal scalar meaning such as:

```text
LC0 +0.61 == Stockfish +37cp
```

unless an independently validated calibration contract explicitly licenses that conversion.

### 3.3 A hybrid decision must be frozen before its outcome is known

The forecasting material reviewed for the wider Allfather architecture is directly useful as experiment discipline:

```text
evidence available at decision time
→ frozen hybrid decision
→ immutable manifest
→ later deeper reference / game outcome
→ score
```

Never:

```text
later outcome
→ alter what the controller "would have decided"
```

This is necessary for honest value-of-compute and strength calibration.

---

## 4. What transfers from the wider Allfather project — and what does not

The wider Allfather BI architecture supplies useful controller patterns, but none of those materials automatically proves chess semantics.

### 4.1 RACR

Useful transfer:

- residual-aware compute routing;
- observe → nominate → authorize separation;
- trust regions;
- abstention / buy-more-compute;
- route audit;
- cost-aware refinement;
- conditional computation.

Do not transfer:

- domain-specific thresholds;
- a claim that a residual proves move incorrectness;
- any theorem whose assumptions have not been established for chess search.

### 4.2 Strategic Core

Useful transfer:

- multiple candidate courses of action rather than one monolithic plan;
- explicit objective and constraint binding;
- replanning after rupture;
- separating proposal generation from authorization;
- anti-goal-drift checks.

Chess mapping:

```text
course of action
≈
candidate move + continuation / PV family
```

This is a controller design analogy, not a proof that chess planning and the business-strategy formalism are mathematically identical.

### 4.3 MCM-HMWH

Useful transfer:

- rival-hypothesis maintenance;
- first-break detection;
- distinguish observation from diagnosis;
- record why one explanation displaced another;
- preserve counterfactual alternatives.

Chess mapping:

```text
rival hypothesis
≈
candidate root / PV family

first break
≈
first budget/depth checkpoint where candidate ordering materially diverges
```

### 4.4 Forecasting

Useful transfer:

- freeze pre-outcome predictions;
- historical blind / prospective distinction;
- immutable generations;
- explicit scorer binding;
- denominator discipline;
- calibration separated from causal claims;
- later outcome does not rewrite earlier forecast.

Chess mapping:

Before spending specialist compute, the router may predict:

```text
probability / class that additional compute changes the final decision
expected regret reduction
expected value of VERIFY
expected value of REFINE
```

Those predictions can then be evaluated prospectively after the full search or game result is known.

### 4.5 Permansson

Useful transfer:

- persistent-regime classification;
- distinguish transient movement from durable regime;
- evaluate the long-run effect of repeated policy choices.

Possible chess use:

- stable search regime;
- tactical-rupture regime;
- broad-policy-uncertainty regime;
- endgame / tablebase regime;
- high-disagreement regime;
- converging regime.

Permansson is **not** a next-move evaluator and should not be used as one.

### 4.6 Epistemic Compiler / AEC

Useful transfer:

- the leaves being individually supported does not make the composition supported;
- frozen boundaries;
- anti-Frankenstein composition;
- provenance and temporal coherence;
- countermodel floors;
- deterministic replay;
- complete-world / evidence / certificate distinctions.

Chess use:

A final decision certificate may require all of these simultaneously:

```text
candidate legality
budget compliance
evidence completeness
source identity
phase identity
common-support comparison
freshness
no post-decision leakage
decision policy version
```

No AEC runtime dependency is required for the first chess implementation. The chess controller should copy the discipline, not import a large unrelated stack.

### 4.7 Intelligence compression

Useful transfer:

- compress large histories into typed state only when the compressed representation preserves the information required by the next decision;
- retain source pointers and the ability to reopen detail.

Chess use:

- bounded candidate summaries;
- PV-family summaries;
- regime summaries;
- route-state summaries.

The controller must never compress away the raw replay evidence needed to audit a decision.

### 4.8 Self-evolving architecture

Useful transfer:

- candidate generation is not promotion;
- holdout evaluation;
- immutable policy generations;
- no self-grading;
- rollback;
- lineage;
- promotion only after separate evidence.

Chess use:

```text
telemetry
→ policy candidate
→ frozen benchmark
→ paired ablation
→ holdout
→ match/SPRT evidence
→ promotion
```

The live engine must not rewrite its own policy because a recent game went well.

### 4.9 Artificial Life

Useful transfer is limited to autonomy accounting:

- distinguish internal governed adaptation from hidden manual rescue;
- record indispensable human intervention;
- do not call a manually repaired system self-improving.

No biological-life claim belongs in the chess engine.

### 4.10 Transition Path material

Potentially useful only as a later modeling analogy for continuation/path families and path-conditioned risk.

A principal variation is not automatically a stochastic transition path, and no transition-path theorem should be imported into chess without a separately validated model.

### 4.11 Toolbox / One-Field / Weil-CCM research

Useful transfer:

- isolate the smallest unresolved defect;
- refine only where the defect persists;
- use explicit witnesses;
- test whether a low-dimensional detector is sufficient before buying full computation.

Forbidden transfer:

- no Weil/CCM theorem becomes a chess pruning theorem by analogy;
- no One-Field abstraction becomes a search bound without a chess-specific derivation and tests;
- no mathematical source claim is promoted into chess strength evidence without independent qualification.

### 4.12 Universal Fetch / Universal Port / DeepSeek Harvest

These are not hot-path chess components.

They may later help with:

- importing external opening books;
- ingesting engine-match corpora;
- collecting external benchmark results;
- building reproducible research datasets;
- harvesting candidate heuristics for offline evaluation.

They must not sit inside the move-time critical path unless a later measured design justifies it.

---

## 5. Target end-state architecture

The intended final controller should look conceptually like this:

```text
                            UCI GUI
                               │
                               ▼
                       Allfather UCI shell
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Position / Game     │
                    │ Resource Envelope   │
                    │ Policy Generation   │
                    └──────────┬──────────┘
                               │
                    legal root universe
                               │
                               ▼
                    Root / Prefix Ledger
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
     Stockfish            Reckless               LC0
      EXPLORE              EXPLORE              EXPLORE
          │                    │                    │
          └───────────── typed evidence ───────────┘
                               │
                               ▼
                    Residual / Regime layer
                               │
                               ▼
                    Value-of-compute router
                               │
                   nominate / authorize
                  ┌────────────┼────────────┐
                  ▼            ▼            ▼
                VERIFY       REFINE      CROSS-FEED
                  └────────────┼────────────┘
                               ▼
                    Common candidate basis
                               │
                               ▼
                     Decision evaluator
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      evidence sufficient                evidence insufficient
              │                                 │
              ▼                                 ▼
      hybrid bestmove                    fallback anchor
              │
              └────────────────┬────────────────┘
                               ▼
                         outward bestmove
                               │
                               ▼
                       immutable audit
```

The final strength engine should not depend on all three solvers being equally active on every position. The controller should learn when each solver or specialist phase is worth its marginal cost.

---

## 6. Cross-feed design

Cross-feed is the next milestone because PR #18 has finally made specialist work accountable.

### 6.1 Cross-feed v1 should transfer candidates, not scores

The first safe cross-feed object should be approximately:

```python
CandidateHint(
    move,
    source_engine,
    source_role,
    source_phase,
    source_rank,
    pv_prefix,
    work,
    observed_ms,
    native_evaluation,
    native_semantics,
    provenance_ref,
)
```

The recipient may use the hint to nominate VERIFY/REFINE/adjudication work.

It may not directly reinterpret `native_evaluation` in another engine's scale.

### 6.2 Cross-feed cannot violate ownership

If LC0 discovers a move owned by LC0 during EXPLORE and Stockfish later examines that same root because of the hint, the second search is not EXPLORE. It must be tagged and charged as one of:

```text
VERIFY
REFINE
ADJUDICATE
```

The ownership ledger therefore remains true.

### 6.3 Cross-feed needs a no-effect shadow phase first

Before cross-feed can alter a move:

```text
actual outward = anchor bestmove
counterfactual hybrid = computed and frozen separately
```

This produces honest evidence about:

- how often the hybrid would differ;
- whether differences survive deeper reference search;
- whether differences improve game outcomes;
- cost per useful decision change;
- which source-engine hints matter;
- where cross-feed only adds noise.

---

# 7. Extended PR train

The PR numbers below continue the current merged sequence. Scope should remain narrow enough that every behavioral change can be ablated independently.

## PR #19 — Typed cross-feed substrate, shadow-only

### Purpose

Create the information-transfer plane without granting any new decision authority.

### Add

- `controller/crossfeed.py`
- `tests/controller/test_crossfeed.py`
- `scripts/crossfeed-contract.py`
- `docs/CROSS_FEED.md`
- `config/allfather.crossfeed.validation.json`

### Modify

- `controller/shadow.py`
- `controller/replay.py`
- `controller/runtime.py`
- `controller/routing.py`
- `tests/controller/test_shadow_runtime.py`
- `tests/controller/test_replay.py`
- `Makefile`
- relevant CI workflow(s)

### Implement

- immutable `CandidateHint`;
- `CrossFeedBundle`;
- source/phase/provenance binding;
- canonical move identity;
- deterministic ordering;
- duplicate-hint collapse without losing provenance;
- target-owner nomination;
- strict prohibition on raw score conversion;
- separate cross-feed artifact;
- shadow-only generation.

### Acceptance

- cross-feed produces no outward behavior change;
- EXPLORE ownership is unchanged;
- every re-search nomination is typed as non-EXPLORE;
- identical raw evidence creates identical cross-feed digest;
- malformed provenance fails closed;
- engine-native score semantics remain intact.

---

## PR #20 — Counterfactual hybrid decision laboratory

### Purpose

Compute what Allfather **would** choose if specialist evidence were allowed to influence the decision, while keeping Stockfish anchor outward authority.

### Add

- `controller/decision.py`
- `controller/counterfactual.py`
- `tests/controller/test_decision.py`
- `tests/controller/test_counterfactual.py`
- `scripts/counterfactual-decision-contract.py`
- `scripts/counterfactual-decision-sweep.py`
- `docs/DECISION_AUTHORITY.md`

### Modify

- `controller/shadow.py`
- `controller/verification_analysis.py`
- `controller/refinement.py`
- `controller/replay.py`
- `controller/routing.py`

### Core objects

```text
DecisionCandidate
DecisionEvidence
DecisionProposal
DecisionAuthorization
DecisionDisposition
CounterfactualDecision
```

### Initial policy

The safest first counterfactual policy is not a vote.

Use:

1. anchor candidate;
2. clean EXPLORE nominees;
3. common-support VERIFY evidence;
4. optional one-level REFINE evidence;
5. one final adjudication policy over a bounded common candidate set;
6. fallback to anchor whenever completeness requirements fail.

No raw cross-engine score average.

### Acceptance

- outward bestmove remains Stockfish anchor;
- counterfactual result is frozen before later reference labels;
- incomplete VERIFY/REFINE cannot silently become complete;
- policy version is included in the decision digest;
- replay regenerates the same counterfactual decision.

---

## PR #21 — Chess-specific value-of-compute labels and prospective calibration

### Purpose

Replace self-reversal-only calibration with labels that can answer whether additional compute was useful to the final chess decision.

### Add

- `controller/value_of_compute.py`
- `controller/decision_calibration.py`
- `tests/controller/test_value_of_compute.py`
- `scripts/value-of-compute-sweep.py`
- `docs/VALUE_OF_COMPUTE.md`

### Labels

Keep distinct:

```text
decision_changed
candidate_survived_full_budget
deep_reference_agreement
game_outcome_delta
verification_changed_adjudication
refinement_changed_adjudication
crossfeed_changed_adjudication
```

No single label is called `correct` unless its authority is explicitly defined.

### Required experiment discipline

- features frozen before the later label;
- no final-depth evidence in an earlier checkpoint feature vector;
- train/calibration/holdout separation;
- position-family separation where needed;
- engine version and hardware identity pinned;
- deeper-engine labels explicitly marked proxy labels;
- game outcomes separately tracked.

### Acceptance

The router can answer a bounded question such as:

> Under this calibrated domain, did another 100 ms / N nodes of VERIFY historically change the eventual decision often enough to justify its cost?

It still cannot claim that the resulting decision is globally optimal.

---

## PR #22 — Strength-qualified LC0 profile

### Purpose

Remove the largest current backend qualification blocker.

### Add / modify

- pin a real LC0 network in `vendor.lock.json` or a dedicated locked artifact section;
- add a strength hardware profile under `config/`;
- extend LC0 adapter configuration for the selected real backend;
- add `scripts/lc0-strength-profile-contract.py`;
- add qualification fixtures and documentation.

### Requirements

Record:

- exact LC0 commit/tree;
- exact network SHA-256;
- backend type;
- GPU model;
- driver/runtime versions;
- batch settings;
- thread settings;
- deterministic/reproducibility limits;
- warmup policy;
- memory limits.

### Acceptance

LC0 is no longer represented by the random/backend-light validation profile in any strength claim.

The backend-light profile remains available for fast controller CI.

---

## PR #23 — Measured process resource accounting

### Purpose

Upgrade equal-resource evidence from configured estimates toward physical measurement.

### Add

- `controller/resource_measurement.py`
- `adapters/resource/`
- `tests/controller/test_resource_measurement.py`
- `scripts/resource-accounting-contract.py`
- `docs/RESOURCE_ACCOUNTING.md`

### Modify

- `controller/budget.py`
- `controller/routing.py`
- `controller/shadow.py`
- active configuration profiles.

### Measure where available

- process CPU time;
- child-process CPU time;
- wall time;
- peak / sampled RSS;
- accelerator utilization or device time under the chosen qualification platform;
- GPU memory;
- controller process CPU;
- synchronization / IPC overhead.

### Architecture

The budget ledger should preserve two axes:

```text
declared / reserved budget
measured actual consumption
```

Never overwrite one with the other.

### Claim rule

If accelerator measurement is unavailable, the run may remain useful for development but cannot support the strongest equal-resource claim.

---

## PR #24 — Active hybrid decision authority v0

### Purpose

Permit a tightly bounded subset of searches to return a hybrid move rather than the anchor move.

### Safety model

Stockfish anchor remains mandatory and becomes fallback/reference rather than sole authority.

### Initial authorization conditions

A hybrid move may be emitted only if:

- bounded outward search request;
- legal move;
- all required evidence artifacts complete;
- active resource envelope valid;
- decision policy in calibrated domain;
- common candidate basis valid;
- no open reservation;
- no stale backend generation;
- adjudication finished before the deadline;
- no fail-closed condition triggered.

Otherwise:

```text
bestmove = anchor bestmove
```

### Modify

- `controller/uci_frontend.py`
- `controller/runtime.py`
- `controller/shadow.py`
- `controller/decision.py`
- `controller/budget.py`
- `controller/routing.py`
- `tests/controller/test_uci_frontend.py`
- `tests/controller/test_shadow_runtime.py`

### Add

- `scripts/active-hybrid-decision-contract.py`
- `config/allfather.hybrid.validation.json`

### Acceptance

- hybrid authority is opt-in;
- anchor fallback remains deterministic;
- decision and resource authorization remain distinct;
- every outward hybrid bestmove has a complete decision certificate;
- failure of any specialist never silently promotes another specialist;
- no move is emitted after deadline.

---

## PR #25 — Multi-level recursive REFINE

### Purpose

Move beyond the current one-child-shell refinement while preserving prefix-free ownership.

### Modify

- `controller/prefix_shards.py`
- `controller/refinement.py`
- `common/prefix_dispatch.py`
- `controller/budget.py`
- `controller/routing.py`
- `tests/controller/test_prefix_shards.py`
- `tests/controller/test_refinement.py`

### Add

- `controller/refinement_policy.py`
- `tests/controller/test_refinement_policy.py`
- `scripts/recursive-refinement-contract.py`

### Rules

- recursion depth is budgeted;
- split only SEALED eligible leaves;
- frontier remains prefix-free;
- exact child legality required;
- every deeper split has explicit nomination evidence;
- no recursive work after outward decision boundary;
- ownership inheritance and transfer remain atomic;
- routing may stop refinement at any depth.

### Goal

Turn REFINE into a genuine zoom mechanism:

```text
large unresolved region
→ identify persistent defect
→ split only that region
→ repeat until resolved or budget exhausted
```

---

## PR #26 — Engine-specific cross-feed adapters

### Purpose

Let each backend consume typed information in the way that matches its native search paradigm.

### Initial subprocess-safe adapters

Examples:

- common candidate-set adjudication through `searchmoves`;
- candidate subset VERIFY;
- PV-prefix REFINE;
- tactical-alarm nomination;
- source-rank-informed budget priority.

### Do not yet claim

- LC0 policy directly rewrites Stockfish native move ordering;
- Stockfish tactical score directly rewrites LC0 Q values;
- engine-native trees are unified.

Those require native integration or separately validated translation semantics.

### Add

- `adapters/crossfeed/stockfish.py`
- `adapters/crossfeed/reckless.py`
- `adapters/crossfeed/lc0.py`
- corresponding tests.

### Acceptance

Every adapter exposes what it consumed and how the hint affected dispatch. No hidden heuristic injection.

---

## PR #27 — Search-regime classifier

### Purpose

Use current evidence to classify what type of search state the controller is in, then condition routing without pretending that one policy is optimal everywhere.

### Add

- `controller/regimes.py`
- `controller/regime_calibration.py`
- `tests/controller/test_regimes.py`
- `scripts/regime-sweep.py`
- `docs/SEARCH_REGIMES.md`

### Candidate regimes

These are hypotheses to calibrate, not frozen truth:

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

### Inputs

Prefer scale-free / source-typed evidence:

- leader turnover;
- top-k churn;
- PV-prefix divergence;
- VERIFY triad state;
- unresolved-set size;
- work-after-stability ratio;
- tablebase/terminal evidence;
- prefix-refinement persistence;
- source-specific rank entropy where valid.

### Output

A regime may change what work is nominated. It does not itself authorize a move.

---

## PR #28 — Decision-router v1

### Purpose

Unify resource routing, regime state, calibrated value-of-compute, and hybrid decision authorization into one typed controller loop.

### Proposed loop

```text
observe
→ summarize
→ classify regime
→ identify unresolved decision mass
→ generate route proposals
→ estimate value/cost
→ authorize one bounded route
→ reserve
→ dispatch
→ settle
→ update evidence
→ repeat or decide
```

### Modify

- `controller/routing.py`
- `controller/budget.py`
- `controller/decision.py`
- `controller/regimes.py`
- `controller/refinement_policy.py`
- `controller/shadow.py`

### Required actions

```text
CONTINUE_OWNER
VERIFY_SET
REFINE_PREFIX
ADJUDICATE_SET
EXTEND_ANCHOR
STOP_OWNER
FALLBACK_ANCHOR
ABSTAIN_BUY_COMPUTE
DECIDE_HYBRID
```

### Acceptance

One route action per authorization point; no action has hidden side effects outside its declared scope.

---

## PR #29 — Structured IPC / local protocol

### Purpose

Measure and remove avoidable subprocess/UCI parsing overhead before embedding full engine libraries.

### Add

A versioned structured local protocol for:

- search request;
- root restriction;
- telemetry frames;
- cancellation;
- result;
- resource metadata.

### Strategy

Do not rewrite all three engines at once.

First wrap one backend behind a structured adapter and run A/B comparisons against the UCI adapter.

### Promotion condition

Native/structured transport is retained only if it improves:

- latency;
- control granularity;
- telemetry fidelity;
- cancellation behavior;
- compute efficiency;

without changing chess semantics unexpectedly.

---

## PR #30 — Selective native engine integration

### Purpose

Embed only the interfaces whose measured value justifies losing process isolation.

### Candidate order

1. Stockfish restricted search / root ordering control;
2. Reckless alpha-beta hooks;
3. LC0 policy/search hooks.

### Potential native capabilities

- native candidate ordering;
- cheaper partial-search continuation;
- direct node/visit counters;
- richer stop conditions;
- subtree/prefix targeting;
- lower-latency telemetry;
- policy hints;
- tactical alarms.

### Hard requirement

Each native adapter must have a compatibility harness proving its externally relevant behavior against the corresponding process adapter before it can replace it.

Process adapters remain the fallback/reference implementation.

---

## PR #31 — Offline policy evolution laboratory

### Purpose

Allow Allfather to improve its controller policy without live self-mutation.

### Add

- `controller/policy.py`
- `controller/policy_registry.py`
- `scripts/policy-candidate-evaluate.py`
- `scripts/policy-promotion-contract.py`
- `tests/controller/test_policy_registry.py`
- `docs/POLICY_EVOLUTION.md`

### Policy candidates may tune

- reserve fractions;
- stage budgets;
- regime thresholds;
- candidate-set widths;
- verify triggers;
- refinement triggers;
- stop floors;
- adjudication budget;
- engine priority;
- cross-feed eligibility.

### Immutable lifecycle

```text
ACTIVE_POLICY_n
→ telemetry
→ CANDIDATE_POLICY_n+1
→ frozen train/calibration
→ holdout
→ paired games / SPRT where applicable
→ promotion decision
→ ACTIVE_POLICY_n+1
```

Never:

```text
recent win
→ mutate active thresholds in-place
```

### Anti-self-grading

The candidate policy does not choose its own scorer, holdout, promotion threshold, or baseline after seeing results.

---

## PR #32 — Strength campaign infrastructure

### Purpose

Make strength qualification reproducible and separate from development CI.

### Add

- `scripts/strength-campaign.py`
- `scripts/match-runner.py`
- `scripts/sprt.py` or equivalent statistical harness;
- `docs/STRENGTH_QUALIFICATION.md`;
- machine-readable match manifests;
- fixed opening suites;
- fixed hardware profiles;
- fixed time/node/resource controls.

### Required arms

At minimum:

```text
Stockfish baseline
Reckless baseline
LC0 baseline
Allfather anchor-only
Allfather observation-only
Allfather VERIFY-only
Allfather REFINE-only
Allfather cross-feed counterfactual policy
Allfather active hybrid
Allfather active hybrid minus each major component
```

### Required resource regimes

- fixed nodes where meaningful;
- fixed wall time;
- fixed CPU budget;
- fixed GPU budget / hardware allocation;
- mixed CPU+GPU declared envelope;
- tournament-style time controls.

### Required statistical discipline

- paired openings/colors;
- seed/config freeze;
- no tuning on confirmatory matches;
- explicit stopping rule;
- confidence interval / SPRT reporting;
- draw-rate reporting;
- crashes/time losses counted;
- all exclusions disclosed.

---

## PR #33 — External qualification and release candidate

### Purpose

Turn a development engine into a defensible release candidate.

### Requirements

- all three backend strength profiles qualified;
- measured resource accounting available on the qualification platform;
- no unresolved controller reservation leaks;
- no orphan processes;
- deterministic replay for internal controller artifacts where applicable;
- hybrid policy frozen;
- strength campaign positive against each declared baseline;
- regression suite green;
- UCI compatibility tested against real GUIs/tournament harness;
- licensing/provenance review complete;
- claim ledger updated with exact achieved claims and explicit nonclaims.

The release claim must state the exact:

- hardware;
- network/model versions;
- engine commits;
- controller policy generation;
- time/resource control;
- benchmark/match protocol;
- statistical result.

No generic “best chess engine in the world” claim follows from one local match suite.

---

# 8. Final decision architecture

The controller should eventually distinguish four types of conclusion.

## 8.1 Observation

Example:

```text
LC0 currently ranks e4 first.
```

## 8.2 Derived analysis

Example:

```text
The three engines disagree on the leader after synchronized VERIFY.
```

## 8.3 Resource authorization

Example:

```text
Spend another 120 ms of REFINE on prefix P.
```

## 8.4 Chess decision authorization

Example:

```text
Emit Nf3 rather than the unrestricted anchor's e4.
```

The code should make it impossible to confuse these by type or artifact location.

---

# 9. Decision evidence contract

A future hybrid outward decision should carry enough information to reconstruct why it happened.

Conceptual record:

```text
HybridDecisionCertificate {
    run_id
    position_digest
    policy_generation
    outward_deadline
    legal_root_digest

    anchor_candidate
    final_candidate

    explore_manifest
    verify_manifest?
    refine_manifest?
    crossfeed_manifest?
    adjudication_manifest?

    regime
    value_of_compute_model
    decision_policy

    resource_envelope
    measured_resource_summary
    open_reservations = 0

    fallback_eligible
    fallback_reason?

    decision_digest
}
```

This is an audit object, not proof that the move is objectively best.

---

# 10. Search-space doctrine after recursive refinement

The long-term search-space invariant should become:

> At every instant, each EXPLORE frontier shard has exactly one owner. Duplicate work is legal only through a separately authorized phase whose purpose and cost are explicit.

That applies recursively.

```text
EXPLORE
  unique ownership

VERIFY
  deliberate common support

REFINE
  deliberate zoom after nomination

ADJUDICATE
  deliberate final common candidate comparison
```

A cross-feed hint never changes the owner of the original EXPLORE shard.

---

# 11. Value-of-compute objective

The controller should eventually optimize something closer to:

```text
expected decision improvement per marginal unit of resource
```

rather than:

```text
equal engine shares
```

For a candidate route `r`:

```text
Value(r | state)
≈
expected reduction in decision regret
-
resource cost
-
deadline risk
-
coordination overhead
```

The exact estimator is empirical and must be calibrated from frozen historical/prospective runs.

Do not smuggle a theoretical probability into this equation before calibration earns one.

---

# 12. Strength labels and evidence hierarchy

Use an explicit evidence hierarchy.

From weakest to strongest:

1. deterministic unit/contract correctness;
2. replay consistency;
3. counterfactual decision difference;
4. deeper-reference agreement;
5. tactical-suite improvement;
6. fixed-resource self-play improvement;
7. paired engine-match improvement;
8. confirmatory SPRT / confidence evidence on frozen policy;
9. independent external reproduction.

A higher level does not retroactively turn lower-level proxy labels into truth.

---

# 13. Required ablations

Every major capability must be removable.

At minimum support these switches:

```text
crossfeed = off
verify = off
refine = off
regime_router = off
value_of_compute = off
hybrid_decision = off
native_adapter = off
policy_generation = fixed
lc0 = off
reckless = off
stockfish_shadow = off
```

The final controller must prove that its gains do not come from one hidden unaccounted source of extra compute.

---

# 14. Hardware and experiment profiles

Development and strength profiles should remain separate.

## 14.1 Fast CI profile

Purpose:

- contract correctness;
- deterministic controller tests;
- backend-light LC0 allowed;
- tiny node budgets;
- no strength claims.

## 14.2 Real-search integration profile

Purpose:

- actual Stockfish/Reckless/LC0 behavior;
- pinned LC0 network;
- bounded local workloads;
- resource instrumentation;
- no long match suite.

## 14.3 Qualification profile

Purpose:

- fixed hardware;
- fixed operating system;
- pinned drivers;
- pinned networks;
- isolated background load;
- process affinity where useful;
- measured resources;
- confirmatory matches.

A CI pass never substitutes for qualification.

---

# 15. Native integration decision rule

Do not native-integrate because it sounds faster.

For each boundary:

```text
process/UCI baseline
vs
structured IPC
vs
native adapter
```

Measure:

- controller overhead;
- engine throughput;
- cancellation latency;
- telemetry delay;
- search semantic drift;
- stability;
- implementation complexity;
- crash containment.

Keep the least coupled design that meets the required performance/control target.

---

# 16. Failure and fallback model

The controller must fail closed throughout the remaining build.

Examples:

### Specialist crash

```text
specialist evidence invalid / incomplete
→ no specialist decision authority
→ anchor fallback
```

### Budget overrun

```text
actual resource use exceeds allowed partition
→ envelope claim false
→ hybrid authorization denied unless a separately legal fallback path exists
```

### Missing calibration

```text
out of domain
→ buy more compute or fallback
```

### Cross-feed provenance mismatch

```text
reject hint
→ do not silently reconstruct it
```

### Decision deadline

```text
hybrid adjudication incomplete at deadline
→ anchor bestmove
```

### Native adapter uncertainty

```text
adapter parity test fails
→ process adapter remains authority
```

---

# 17. Documentation updates expected as implementation proceeds

Existing documents should remain the detailed contracts for their current layers.

Future PRs should update or add only what they actually change.

Likely documents:

- `docs/CROSS_FEED.md`
- `docs/DECISION_AUTHORITY.md`
- `docs/VALUE_OF_COMPUTE.md`
- `docs/RESOURCE_ACCOUNTING.md`
- `docs/SEARCH_REGIMES.md`
- `docs/POLICY_EVOLUTION.md`
- `docs/STRENGTH_QUALIFICATION.md`

Existing docs that will require synchronized updates when their semantics change:

- `docs/ARCHITECTURE.md`
- `docs/BUILD_PLAN.md`
- `docs/CLAIM_LEDGER.md`
- `docs/BUDGET_ROUTING.md`
- `docs/REFINEMENT.md`
- `docs/COMPARE_RELOCK.md`
- `docs/REPLAY_FORMAT.md`
- `docs/THEORY_IMPLEMENTATION_MAP.md`

---

# 18. What should not be built yet

Do not jump directly to:

- raw score voting;
- weighted average of Stockfish/Reckless/LC0 evaluations;
- one universal merged search tree;
- uncontrolled native embedding of all three engines;
- live self-modification;
- self-selected confirmatory benchmarks;
- a neural meta-controller trained on contaminated future labels;
- recursive refinement without exact ownership;
- unmetered GPU specialist work;
- a “best engine” marketing claim before match qualification;
- direct use of mathematical results from unrelated domains as pruning/search theorems.

These shortcuts would destroy the causal information the current architecture has spent eighteen PRs preserving.

---

# 19. Recommended immediate next implementation

The next code PR should be **PR #19 — Typed cross-feed substrate, shadow-only**.

It is the smallest step that moves toward actual hybrid intelligence without throwing away the safety and experiment discipline already built.

It gives the project a clean answer to:

> How can one engine tell the controller something useful that another engine may investigate, without pretending that their scores are interchangeable and without granting the message automatic decision authority?

Only after that object model is stable should the project build the counterfactual hybrid decision laboratory.

---

# 20. End-state promotion gate

AllfatherChess is ready for a genuine equal-resource strength claim only when all of the following are true:

- the policy generation is frozen;
- all constituent versions and model/network files are pinned;
- LC0 is real-backend strength-qualified;
- resource accounting is measured on the qualification platform;
- controller overhead is included;
- VERIFY, REFINE, cross-feed and adjudication all spend from the same envelope;
- no hidden EXPLORE overlap exists;
- the outward hybrid decision is replayable from frozen evidence;
- every fallback is explicit;
- all major components have ablations;
- historical calibration and confirmatory test sets are separated;
- final matches use a predeclared protocol;
- statistical stopping rules are fixed before results;
- Allfather beats each declared constituent baseline under the same resource contract;
- crashes, time losses and invalid runs are counted honestly;
- claim language matches the actual evidence.

The shortest form of the remaining programme is:

```text
PR #18
resource-governed specialist observation
        ↓
PR #19–23
typed cross-feed + counterfactual decisions + useful labels + real backend/resource qualification
        ↓
PR #24–28
bounded hybrid decision authority + recursive refinement + regime/value routing
        ↓
PR #29–30
measured transport/native optimization
        ↓
PR #31
offline governed policy evolution
        ↓
PR #32
confirmatory equal-resource strength campaign
        ↓
PR #33
release qualification
```

The architecture should earn every arrow.
