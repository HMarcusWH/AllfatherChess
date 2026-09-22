# AllfatherChess Extended Build Plan

**Status:** Post-PR #22 implementation baseline; PR #23 VERIFY value-of-compute calibration in progress  
**Date:** 2026-09-22  
**Scope:** Expand the existing `docs/BUILD_PLAN.md` from the current M12 control/resource milestone through typed cross-feed evidence, counterfactual and active hybrid decision authority, backend/resource qualification, recursive refinement, native integration, governed policy evolution, and the final equal-resource strength campaign.

This document is intentionally more detailed than `docs/BUILD_PLAN.md`. It does not replace the existing build plan, telemetry contracts, shard ledgers, replay contracts, or claim ledger. PR #19 and PR #20 were documentation-only. PR #21 added the typed cross-feed evidence plane and PR #22 added deterministic counterfactual hybrid proposals with PRE/POST_ANCHOR timing. Both are merged. The current implementation milestone is PR #23.

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

## 2. Current repository baseline after PR #19

PR #19 was documentation-only. The current runtime codebase is still the PR #18 implementation state, which already has the control substrate required to begin the actual hybrid-strength work.

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
- no typed cross-feed evidence layer exists yet; however, PR #14/#15 VERIFY already performs the expensive common-support re-search that cross-feed v1 should consume rather than duplicate;
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

Cross-feed is the next implementation milestone because PR #18 made specialist work accountable and the existing VERIFY path already performs the expensive common-support operation.

The important repo-level correction is:

~~~text
Do not add another cross-engine search phase merely to call it cross-feed.

EXPLORE
  -> owner-bestmove union
  -> existing common-support VERIFY
  -> existing VERIFY analysis / optional REFINE
  -> NEW typed cross-feed view
~~~

The current repository already supplies the search work:

- controller.shadow._execute_verification() reuses the three configured shadow instances after EXPLORE;
- controller.verification.build_verification_plan() freezes the owner-bestmove union as one common candidate set;
- controller.verification_analysis reconstructs those common-support trajectories and derives pairwise/triad structure plus descriptive RELOCK;
- controller.refinement can add one descendant shell when completed VERIFY remains non-unanimous.

Cross-feed v1 should therefore be an evidence-composition layer over those existing artifacts and in-memory run objects, not a fourth search protocol.

### 6.1 Cross-feed v1 should transfer candidates, provenance, and typed native evidence

The first safe transfer object should carry facts such as:

~~~text
CandidateHint
  move
  source owner / instance / family
  source phase
  source rank
  PV prefix
  source-native work
  source-native evaluation
  native semantics tag
  source run / stage identity
  provenance references
~~~

The recipient or decision layer may use the hint to nominate later VERIFY, REFINE, or adjudication work.

It may not reinterpret one engine's numeric evaluation in another engine's scale.

### 6.2 Build one pure in-memory view and one sealed artifact

The implementation should have two forms of the same information:

~~~text
completed in-memory VERIFY / REFINE evidence
        |
        v
CrossFeedView
        |
        +--> counterfactual decision code may consume the immutable view
        |
        v
after parent/VERIFY/REFINE manifests finalize
        |
        v
sealed crossfeed/manifest.json
~~~

This avoids two bad designs:

1. forcing the hot path to re-read finalized files before it can reason; and
2. persisting an unbound derived artifact before the source manifests exist.

The sealed artifact must hash-bind the parent replay manifest, VERIFY manifest, and REFINE manifest when REFINE was consumed.

### 6.3 Cross-feed cannot violate ownership

If LC0 discovers a move in its EXPLORE region and Stockfish later examines that move, the later work is not EXPLORE.

It must already be represented as VERIFY, REFINE, or a future explicit ADJUDICATE phase. The RootShardLedger / PrefixShardLedger ownership facts remain unchanged.

### 6.4 Cross-feed v1 gets no decision authority

PR #21 proved:

~~~text
cross-feed enabled
or
cross-feed disabled

=> identical outward anchor bestmove
~~~

The cross-feed view may be generated, sealed, replayed, and audited, but Stockfish anchor remains sole outward authority.

### 6.5 The initial no-effect phase is still useful

With the outward move unchanged, later counterfactual tooling can measure:

- how often a hybrid policy would differ from the anchor;
- which engine introduced the eventual common candidate;
- whether VERIFY convergence survives deeper reference work;
- whether REFINE changes the candidate;
- which evidence patterns are associated with useful or useless extra compute.

That evidence is required before any cross-feed result gets chess decision authority.

---

# 7. Extended PR train

PR #19 and PR #20 are merged documentation-only milestones. The implementation train begins at PR #21; PR #21 is now merged and PR #22 is the current implementation.

The sequencing below is anchored to the current repository boundaries:

- controller.runtime owns configuration/process identity;
- controller.shadow owns live generation barriers, EXPLORE/VERIFY/REFINE dispatch and the anchor-completion race;
- controller.verification and controller.refinement own raw specialist evidence;
- controller.verification_analysis owns derived common-support analysis;
- controller.routing and controller.budget own compute authorization and accounting;
- controller.uci_frontend is the only outward UCI authority path;
- Replay schema v1 remains the raw execution record and must not absorb derived decision artifacts.

## PR #19–20 — Extended build-plan documentation and synchronization

**Status: merged.**

These PRs introduced and synchronized the extended plan only. They did not change runtime behavior, search semantics, budget accounting, decision authority, or backend qualification.

---

## PR #21 — Typed cross-feed evidence plane, shadow-only

**Status: merged.**

### Purpose

Expose the information already created by EXPLORE -> VERIFY -> optional REFINE as one deterministic, typed, replayable evidence view without dispatching any new engine search and without changing the outward move.

### Add

- controller/crossfeed.py
- tests/controller/test_crossfeed.py
- scripts/crossfeed-contract.py
- docs/CROSS_FEED.md
- config/allfather.crossfeed.validation.json

### Modify

- controller/runtime.py
- controller/shadow.py
- controller/verification.py
- controller/refinement.py
- tests/controller/test_shadow_runtime.py
- Makefile
- .github/workflows/controller-shell.yml

### Runtime wiring

Add CrossFeedSettings to controller.runtime and one optional RuntimeConfig field.

The loader should require:

~~~text
mode in {shadow, active}
crossfeed.enabled == true
verification.enabled == true
~~~

Cross-feed v1 gets no dispatch limit because it launches no new search.

The new config exists only to make the capability explicit and ablatable.

### Data model

Implement immutable objects such as:

~~~text
CandidateHint
CrossFeedView
CrossFeedArtifact
~~~

CandidateHint should bind at least:

~~~text
move
source_owner
source_instance
source_family
source_phase
source_rank
pv_prefix
native_evaluation
native_semantics
native_work
search_id
source_run_id
~~~

No field may contain a derived cross-engine score delta.

### Construction path

In controller.shadow:

1. complete EXPLORE exactly as today;
2. complete existing VERIFY exactly as today;
3. complete existing REFINE exactly as today when applicable;
4. build an in-memory CrossFeedView from completed specialist state;
5. never let that view affect RouterCommand, anchor authority, or bestmove;
6. during finalization, finalize parent replay first;
7. finalize VERIFY;
8. finalize REFINE when present;
9. seal crossfeed/manifest.json by hashing the exact source manifests and stream identities.

This ordering is important because the current finalizer already produces parent -> VERIFY -> REFINE provenance in that order.

### Tests

Unit tests must cover:

- deterministic candidate ordering;
- canonical move validation;
- source-family / source-owner consistency;
- duplicate candidate collapse without provenance loss;
- incomplete VERIFY preserved as incomplete rather than promoted;
- missing/failed REFINE preserved as absent/incomplete;
- source-native semantics retained;
- no raw score averaging/conversion;
- source tamper detection;
- deterministic digest under identical source bytes;
- changed digest when any source identity changes.

The end-to-end contract should run the existing fake/real VERIFY path and require one cross-feed artifact with no additional engine search stage.

### Acceptance gate

- no new engine dispatch exists solely because cross-feed is enabled;
- EXPLORE ownership snapshots are byte-equivalent to the same run shape without cross-feed;
- raw replay, VERIFY, and REFINE artifacts remain separate;
- cross-feed is derived and source-bound;
- outward bestmove remains Stockfish anchor;
- cross-feed construction failure cannot change the outward move;
- controller-shell CI compiles and runs the new tests.

---

## PR #22 — Counterfactual hybrid decision laboratory

**Status: merged.**

### Purpose

Answer the first actual hybrid-intelligence question:

> Given the evidence Allfather already collected, what move would a hybrid decision policy have proposed?

The answer is still research evidence. The UCI frontend continues to emit the Stockfish anchor move.

### Add

- controller/decision.py
- controller/counterfactual.py
- tests/controller/test_decision.py
- tests/controller/test_counterfactual.py
- scripts/counterfactual-decision-contract.py
- scripts/counterfactual-decision-sweep.py
- docs/DECISION_AUTHORITY.md

### Modify

- controller/runtime.py
- controller/shadow.py
- tests/controller/test_shadow_runtime.py
- docs/CLAIM_LEDGER.md
- docs/BUILD_PLAN.md
- Makefile
- controller-shell CI
- baseline CI

### Type separation

Define separate immutable types for:

~~~text
DecisionCandidate
DecisionEvidence
DecisionProposal
DecisionAuthorization
DecisionDisposition
CounterfactualDecision
~~~

ResourceAuthorization from routing remains a different type and authority domain.

### Initial policy v1

Do not implement voting or numeric score fusion.

The first policy should be deliberately narrow:

~~~text
cross-feed / VERIFY evidence incomplete
    -> NO_HYBRID_PROPOSAL

VERIFY complete but final leaders non-unanimous
    -> NO_HYBRID_PROPOSAL

VERIFY complete and final leaders unanimous on one legal common-support move
    -> HYBRID_PROPOSAL(move)

any stale / malformed / unbound input
    -> NO_HYBRID_PROPOSAL
~~~

Descriptive RELOCK may be recorded as evidence, but RELOCK_OBSERVED is not itself a chess correctness certificate and should not bypass decision-policy checks.

The final verifier choice must come from each VERIFY stage's terminal bestmove / search.complete fact. The last candidate.update line is not an acceptable substitute for terminal choice.

### Freeze point

The counterfactual proposal must be frozen from only evidence available in that run before any later deep-reference or game-result label is attached.

Persist separately:

~~~text
<run>/decision/counterfactual.json
~~~

The record should include:

- anchor move;
- proposed hybrid move or none;
- policy/version;
- evidence digest;
- source cross-feed digest;
- disposition/reason;
- outward_authority = anchor.

### Runtime preparation for PR #26

The decision builder should be pure and usable both:

1. offline from sealed artifacts; and
2. in-memory from a completed CrossFeedView.

When specialist evidence finishes, controller.shadow freezes an in-memory DecisionProposal on the active run and stamps whether it existed PRE_ANCHOR or POST_ANCHOR. The anchor move is attached only later during artifact finalization, and the proposal must not be exposed outward yet.

### Acceptance gate

- exactly one anchor bestmove still reaches stdout;
- a counterfactual proposal cannot mutate Replay schema v1;
- replaying the same source artifacts yields the same decision digest;
- incomplete evidence never becomes unanimity;
- policy version is digest-bound;
- later outcome labels cannot alter an already-written counterfactual decision.

---

## PR #23 — Prospective chess-specific value-of-compute calibration

**Status: current implementation.**

### Purpose

Replace the current self-reversal/stability-only question with the first
decision-relevant intervention question:

> Under a matching upstream state, did buying another block of VERIFY compute
> change the frozen counterfactual hybrid decision?

This milestone calibrates **VERIFY compute specifically**. The current
`unanimous_verify_v1` proposal is driven by terminal VERIFY bestmoves, while
REFINE does not yet alter the proposal. PR #23 therefore disables REFINE in its
experimental profile rather than confounding the intervention.

### Add

- controller/value_of_compute.py
- controller/decision_calibration.py
- tests/controller/test_value_of_compute.py
- tests/controller/test_decision_calibration.py
- scripts/value-of-compute-sweep.py
- scripts/value-of-compute-contract.py
- scripts/decision-calibration.py
- config/allfather.value.validation.json
- docs/VALUE_OF_COMPUTE.md

### Modify

- Makefile
- .github/workflows/controller-shell.yml
- .github/workflows/baseline.yml
- docs/CLAIM_LEDGER.md
- docs/BUILD_PLAN.md
- docs/EXTENDED_BUILD_PLAN.md

The existing controller.calibration.py / bucketed_reversal_risk_v3 model remains
unchanged and load-incompatible with the new decision-change calibration.

### Real stopped-search ladder

Do not simulate a 128-node terminal bestmove from the first 128 nodes of a
longer search. Run actual complete VERIFY arms, initially:

~~~text
64 -> 128 -> 256 -> 512 nodes per verifier
~~~

Each arm must produce its own search.complete facts and frozen counterfactual
decision.

### Upstream causal fingerprint

Pair two arms only if a content hash agrees on all state upstream of the VERIFY
budget intervention, including:

- synchronized position / external request;
- engine binaries, arguments and options;
- EXPLORE owner roots and dispatch commands;
- EXPLORE nominees;
- common VERIFY candidate set and participants;
- cross-feed policy;
- decision policy.

Run ids, timestamps, VERIFY node limit, terminal VERIFY results, anchor result
and future labels are excluded.

A changed EXPLORE nominee or candidate set makes the pair ineligible rather than
being counted as a VERIFY effect.

### Frozen lower-arm features

Use only information available when the lower arm stopped:

- current decision disposition/move/source owner;
- PRE_ANCHOR / POST_ANCHOR state;
- terminal VERIFY vector at that budget;
- observation count;
- leader flips;
- stable-run fraction;
- PV persistence;
- self-retention of the EXPLORE nominee;
- stage elapsed time;
- engine-native work with semantics tags;
- evidence-completeness flags.

Reuse common.residuals.past_only_features for the shared temporal primitives.
Do not create separate offline/training definitions.

### Heterogeneous work firewall

Do not add Stockfish/Reckless/LC0 native counters into a universal "total
nodes" scalar. The requested intervention can state an extra N VERIFY nodes per
verifier; actual native work remains separately source-tagged until measured
physical resource accounting exists.

### Initial labels

Observed adjacent-arm labels:

~~~text
decision_changed
proposal_emerged
proposal_disappeared
proposal_move_changed
terminal_vector_changed
~~~

Complete ladders may additionally attach:

~~~text
candidate_survived_full_budget
decision_stabilized
~~~

A checkpoint with no proposal has candidate_survived_full_budget = null, not
false. Missing later arms are unobserved/right-censored, not negative labels.

Do not implement fake placeholders for deep_reference_agrees,
refine_changed_decision, crossfeed_changed_decision or game_outcome_delta until
those interventions/reference sources actually exist.

### Feature/label firewall

Each transition carries separate content addresses:

~~~text
feature_digest = SHA256(lower-arm past-only features)
label_digest   = SHA256(upper-arm outcome labels)
~~~

Changing future evidence may change the label digest but must not alter the
lower feature digest.

### Data split firewall

Run-id splitting is insufficient because multiple budget arms of one chess
position are near-duplicates.

Assign all budgets/repeats of one position_group to one deterministic partition.
With >=5 groups use a 60/20/20 train/calibration/holdout split. The ten-position
mechanism corpus therefore yields six train groups, two calibration groups and
two holdout groups.

### Model v1

Fit a deliberately small auditable model:

~~~text
bucketed_verify_decision_change_v1
~~~

Serving features:

~~~text
VERIFY transition
current proposal disposition
minimum verifier observation-count bucket
maximum verifier leader-flip bucket
minimum verifier stable-run bucket
~~~

Unknown or below-support buckets fail closed as out-of-domain with conservative
change probability 1.0. The artifact records Brier score, empirical/predicted
change rates, in-domain rate, reliability buckets and support per VERIFY
transition for train/calibration/holdout separately.

### Validation

Fast tests must cover:

- causal upstream mismatch rejection;
- replicate isolation;
- proposal emergence/disappearance/move-change labels;
- terminal-vector changes without policy changes;
- future labels unable to mutate lower feature digests;
- source-tagged native work with no fabricated aggregate;
- null candidate survival for no-proposal checkpoints;
- position-group split isolation;
- old reversal-risk model rejection;
- unknown/low-support fail-closed behavior;
- calibration artifact tamper detection.

The real-engine CI contract runs one nonterminal position at two budgets
(64 -> 128), requires matching upstream fingerprints and one eligible
ComputeTransition, and preserves Stockfish-anchor outward authority. It does
**not** require decision_changed == true.

The full 10-position x 4-budget x repeat sweep remains research-only and is not
normal CI.

### Acceptance gate

The output may support a bounded statement such as:

> In held-out positions within calibrated domain D, increasing VERIFY from N to
> M nodes changed the frozen counterfactual decision at observed rate R.

It does not yet support:

> The later decision was better, or the extra VERIFY compute improved Elo.

---

## PR #24 — Strength-qualified LC0 profile

### Purpose

Remove the largest backend qualification blocker before LC0 evidence can support a strength claim or active hybrid move.

### Modify / add

- vendor.lock.json or another existing locked-artifact section for the selected LC0 network identity;
- config/allfather.strength.validation.json;
- LC0 runtime/adapter configuration needed for the chosen real backend;
- scripts/lc0-strength-profile-contract.py;
- docs/STRENGTH_BACKENDS.md;
- qualification tests that do not replace the fast random-backend CI profile.

### Pin

- LC0 source/tree identity;
- network file identity and SHA-256;
- backend;
- GPU model;
- driver/runtime;
- batch/minibatch settings;
- cache settings;
- searcher/thread settings;
- memory limits;
- warmup policy;
- ScoreType;
- reproducibility limitations.

### Acceptance gate

The backend-light random profile remains the controller CI profile, but no strength-facing run may call it LC0 strength evidence.

---

## PR #25 — Measured process resource accounting

### Purpose

Preserve the PR #18 reservation model while separating declared/reserved cost from measured physical consumption.

### Add

- controller/resource_measurement.py
- adapters/resource/
- tests/controller/test_resource_measurement.py
- scripts/resource-accounting-contract.py
- docs/RESOURCE_ACCOUNTING.md

### Modify

- controller/budget.py
- controller/routing.py
- controller/shadow.py
- strength configuration
- relevant tests and CI compile lists.

### Preserve existing ledger semantics

The current BudgetLedger already correctly does:

~~~text
reserve
-> dispatch
-> settle
~~~

Keep that.

Extend settlement/reporting so the ledger can retain both:

~~~text
declared / reserved cost
measured actual cost
~~~

Do not overwrite one with the other.

### Measure on the qualification platform where supported

- process CPU time;
- child process CPU time;
- controller CPU time;
- wall time;
- RSS / memory;
- accelerator utilization or device time;
- GPU memory;
- IPC/controller overhead.

The existing wall x configured-threads estimate remains available as an estimate, but it must be labeled as such once measured CPU data exists.

### Acceptance gate

A run may be development-valid with partial measurement, but the strongest equal-resource claim requires the declared qualification platform's required CPU/GPU measurements.

---

## PR #26 — Active hybrid decision authority v0

### Purpose

Permit a tightly bounded, already-frozen hybrid proposal to replace the Stockfish anchor move.

This is the first PR that changes chess decision authority.

### Critical repo seam

Today controller.uci_frontend._on_search_complete():

1. calls shadow.note_anchor_complete();
2. marks the UCI shell ready;
3. immediately writes the anchor bestmove.

PR #26 must change this path without blocking the anchor stdout reader on engine work or filesystem IO.

### Required authority design

By the time the anchor bestmove arrives, any hybrid proposal eligible for v0 must already be frozen in memory.

The anchor completion callback may perform only bounded in-memory authorization.

Conceptually:

~~~text
anchor bestmove callback
    -> publish anchor completion boundary
    -> read already-frozen CounterfactualDecision
    -> run bounded DecisionAuthorization checks
       -> granted: return hybrid move
       -> denied/missing: return original anchor move
    -> UciFrontend emits exactly one bestmove
~~~

No new VERIFY/REFINE/engine search may start after the current anchor-completion lock boundary.

### Add

- FinalDecision / DecisionAuthorization result in controller/decision.py;
- config/allfather.hybrid.validation.json;
- scripts/active-hybrid-decision-contract.py;
- active-hybrid frontend/runtime tests.

### Modify

- controller/uci_frontend.py;
- controller/shadow.py;
- controller/runtime.py;
- controller/decision.py;
- controller/budget.py / routing only for the authorization facts they own;
- tests/controller/test_uci_frontend.py;
- tests/controller/test_shadow_runtime.py.

### Initial supported request class

Start with a narrow bounded request class, preferably go movetime, with an explicit configuration gate.

Unsupported request forms such as infinite / ponder / complex clock control remain anchor-authority until separately qualified.

### Authorization gate

Hybrid emission requires all of:

- legal proposed move;
- current generation/position match;
- frozen decision policy/version;
- complete required evidence;
- source digests match;
- no evidence-loss flag;
- bounded request class;
- active envelope claim still valid for the run state used;
- no open indispensable specialist reservation;
- proposal existed before anchor completion;
- no stale backend generation;
- no fail-closed reason.

Any failure returns the buffered anchor move.

### Acceptance gate

- one and only one bestmove is emitted;
- hybrid-disabled mode stays byte-for-byte anchor behavior;
- hybrid failure falls back to anchor, never another specialist;
- anchor process failure still produces the existing fail-closed behavior;
- no blocking engine work or file IO is added to the anchor stdout callback;
- decision certificate records whether authority was HYBRID or ANCHOR_FALLBACK.

---

## PR #27 — Multi-level recursive REFINE

### Purpose

Generalize the current one-child-shell refinement into bounded recursive zoom while preserving PrefixShardLedger invariants.

### Modify

- controller/prefix_shards.py;
- controller/refinement.py;
- common/prefix_dispatch.py;
- controller/budget.py;
- controller/routing.py;
- tests/controller/test_prefix_shards.py;
- tests/controller/test_refinement.py.

### Add

- controller/refinement_policy.py;
- tests/controller/test_refinement_policy.py;
- scripts/recursive-refinement-contract.py.

### Rules

- every split begins at a SEALED eligible leaf;
- exact Stockfish perft child legality is required;
- frontier remains prefix-free;
- child ownership inheritance/transfer remains atomic;
- every deeper split needs explicit nomination evidence;
- each oracle/stage reserves before dispatch;
- recursion terminates on resolution, budget exhaustion, depth cap, anchor boundary, or failure;
- no descendant work starts after outward decision boundary.

### Acceptance gate

Recursive REFINE can zoom multiple levels without weakening ownership or resource accounting. No claim that the chosen refinement policy improves Elo yet.

---

## PR #28 — Engine-specific cross-feed adapters

### Purpose

Let each engine consume typed candidate information in a way compatible with its own search paradigm while remaining inside explicit VERIFY/REFINE/ADJUDICATE semantics.

### Add

- adapters/crossfeed/stockfish.py;
- adapters/crossfeed/reckless.py;
- adapters/crossfeed/lc0.py;
- corresponding tests and contract fixtures.

### Initial subprocess-safe operations

- common candidate-set search via searchmoves;
- candidate-subset VERIFY;
- PV-prefix REFINE;
- tactical-alarm nomination;
- source-rank-informed route priority.

### Nonclaims

Do not yet claim:

- LC0 policy directly rewrites native Stockfish move ordering;
- Stockfish cp directly rewrites LC0 Q/value;
- native search trees are unified.

Those require later native hooks and separate validation.

---

## PR #29 — Search-regime classifier

### Purpose

Classify the current search situation from source-typed evidence so the router can condition later route proposals without pretending one policy is optimal everywhere.

### Add

- controller/regimes.py;
- controller/regime_calibration.py;
- tests/controller/test_regimes.py;
- scripts/regime-sweep.py;
- docs/SEARCH_REGIMES.md.

### Candidate regime vocabulary

Hypotheses to calibrate, not truth labels:

~~~text
STABLE_CONVERGENT
TACTICAL_RUPTURE
CROSS_ENGINE_DISAGREEMENT
POLICY_DIFFUSE
ENDGAME_EXACT
TIME_CRITICAL
REFINEMENT_PRODUCTIVE
REFINEMENT_STALLED
OUT_OF_DOMAIN
~~~

A regime may nominate work. It does not authorize a move.

---

## PR #30 — Unified value-of-compute decision router

### Purpose

Bring current routing, new decision calibration, regime state, and hybrid authorization into one typed loop without collapsing their authorities.

### Target loop

~~~text
observe
-> summarize
-> classify regime
-> estimate unresolved decision mass
-> propose route
-> estimate value/cost
-> authorize resource
-> reserve
-> dispatch
-> settle
-> update evidence
-> decide / repeat / fallback
~~~

### Route vocabulary

Evolve the current RouteAction family toward explicit actions such as:

~~~text
CONTINUE_OWNER
VERIFY_SET
REFINE_PREFIX
ADJUDICATE_SET
EXTEND_ANCHOR
STOP_OWNER
FALLBACK_ANCHOR
ABSTAIN_BUY_COMPUTE
DECIDE_HYBRID
~~~

The decision action still requires DecisionAuthorization; RouteAction alone never becomes bestmove authority.

---

## PR #31 — Structured IPC experiment

### Purpose

Measure whether a typed local protocol improves latency/control enough to justify replacing selected UCI text boundaries.

### Add

A versioned request/result/telemetry protocol for one backend first.

Compare:

~~~text
current UCI process adapter
vs
structured local adapter
~~~

Keep the UCI adapter as parity/reference implementation.

Promotion requires measured benefit and no unexplained chess-semantic drift.

---

## PR #32 — Selective native engine integration

### Purpose

Embed only the hooks whose measured value justifies tighter coupling.

### Candidate order

1. Stockfish restricted-search / ordering hooks;
2. Reckless alpha-beta hooks;
3. LC0 policy/search hooks.

### Required parity

Every native adapter must pass a compatibility harness against the process adapter for the behavior the controller depends on.

Native integration is an optimization of an already-demonstrated hybrid mechanism, not a prerequisite for learning whether the hybrid mechanism works.

---

## PR #33 — Offline governed policy evolution laboratory

### Purpose

Generate and qualify controller-policy candidates without live self-mutation.

### Add

- controller/policy.py;
- controller/policy_registry.py;
- scripts/policy-candidate-evaluate.py;
- scripts/policy-promotion-contract.py;
- tests/controller/test_policy_registry.py;
- docs/POLICY_EVOLUTION.md.

### Candidate parameters

Examples:

- reserve fractions;
- stage budgets;
- regime thresholds;
- candidate-set widths;
- VERIFY triggers;
- REFINE triggers/depth caps;
- stop floors;
- adjudication budget;
- engine priority;
- cross-feed eligibility.

### Promotion lifecycle

~~~text
ACTIVE_POLICY_n
-> telemetry
-> CANDIDATE_POLICY_n+1
-> frozen train/calibration
-> holdout
-> paired games / confirmatory test
-> promotion decision
-> ACTIVE_POLICY_n+1
~~~

The candidate policy cannot choose its own scorer, holdout, stopping rule, or baseline after observing results.

---

## PR #34 — Strength campaign infrastructure

### Purpose

Build reproducible confirmatory match infrastructure separate from development CI.

### Add

- scripts/strength-campaign.py;
- scripts/match-runner.py;
- statistical stopping / SPRT tooling;
- docs/STRENGTH_QUALIFICATION.md;
- machine-readable match manifests;
- frozen opening suites;
- qualification hardware profiles.

### Required arms

At minimum:

~~~text
Stockfish baseline
Reckless baseline
LC0 baseline
Allfather anchor-only
Allfather observation-only
Allfather VERIFY-only
Allfather REFINE-only
Allfather cross-feed/counterfactual
Allfather active hybrid
Allfather active hybrid minus each major component
~~~

### Discipline

- paired openings/colors;
- predeclared protocol;
- fixed policy generation;
- no tuning on confirmatory matches;
- fixed stopping rule;
- confidence interval / SPRT reporting;
- crash/time loss inclusion;
- explicit resource accounting;
- disclosed exclusions.

---

## PR #35 — External qualification and release candidate

### Purpose

Freeze the first release candidate that can support an equal-resource strength statement bounded to a declared platform/protocol.

### Gate

- all three backend strength identities pinned;
- real LC0 profile qualified;
- required measured resources available;
- policy generation frozen;
- no open reservation leaks;
- hybrid decision replayable;
- ablations complete;
- confirmatory campaign positive against each declared constituent baseline under the same resource contract;
- UCI compatibility tested against real harnesses/GUI;
- licensing/provenance review complete;
- CLAIM_LEDGER updated with exact claims and nonclaims.

No generic global best-engine claim follows from one local platform/match suite.

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

The next code PR is **PR #23 — Prospective VERIFY value-of-compute calibration**.

PR #21 and PR #22 are merged: the repo now has typed replayable specialist evidence plus deterministic frozen hybrid proposals. PR #23 measures, prospectively and without hindsight leakage, whether additional VERIFY compute changes those proposals under a matching upstream state.

The immediate implementation question becomes:

> How do we freeze a deterministic hybrid proposal from typed cross-feed plus the actual terminal VERIFY bestmoves, record whether that proposal existed before the anchor boundary, and independently replay it without granting outward authority?

The counterfactual laboratory must use actual VERIFY terminal bestmoves, not infer final verifier choice from the last candidate.update event.

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
PR #19–20
extended build-plan documentation (merged)
        ↓
PR #21–25
typed cross-feed evidence + counterfactual decisions + value-of-compute + real backend/resource qualification
        ↓
PR #26–30
bounded hybrid decision authority + recursive refinement + engine-specific cross-feed + regime/value routing
        ↓
PR #31–32
measured transport / selective native optimization
        ↓
PR #33
offline governed policy evolution
        ↓
PR #34
confirmatory equal-resource strength campaign
        ↓
PR #35
release qualification
```

The architecture should earn every arrow.
