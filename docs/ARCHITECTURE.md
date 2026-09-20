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

## Generations

### Generation 1 — process-isolated monorepo

One checkout provides four process boundaries:

- `allfather-chess` / `python -m controller` — the only external UCI endpoint;
- Stockfish baseline;
- Reckless baseline;
- LC0 baseline.

The controller communicates with engine processes through a production UCI process adapter with one permanent stdout reader per backend. In the first Generation-1 implementation all three engines are launched, configured, synchronized, and health-checked, while Stockfish alone acts as the transparent search anchor. Reckless and LC0 remain ready until later shard/shadow milestones authorize them to search.

### Generation 2 — structured local adapters

Replace text parsing where useful with richer local IPC while preserving process isolation.

### Generation 3 — native adapters

Embed solver APIs where the measured overhead or required control granularity justifies it.

### Generation 4 — cross-fed heterogeneous search

Permit controlled information transfer such as LC0 policy hints to alpha-beta move ordering and alpha-beta tactical alarms to LC0 candidate prioritization.

## Central state

The controller will maintain an engine-neutral state containing:

- current position and legal moves;
- active `SearchRegion` / shard ownership;
- per-engine observations;
- normalized candidate rankings and PV summaries;
- intra-alpha-beta and cross-paradigm residuals;
- resource budget;
- route history;
- verification / re-lock state;
- hard terminal evidence such as legal mate or tablebase facts.

Engine-native values remain tagged with their source and semantics.
