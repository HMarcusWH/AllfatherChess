# Shadow Execution and Replay

## Status

**Implemented.** This document was the frozen design contract; it now also
records how the implementation satisfies it.

PR #10 established controller-owned root topology. Shadow mode is the first
milestone that executes that topology concurrently. It remains an
**observability and evidence-collection milestone**: it implements no hybrid
move selection and makes no strength claim. The derived layer built on top of it
is documented in `docs/RESIDUAL_CALIBRATION.md`, and the active policy built on
top of that in `docs/BUDGET_ROUTING.md`. Neither can change the outward move.

Implementation: `controller/shadow.py`, `controller/replay.py`,
`controller/runtime.py`. Validated by `tests/controller/test_shadow_runtime.py`,
`tests/controller/test_replay.py`, and `scripts/shadow-execution-contract.py`.

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

Shadow mode is intentionally allowed to violate that eventual budget constraint because it is a research observatory. It runs an unrestricted anchor and three shadow workers simultaneously to learn where compute was useful, wasted, stable, or decision-changing. Therefore **shadow mode is not evidence of equal-compute strength**.

Active mode (`mode: "active"`) is where the envelope becomes binding for shadow
work. Even there, no equal-resource benchmark match has been run, so equal-envelope superiority remains OPEN.

## Process topology

Shadow and active mode require four managed engine instances under one external UCI identity:

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

There is exactly one outward decision authority:

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

For each non-empty owner region, the coordinator dispatches exactly:

```text
ledger.active_roots(owner)
```

through the already-qualified UCI `searchmoves` primitive.

Empty owner regions are legal ledger states but cannot create a search dispatch.

## Evidence firewall

Shadow execution records **raw evidence only**.

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

Forbidden in raw evidence, and implemented in the **derived** layer instead:

- cross-engine score calibration (still not implemented: see the scale firewall);
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

Derived features live in `derived/<derived_id>/features.json`, calibrated models
in `calibration/<model_id>/model.json`, and active routing decisions in the
run's `route.json`. None of them is ever written into a raw stream or manifest,
and tests assert this by scanning for the vocabulary.

The rule is:

```text
backend output
    -> raw telemetry
    -> replay/state reconstruction
    -> derived residual geometry
    -> routing / verification policy
```

Shadow mode implements the first two layers. The third and fourth are separate
modules with their own artifacts and their own documents.

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

The replay manifest is orchestration evidence. It must not contain residuals or
routing decisions. The full field list is in `docs/REPLAY_FORMAT.md`.

Two additions the implementation makes to the frozen list:

- `controller.overhead.prepare_ms` and `controller.overhead.qualification_ms`
  record the controller work performed before the anchor dispatch and before the
  first shadow dispatch, so controller overhead cannot hide;
- each stream record carries `contract_validatable`, which is false for an
  incomplete or lossy stream. Such a stream is kept as evidence and is *not*
  given a fabricated `search.complete`.

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

The implemented ordering differs deliberately in one place: the unrestricted
anchor is dispatched **first**, and legal-root qualification plus shadow
dispatch happen afterwards on a worker thread. Root qualification uses
`stockfish-shadow`, never the anchor; naming the anchor as oracle is a
configuration error. The manifest records actual dispatch and completion
ordering for every stage.

One consequence is recorded honestly rather than hidden: if the anchor's search
is very short, it can complete before shadow qualification finishes, and the run
is then recorded with a note and **no** shadow stages at all. Evidence density
therefore depends on the time control, and a fixed-node regression run is not a
useful observatory run.

If the external request carries `searchmoves`, the shadow universe is
intersected with it before partitioning. The anchor searches only what the
caller asked for, and shadow evidence has to describe the same request; owning
roots the caller excluded would make the streams and the manifest describe two
different searches. The manifest records the oracle count, the dispatched count,
and the restriction.

## Stop, ponder, quit, and failures

The outward UCI lifecycle remains authoritative.

- `stop` must unblock the outward anchor and terminate/drain shadow work for the same run.
- `quit` must close every managed process without orphans.
- any state mutation (`position`, `ucinewgame`, `setoption UCI_Chess960`) passes through a quiesce barrier that cancels and joins the previous generation first, so no stale generation can observe the next position.
- when the anchor completes, `shadow.on_anchor_complete` declares what happens to an in-flight node-limited stage: `drain` (default) lets it finish, `cancel` kills it. Either way no *new* stage may open once the outward decision has been emitted — including the first stage of a run whose qualification lost the race to a short anchor search. The check and the dispatch are committed under one lock, so the window between them cannot leak a stage.
- a worker that ignores `stop` and misses the drain deadline is recorded as a shadow failure, which removes it from synchronization and dispatch and releases the run's bundle. A stuck worker degrades to evidence, exactly like a crashed one; it never leaves the caller free to synchronize state into a process still executing the previous generation.
- **an oracle still answering `go perft 1` is covered by the same rule.** Owner states are created only after qualification returns, so the stuck-worker sweep has nothing to iterate while the oracle is blocked; qualification-in-flight is tracked explicitly and the oracle is recorded as a shadow failure on the same path. This is also why `shadow.oracle` must name an instance whose role is `shadow`: a `managed` instance is authority-critical, its timeout would take the authority failure path, and `record_shadow_failure` would not exclude it from synchronization at all.
- **the authority stream's deadline is the anchor's own completion, never a shadow-side timeout.** Finalization waits while the anchor is alive and the coordinator is open, both polled rather than assumed. Bounding that wait by the shadow drain timeout closed the anchor's stream and cleared the run mid-search whenever the anchor outlived its node-limited shadows, so the outward `bestmove` had nowhere to land.
- a `go` that arrives while a previous generation is still draining waits for that generation to drain before a new run is installed. Replacing the run without waiting would orphan the old worker, whose own drain deadline could then stop a process the new generation is already using.
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

## Non-goals that remain non-goals

Still not implemented anywhere in this milestone:

- normalized cross-engine values (the scale firewall actively prevents them);
- candidate voting or any shadow influence on the outward move;
- recursive shard splitting;
- shard transfer;
- VERIFY / RELOCK overlap;
- cross-feed between backends;
- native in-process engine integration;
- an Elo or "best engine" claim.

## What the derived layer answers

`controller/replay_analysis.py` and `controller/residuals.py` consume these
bundles to answer counterfactual questions such as:

- when did each engine's leader stabilize?
- which disagreements predicted a later decision reversal?
- which disagreements were harmless noise?
- what compute was spent after a decision was effectively stable?
- when did LC0-vs-alpha-beta disagreement carry more information than Stockfish-vs-Reckless disagreement?
- under a smaller hypothetical budget, which engine should have received the next unit of compute?

Only after those relationships are calibrated may the controller begin turning
observation into routing policy, which is exactly the gate structure
`controller/routing.py` enforces.

Two of those questions currently have **no answer inside a single run**: because
the observation partition is pairwise disjoint, Stockfish and Reckless are never
authorized to search a common root, so their direct disagreement has empty
support. The feature library handles shared support correctly and is tested on
overlapping synthetic regions, but answering those questions live requires an
overlap-capable COMPARE/VERIFY phase. See `docs/RESIDUAL_CALIBRATION.md`.
