# Architecture

## Product boundary

AllfatherChess is the externally visible chess engine. Stockfish, Reckless, and LC0 are heterogeneous solver backends managed by a meta-controller.

The controller owns:

- legal search-region allocation;
- CPU, GPU, wall-clock, and memory budgets;
- engine-specific work dispatch;
- normalized evidence collection;
- disagreement / residual estimation;
- explicit verification and re-lock;
- fallback and final stopping.

The controller does **not** flatten the three native search states into one universal tree. Stockfish and Reckless keep their alpha-beta/NNUE internals; LC0 keeps its MCTS tree, NN cache, policy/value semantics, and batching.

## Optimization objective

The product target is not "three engines worth of compute against one engine." In the eventual active regime, all solver work, verification work, and controller overhead must fit inside one declared total resource envelope `B`. The controller succeeds only if allocating that envelope heterogeneously produces stronger chess than giving the comparable envelope to Stockfish, Reckless, or LC0 alone. Shadow mode is deliberately allowed to overspend while collecting calibration evidence, and therefore carries no equal-compute strength claim.

## Current composition boundary

The live repository is post-PR #36 / ONLINE-1. Several advanced mechanisms are
individually qualified but intentionally not composable in one production profile yet.
See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the exact release matrix.

The key present firewalls are:

- M14-C can grant hybrid outward authority only for its qualified `movetime_v0` class;
- M14-G1/G2 staged VERIFY/value routing does not grant outward move authority and staged
  VERIFY is rejected when M14-C authority is enabled;
- ONLINE-1 clock-derived `TimePlan` execution is CPU-only, anchor-authoritative, and
  rejected with M14-C authority or recursive REFINE;
- the real-network LC0 BLAS profile is a separate inference qualification, not yet the
  ONLINE-1 deployment profile.

The next architecture composition milestone is M14-G3, after ONLINE-2 establishes the
real-network online host/profile identity.

## Generations

### Generation 1 — process-isolated monorepo

Anchor-mode Generation 1 provides four process boundaries:

- `allfather-chess` / `python -m controller` — the only external UCI endpoint;
- Stockfish anchor;
- Reckless managed backend;
- LC0 managed backend.

Shadow mode adds a second Stockfish process. The controller therefore manages four engine instances while preserving one external UCI identity:

- `stockfish-anchor` — unrestricted outward decision authority;
- `stockfish-shadow` — restricted Stockfish evidence worker;
- `reckless-shadow` — restricted Reckless evidence worker;
- `lc0-shadow` — restricted LC0 evidence worker.

The controller communicates with engine processes through a production UCI process adapter with one permanent stdout reader per backend. In the first Generation-1 implementation all three engines are launched, configured, synchronized, and health-checked, while Stockfish alone acts as the transparent search anchor.

PR #10 adds a controller-owned legal-root oracle and `RootShardLedger`. Stockfish `go perft 1` provides the canonical live legal-root universe; the ledger can atomically assign those roots into pairwise-disjoint owner regions for Stockfish, Reckless, and LC0. In shadow/active mode that oracle runs on `stockfish-shadow`, so root qualification can never block the outward anchor.

Shadow mode consumes that ownership. The three shadow workers search their pairwise-disjoint `active_roots(owner)` regions concurrently and emit raw telemetry plus a run-level replay bundle. The unrestricted `stockfish-anchor` is intentionally outside the exploration ledger and remains the only outward bestmove authority.

### Layering

```text
raw telemetry v1          what each engine observed        controller/replay.py
    |
replay bundle             what experiment was executed      controller/replay.py
    |
residual features         derived disagreement geometry     controller/residuals.py
    |
calibrated risk model     fitted, out-of-sample validated   controller/calibration.py
    |
routing policy            proposal -> gate -> action        controller/routing.py
    |
dispatch                  restricted searchmoves            controller/shadow.py
```

Each layer is independently testable and no layer may reach upward. Residuals never appear in raw telemetry; routing decisions never appear in a replay manifest.

### Execution modes

| Mode | Instances | Ledger | Routing | Outward authority |
| --- | --- | --- | --- | --- |
| `anchor` | 3, legacy profile | unused live | none | Stockfish anchor |
| `shadow` | 4 | 3 shadow owners | none | `stockfish-anchor` |
| `active` default | 4 | 3 shadow owners | conservative observation/resource routing | `stockfish-anchor` |
| `active` M14-C hybrid profile | 4 | 3 shadow owners | VERIFY/REFINE + separate DecisionAuthorization | HYBRID only when `movetime_v0` gates pass; otherwise anchor fallback |
| `active` M14-G2 staged profile | 4 | 3 shadow owners | base VERIFY -> BUY/SKIP -> optional staged VERIFY | `stockfish-anchor` |
| `active` ONLINE-1 clock profile | 4 | 3 shadow owners | clock-derived bounded observation/resource routing | `stockfish-anchor` |

Default active routing and G2/ONLINE-1 remain observation/resource authority only. The
narrow M14-C profile is the sole current path that can replace the anchor move, and its
request/evidence contract is deliberately incompatible with staged VERIFY and ONLINE-1
until M14-G3. See `docs/BUDGET_ROUTING.md`, `docs/DECISION_AUTHORITY.md`,
`docs/UNIFIED_VALUE_ROUTER.md`, and `docs/ONLINE_TIME.md`.

### Generation 1.5 — measured control

Generations 2-4 remain as below. The immediate stack adds the measurement,
calibration, and authorization machinery those later generations require:
without replay evidence and an out-of-sample calibration there is no honest way
to decide that a shortcut is safe.

### Generation 2 — structured local adapters

Replace text parsing where useful with richer local IPC while preserving process isolation.

### Generation 3 — native adapters

Embed solver APIs where the measured overhead or required control granularity justifies it.

### Generation 4 — cross-fed heterogeneous search

Permit controlled information transfer such as LC0 policy hints to alpha-beta move ordering and alpha-beta tactical alarms to LC0 candidate prioritization.

## Central state

The controller maintains and will extend an engine-neutral state containing:

- current synchronized position and a Stockfish-qualified legal-root universe;
- root-v1 `RootShardLedger` ownership, generation, revision, and shard state;
- shadow-run identity and replay-manifest binding across anchor/shadow engine instances;
- per-engine observations;
- normalized candidate rankings and PV summaries;
- intra-alpha-beta and cross-paradigm residuals;
- resource budget;
- route history;
- verification / re-lock state;
- hard terminal evidence such as legal mate or tablebase facts.

Engine-native values remain tagged with their source and semantics.


## Explicit common-support VERIFY

After a clean pairwise-disjoint EXPLORE run, VERIFY v1 takes the three owner
bestmoves in declared owner order and reuses the same three shadow processes to
search that exact common root set. This is deliberate duplicate work under
phase `VERIFY`; it does not modify `RootShardLedger`.

Raw VERIFY streams and their own manifest live under
`<run>/verification/` and hash-bind the finalized parent replay manifest.
`controller/verification.py` owns that evidence model; `controller/shadow.py`
continues to own UCI process lifecycle and the anchor-completion race.

The outward move remains the unrestricted Stockfish anchor's. Active-mode
VERIFY is rejected until verification spend is wired into `BudgetLedger`.


## COMPARE / descriptive RELOCK derived layer

The raw VERIFY child is consumed offline by
`controller/verification_analysis.py`. The analyzer reuses the common
`SearchTrajectory` reconstruction and `compare_at()` residual primitive; no
new score-conversion path exists.

For complete VERIFY evidence it derives the three stable pairings, synchronized
three-way state over the common active controller-clock window, candidate
adoption from EXPLORE to VERIFY, and a frozen terminal-suffix RELOCK descriptor.
The output is written under `build/verification-derived/` and is not read by
`shadow.py`, `routing.py`, `budget.py`, or the UCI frontend.


## Recursive PrefixShardLedger v2

Recursive search-space geometry is qualified in a separate substrate rather than
being inserted into the current shadow lifecycle.

```text
RootShardLedger v1
    -> live EXPLORE root partition

PrefixShardLedger v2
    -> recursive prefix tree
    -> prefix-free frontier
    -> atomic split / transfer
    -> descendant UCI request compiler
    -> no live consumer yet
```

For prefix `(m1,...,mn)`, `common/prefix_dispatch.py` preserves the external
position history, appends `prefix[:-1]`, and restricts the descendant search to
`prefix[-1]`. Exact child legality is certified externally with the existing
Stockfish `go perft 1` oracle.

This milestone does not alter replay v1, routing, VERIFY, budget accounting, or
outward decision authority.


## Shadow REFINE execution

The recursive substrate now has one live research consumer without replacing
RootShardLedger v1:

```text
RootShardLedger v1 EXPLORE
        ↓
raw VERIFY
        ↓
deterministic final-disagreement nomination
        ↓
PrefixShardLedger v2 mirror
        ↓
exact perft-1 split + child-index transfer
        ↓
descendant REFINE stages
```

REFINE uses per-instance shadow positioning while the global runtime position
and unrestricted anchor remain untouched. The coordinator tracks every
temporarily diverged worker and an in-flight descendant oracle so quiesce cannot
release a stale descendant position into the next generation.

REFINE writes a sibling `refinement/` artifact and does not extend Replay
schema v1. Active routing and `BudgetLedger` do not consume it yet.


## Active specialist resource plane

PR #18 adds resource authority without adding chess decision authority:

```text
EXPLORE ───────┐
VERIFY ────────┼─> one BudgetLedger / one wall deadline
REFINE oracle ─┤
REFINE stages ─┤
controller ────┘

Stockfish anchor ─────────────────────> outward bestmove
```

Solver/anchor, VERIFY, and REFINE occupy separate declared CPU/GPU partitions.
Every active VERIFY/REFINE/oracle dispatch must hold a reservation first, and
all dispatched work is settled before the run certificate closes. Raw replay,
VERIFY, and REFINE artifacts remain separated from the routing/audit record.
See `docs/ACTIVE_SPECIALIST_SCHEDULER.md`.


## Unified value-of-compute route plane (M14-G2)

M14-G2 inserts one live routing decision between the completed base VERIFY
barrier and the optional G1 staged VERIFY extension:

```text
clean base VERIFY
      |
      v
live staged features + structural regime
      |
      v
unified_value_v1
   /       \\
 skip      buy
   |        |
   |        v
   |   existing authorize_specialist
   |        |
   |   reserve / deny / dispatch / settle
   |        |
   +--------+
      |
      v
counterfactual evidence update
```

The route decision can recommend buying or skipping compute. It does not own a
BudgetLedger reservation and cannot emit a chess move. The existing specialist
resource gate remains mandatory for every dispatched extension, and the M14-C
DecisionAuthorization type remains the only hybrid outward-authority gate.

Skipping is treated as a shortcut: the exact staged calibration bucket must be
in-domain and observed on held-out data, its predicted decision-change
probability must be below the configured threshold, and the M14-F structural
regime bucket must be in-domain. Any missing/OOD/unvalidated evidence buys more
compute fail-closed when the resource window remains open.

When the staged extension completes, the shared unanimous-VERIFY decision is
re-evaluated from its terminal bestmoves. The sealed counterfactual artifact
records whether its terminal source was base `verification` or
`staged_verification` and hash-binds the latter when used. This is an evidence
update, not a move-authority transfer.
