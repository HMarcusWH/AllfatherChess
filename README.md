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

The repository foundation is now reproducible: CI is read-only, engine ancestry and external NNUE inputs are pinned, and the pre-controller behavior of Stockfish, Reckless, and LC0 is frozen under `tests/baseline/golden/` and verified on every relevant CI run. Restricted-root search parity is established across all three backends, read-only telemetry maps live UCI/native evidence into telemetry v1, and PR #9 adds the first externally visible AllfatherChess UCI/process shell.

See `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/UCI_SHELL.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/TELEMETRY_MAPPING.md`, `docs/CODEX_REVIEW_AUDIT.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

No hybrid routing-strength claim is made yet. PR #9 is intentionally a transparent anchor shell: all three engines are managed and synchronized, but only Stockfish searches and selects the outward move. The current LC0 random/backend-light validation build remains deterministic regression infrastructure only and is explicitly **not strength-qualified**; real LC0 inference/network/hardware qualification remains a later strength-campaign requirement.


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
