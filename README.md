# AllfatherChess

AllfatherChess is a research monorepo for a residual-aware hybrid chess engine that coordinates **Stockfish**, **Reckless**, and **LC0** under one meta-control layer.

The long-term target is a single UCI engine whose controller owns search-space allocation, compute budgets, verification, and stopping. The three constituent engines remain heterogeneous solver backends rather than being flattened into one search algorithm.

The strength target is **equal-envelope superiority**, not additive brute force: under a declared total resource budget `B`, Allfather must eventually outperform Stockfish, Reckless, and LC0 individually while counting solver compute, verification work, and controller overhead inside the same envelope. Shadow-mode research may intentionally spend more compute to learn where compute matters, but those runs are evidence collection rather than strength claims.

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

The repository foundation is reproducible: CI is read-only, engine ancestry and external NNUE inputs are pinned, and the pre-controller behavior of Stockfish, Reckless, and LC0 is frozen under `tests/baseline/golden/` and verified on every relevant CI run. Restricted-root search parity is established across all three backends, read-only telemetry maps live UCI/native evidence into telemetry v1, PR #9 added the first externally visible AllfatherChess UCI/process shell, and PR #10 added controller-owned legal-root qualification plus the root ShardLedger.

The current stack includes the immediate controller layers plus an explicit common-support VERIFY evidence plane:

1. **shadow execution and replay** — an unrestricted `stockfish-anchor` holds sole outward authority while `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow` search pairwise-disjoint ledger-owned regions and emit replayable telemetry;
2. **residual and counterfactual calibration** — a derived layer turns replay bundles into scale-free disagreement/stability features and a calibrated reversal-risk model, with an enforced firewall against mixing engine evaluation scales;
3. **active adaptive compute routing** — a conservative policy allocates shadow observation compute inside one declared resource envelope, where an instability signal may nominate computation but only a calibrated, in-domain, sufficiently supported, low-risk verdict may authorize stopping;
4. **explicit VERIFY evidence** — after a clean three-owner EXPLORE run, the three distinct owner bestmoves form one deterministic common candidate set and the same three shadow processes independently re-search it. VERIFY remains shadow-only research instrumentation and cannot alter the outward move;
5. **COMPARE / descriptive RELOCK analysis** — offline analysis reconstructs the three common-support trajectories, derives scale-free pairwise and three-way convergence geometry, attributes EXPLORE→VERIFY candidate adoption, and freezes a terminal-suffix RELOCK definition. The result is derived evidence only and is not consumed by routing;
6. **recursive prefix-shard substrate** — `PrefixShardLedger` v2 qualifies deterministic move-prefix ownership, atomic split/transfer semantics, a prefix-free frontier, and descendant-position UCI dispatch;
7. **shadow REFINE execution** — completed raw VERIFY disagreement can nominate one-ply root targets, which are split by the exact Stockfish perft oracle and searched as pairwise-disjoint descendant child regions. REFINE is research instrumentation only and cannot influence the outward anchor.

Documentation: `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/UCI_SHELL.md`, `docs/SHARD_LEDGER.md`, `docs/PREFIX_SHARDS.md`, `docs/SHADOW_EXECUTION.md`, `docs/REPLAY_FORMAT.md`, `docs/RESIDUAL_CALIBRATION.md`, `docs/BUDGET_ROUTING.md`, `docs/VERIFY_RELOCK.md`, `docs/COMPARE_RELOCK.md`, `docs/PREFIX_SHARDS.md`, `docs/REFINEMENT.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/TELEMETRY_MAPPING.md`, `docs/THEORY_IMPLEMENTATION_MAP.md`, `docs/CLAIM_LEDGER.md`, `docs/ADVERSARIAL_AUDIT.md`, `docs/CODEX_REVIEW_AUDIT.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

**No strength claim is made.** No Elo experiment has been run. The outward move is still the unrestricted Stockfish anchor's in every mode, routing governs observation compute only, and shadow mode deliberately overspends compute to collect evidence. `docs/CLAIM_LEDGER.md` labels every claim as PROVED, MEASURED, DERIVED, CALIBRATED, POLICY, or OPEN. The current LC0 random/backend-light validation build remains deterministic regression infrastructure only and is explicitly **not strength-qualified**; real LC0 inference/network/hardware qualification remains a later strength-campaign requirement.


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

The validation configuration exists to prove process/UCI semantics, not playing strength. It is the frozen anchor-only regression baseline and is unchanged by this milestone.

## Run the shadow observatory

```bash
make run-allfather-shadow                # four instances, one outward identity
make run-allfather-verify                # shadow mode + explicit common-support VERIFY
make run-allfather-refine                # shadow mode + VERIFY-triggered one-level REFINE
make verification-analysis               # offline COMPARE / descriptive RELOCK derivation
make verification-evidence-sweep         # research-only common-support corpus sweep
make shadow-evidence-sweep               # collect replay bundles over the frozen corpus
make residual-calibration                # derive features, then fit a reversal-risk model
```

Shadow mode is a research observatory. It intentionally exceeds the eventual competitive resource envelope and carries no equal-compute claim.

## Run active routing

`config/allfather.active.validation.json` ships with `"calibration": null`, which is fail-closed: with no fitted model the policy can never authorize suppression. Point `routing.calibration` at a model produced by `make residual-calibration` to enable gated stopping.

## Validation

```bash
make controller-tests                    # fast, no engines required
make prefix-shard-tests                  # recursive ownership + descendant dispatch compiler
make prefix-shard-contract               # real engines: two-level split + depth-3 restriction
make refinement-tests                    # live shadow REFINE planning/lifecycle/integrity
make refinement-execution-contract       # real engines: VERIFY disagreement -> descendant REFINE
make shadow-execution-contract           # real engines: concurrency, containment, telemetry, replay
make verification-execution-contract     # real engines: disjoint EXPLORE -> common-support VERIFY
make verification-analysis-contract      # derive COMPARE / RELOCK from that exact raw run
make active-routing-contract             # real engines: sweep -> derive -> calibrate -> route
```
