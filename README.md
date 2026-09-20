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

The three engine trees were bootstrapped from exact pinned upstream snapshots and now live as **derived Allfather source trees**. Their upstream ancestry is frozen in `vendor.lock.json`; future Allfather modifications are tracked normally in this monorepo and are not expected to remain byte-identical to the imported snapshots.

The repository foundation is now reproducible: CI is read-only, engine ancestry and external NNUE inputs are pinned, and the pre-controller behavior of Stockfish, Reckless, and LC0 is frozen under `tests/baseline/golden/` and verified on every relevant CI run.

See `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

No hybrid routing-strength claim is made yet. Restricted-root search parity is now being established across Stockfish, Reckless, and LC0; the next milestone is a common telemetry contract so the future controller can compare evidence returned from assigned regions without flattening native engine semantics.
