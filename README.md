# AllfatherChess

AllfatherChess is a research monorepo for a residual-aware hybrid chess engine that coordinates **Stockfish**, **Reckless**, and **LC0** under one meta-control layer.

The long-term target is a single UCI engine whose controller owns search-space allocation, compute budgets, verification, and stopping. The three constituent engines remain heterogeneous solver backends rather than being flattened into one search algorithm.

## End-state idea

```text
GUI / tournament
      |
      v
AllfatherChess UCI frontend
      |
      v
Meta-controller
  |- shard ownership
  |- residual / disagreement tracking
  |- CPU/GPU budget routing
  |- verify / re-lock
  '- fallback
      |
      +--> Stockfish  (alpha-beta / NNUE)
      +--> Reckless   (alpha-beta / NNUE)
      '--> LC0        (MCTS / policy-value NN)
```

During exploration, search regions are controller-owned and disjoint. Deliberate overlap is allowed only in an explicit verification phase.

## Repository status

This repository is being bootstrapped from exact pinned snapshots of the three source engines. See `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

The first milestone is deliberately conservative: reproduce the three baselines in one checkout before changing search behavior.
