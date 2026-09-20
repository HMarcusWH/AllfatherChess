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

The repository foundation is now reproducible: CI is read-only, engine ancestry and external NNUE inputs are pinned, and the pre-controller behavior of Stockfish, Reckless, and LC0 is frozen under `tests/baseline/golden/` and verified on every relevant CI run. Restricted-root search parity is established across all three backends, read-only telemetry maps live UCI/native evidence into telemetry v1, PR #9 added the first externally visible AllfatherChess UCI/process shell, and PR #10 adds controller-owned legal-root qualification plus the root ShardLedger.

See `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/UCI_SHELL.md`, `docs/SHARD_LEDGER.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/TELEMETRY_MAPPING.md`, `docs/CODEX_REVIEW_AUDIT.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

No hybrid routing-strength claim is made yet. PR #10 can represent an exact pairwise-disjoint root ownership partition, but live gameplay intentionally remains the PR #9 transparent Stockfish anchor path. The real-engine ShardLedger contract exercises owned regions sequentially only as qualification; it is not shadow execution or a routing policy. The current LC0 random/backend-light validation build remains deterministic regression infrastructure only and is explicitly **not strength-qualified**; real LC0 inference/network/hardware qualification remains a later strength-campaign requirement.


## Run the Generation-1 validation shell

After building the three constituent engines:

```bash
make build-baselines
./allfather-chess
```

Equivalent cross-platform invocation:

```bash
python3 -m controller --config config/allfather.validation.json
```

The validation configuration exists to prove process/UCI semantics, not playing strength.
