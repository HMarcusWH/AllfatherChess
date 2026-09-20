# Build Plan

## Goal

Build a single UCI chess engine that can exceed the strength/compute frontier of its three constituent engines by routing heterogeneous search more intelligently.

The target claim is empirical, not assumed: the hybrid must outperform the strongest constituent under a declared equal hardware and wall-clock budget before any "best engine" claim is made.

## Frozen architectural rules

1. The controller is the engine; Stockfish, Reckless, and LC0 are solver backends.
2. Exploration work is controller-owned and non-overlapping by assigned search region.
3. Deliberate overlap is only legal in an explicit VERIFY / RELOCK phase.
4. Engine-native values are not treated as interchangeable without calibration.
5. If a shortcut is not justified, the system buys more compute or falls back.
6. Every behavioral optimization must be ablated against reproducible baselines.

## Milestones

### M0 — Reproducible three-brain monorepo

- pin exact source commits;
- vendor complete source snapshots with original notices/licenses;
- build all three from one checkout;
- smoke-test UCI startup;
- record provenance and toolchains.

### M1 — Common observability

Add or adapt read-only telemetry so all three solvers expose decision trajectories without changing search behavior.

### M2 — Restricted search regions

Normalize root restrictions. Stockfish and LC0 already expose the required primitives; Reckless must gain an equivalent restricted-root/searchmoves capability.

### M3 — Hybrid UCI shell

Create `allfather-chess` as the only externally visible UCI endpoint and process manager for the three backends.

### M4 — Root ShardLedger

Partition legal root moves into pairwise-disjoint controller-owned regions. Assert that every active legal move has exactly one exploration owner.

### M5 — Recursive shard splitting

Represent search regions as move-prefix shards that may be split, transferred, sealed, or retired atomically.

### M6 — Shadow three-engine search

Run the controller without changing solver behavior. Capture trajectories for replay and counterfactual stopping analysis.

### M7 — Residual geometry

Calibrate Stockfish-vs-Reckless disagreement, LC0-vs-alpha-beta-family disagreement, leader stability, top-k overlap, PV divergence, budget sensitivity, and reversal risk.

### M8 — Adaptive compute routing

Route CPU/GPU/time budgets according to expected marginal decision value rather than fixed equal shares.

### M9 — VERIFY / RELOCK

Permit intentional independent re-search of selected candidates. Track verification compute separately from exploration compute.

### M10 — Cross-feed

Ablate controlled information transfer between backends.

### M11 — Native integration

Replace subprocess boundaries only where measured benefits justify tighter coupling.

### M12 — Strength campaign

Run fixed-node, fixed-time, self-play, SPRT-style, and external-engine comparisons. The final product is promoted only on statistically credible strength gains at equal declared resources.

## Immediate PR train

1. Monorepo bootstrap, pinned imports, provenance, CI.
2. Golden baseline/regression harness.
3. Reckless restricted-root search.
4. Common telemetry contract.
5. Per-engine telemetry adapters.
6. Hybrid UCI shell.
7. Root ShardLedger.
8. Shadow execution + replay.
9. Residual calibration.
10. Adaptive routing.
