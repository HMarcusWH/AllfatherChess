# Shadow Execution and Replay

## Status

Normative design contract for **PR #11**.

PR #10 established controller-owned root topology. PR #11 is the first milestone that executes that topology concurrently, but it remains an **observability and evidence-collection milestone**. It does not yet implement hybrid move selection, residual calibration, adaptive routing, or a strength claim.

## Why shadow mode exists

The final Allfather objective is equal-envelope superiority:

```text
total budget B

Stockfish alone -> B
Reckless alone  -> B
LC0 alone       -> B

Allfather ->
    Stockfish work
  + Reckless work
  + LC0 work
  + VERIFY / RELOCK work
  + controller overhead
  <= B
```

Allfather succeeds only if using that common envelope more intelligently produces stronger chess than giving the comparable envelope to any constituent engine alone.

PR #11 is intentionally allowed to violate that eventual budget constraint because it is a research observatory. It may run an unrestricted anchor and three shadow workers simultaneously to learn where compute was useful, wasted, stable, or decision-changing. Therefore **shadow mode is not evidence of equal-compute strength**.

## Process topology

PR #11 requires four managed engine instances under one external UCI identity:

```text
                       external go
                           |
                  +--------+--------+
                  |                 |
                  v                 v
          stockfish-anchor      shadow run
          unrestricted              |
          outward authority         v
                  |          legal root universe
                  |                 |
                  |          RootShardLedger
                  |          /      |      \
                  |         /       |       \
                  |   stockfish  reckless   lc0
                  |    shadow     shadow    shadow
                  |       |         |        |
                  |       +---- searchmoves--+
                  |                 |
                  |          raw telemetry v1
                  |                 |
                  |           replay manifest
                  |
                  v
          outward bestmove
```

The solver-family ledger owners remain:

```text
stockfish
reckless
lc0
```

The runtime process identities are:

```text
stockfish-anchor
stockfish-shadow
reckless-shadow
lc0-shadow
```

`engine` in telemetry identifies solver-family semantics. `engine_instance` identifies the concrete managed role.

## Decision firewall

PR #11 has exactly one outward decision authority:

```text
stockfish-anchor -> outward bestmove
```

Shadow output may be recorded, replayed, and analyzed later. It may **not**:

- vote on the outward move;
- replace the anchor bestmove;
- constrain the anchor root set;
- trigger rerouting;
- trigger early stopping;
- change a resource allocation policy;
- manufacture fallback authority.

A shadow backend failure is evidence, not an election. No shadow worker is silently promoted.

## Ownership firewall

`RootShardLedger` governs the three shadow exploration workers only.

The unrestricted Stockfish anchor is intentionally outside shard ownership. It may independently visit positions or roots also visited by shadow workers because it is a reference/authority computation, not a competing exploration owner.

Among shadow exploration workers, the PR #10 invariant remains exact:

```text
owner_count(root_shard) <= 1
```

For each non-empty owner region, PR #11 dispatches exactly:

```text
ledger.active_roots(owner)
```

through the already-qualified UCI `searchmoves` primitive.

Empty owner regions are legal ledger states but cannot create a search dispatch.

## Evidence firewall

PR #11 records **raw evidence only**.

Allowed:

- telemetry v1 `search.started`;
- `candidate.update`;
- lossless engine-native events;
- independently sourced terminal facts;
- `search.complete`;
- ledger snapshots;
- exact root assignments;
- process/config/build identity;
- completion/failure disposition;
- run timing needed for replay ordering.

Deferred to PR #12 or later:

- cross-engine score calibration;
- aggregate candidate rankings;
- top-k overlap;
- rank correlation;
- PV-overlap scores;
- leader/runner-up synthesis;
- residuals;
- disagreement thresholds;
- reversal-risk labels;
- routing decisions;
- stopping decisions.

The rule is:

```text
backend output
    -> raw telemetry
    -> replay/state reconstruction
    -> derived residual geometry
    -> routing / verification policy
```

PR #11 implements only the first two layers.

## Replay bundle

Telemetry v1 remains a per-search stream. One PR #11 experiment therefore needs a separate run-level manifest that binds the streams together.

A replay manifest must contain enough information to reconstruct the experiment without inventing missing controller state. At minimum:

```text
schema_version
run_id
position_id
variant / move encoding
position reconstruction
external go request
anchor engine/build/config identity
shadow engine/build/config identities
pre-dispatch ledger snapshot
exact shadow root set per owner
telemetry stream identity/path per engine instance
dispatch/completion ordering metadata
per-instance completion or failure disposition
post-run ledger snapshot
artifact hashes or equivalent integrity references where practical
```

The replay manifest is orchestration evidence. It must not contain PR #12 residuals or PR #13 routing decisions.

## Lifecycle

A normal nonterminal shadow run is:

```text
synchronize position
qualify legal roots
create ledger
assign exact partition
activate non-empty owners
snapshot ledger
start unrestricted stockfish-anchor
start restricted shadow workers
collect raw telemetry
stop/complete workers
seal completed owner regions
record failures explicitly
snapshot final ledger
write replay manifest
emit outward anchor bestmove only
```

Implementation ordering may differ where needed to preserve UCI semantics, but the recorded replay evidence must make actual ordering explicit.

## Stop, ponder, quit, and failures

The outward UCI lifecycle remains authoritative.

- `stop` must unblock the outward anchor and terminate/drain shadow work for the same run.
- `quit` must close every managed process without orphans.
- `ponderhit` semantics belong to the outward anchor unless and until a later shadow policy explicitly qualifies equivalent behavior.
- an unexpected shadow exit is recorded as a shadow failure;
- an unexpected anchor failure retains the existing fail-closed outward behavior;
- a shadow failure never grants outward authority to Reckless, LC0, or `stockfish-shadow`.

Exactly one outward bestmove may be emitted for a successfully completed external search.

## Resource and strength claim boundary

Concurrent shadow execution can perturb timed anchor search through CPU/GPU contention even when no shadow result is consulted.

Therefore PR #11 separates **decision non-intervention** from **resource non-interference**:

- fixed-node or otherwise deterministic anchor contracts may establish that shadow logic does not directly alter Stockfish move authority;
- timed equivalence to standalone Stockfish may be claimed only when relevant CPU/GPU/memory resource isolation is explicitly established and tested;
- ordinary shadow runs are research data collection and may consume more than the eventual competitive budget.

The final active controller must count controller overhead and all constituent/verification compute inside the same declared resource envelope.

## PR #11 non-goals

PR #11 does not implement:

- residual calibration;
- normalized cross-engine values;
- candidate voting;
- adaptive budget routing;
- recursive shard splitting;
- shard transfer;
- VERIFY / RELOCK overlap;
- cross-feed;
- native in-process engine integration;
- an Elo or "best engine" claim.

Those milestones require the replay evidence created here.

## Handoff to PR #12

PR #12 consumes PR #11 replay bundles to answer counterfactual questions such as:

- when did each engine's leader stabilize?
- which disagreements predicted a later decision reversal?
- which disagreements were harmless noise?
- what compute was spent after a decision was effectively stable?
- when did LC0-vs-alpha-beta disagreement carry more information than Stockfish-vs-Reckless disagreement?
- under a smaller hypothetical budget, which engine should have received the next unit of compute?

Only after those relationships are calibrated may the controller begin turning observation into routing policy.
