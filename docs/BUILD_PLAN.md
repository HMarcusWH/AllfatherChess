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
7. Upstream ancestry and current derived-engine source are separate identities: the engine trees may evolve locally without pretending to remain byte-identical to their imported baselines.
8. CI validates committed expectations; it does not record new golden behavior, silently mutate source, or re-vendor engine trees.

## Milestones

### M0 — Reproducible three-brain monorepo

- pin exact source commits and source trees;
- vendor complete source snapshots with original notices/licenses;
- record tracked-entry counts and provenance;
- build all three from one checkout;
- smoke-test UCI startup.

### M0.1 — Foundation hardening

Before behavior-changing chess work:

- make `vendor.lock.json` the single source of truth for imported ancestry;
- separate immutable upstream ancestry from evolving Allfather engine trees;
- remove CI source mutation and auto-vendoring;
- validate normal pushes to `main`;
- cryptographically pin externally downloaded engine artifacts;
- ensure destructive source refresh requires an explicit operator action.

### M1 — Golden regression and common observability foundation

Freeze the baseline behavior of all three constituent engines before changing search semantics. Establish deterministic test positions, build/run manifests, crash/hang detection, legal best-move checks, and baseline fingerprints.

Then add or adapt read-only telemetry so all three solvers expose decision trajectories without changing search behavior.

### M2 — Restricted search regions

Normalize root restrictions. Stockfish and LC0 already expose the required primitives; Reckless must gain an equivalent restricted-root/searchmoves capability.

### M3 — Hybrid UCI shell

Create `allfather-chess` as the only externally visible UCI endpoint and process manager for the three backends.

### M4 — Root ShardLedger

Partition legal root moves into pairwise-disjoint controller-owned regions. Assert that every active legal move has exactly one exploration owner.

### M5 — Shadow three-engine search

Run synchronized three-engine searches without changing final move selection. Capture trajectories for replay and counterfactual stopping analysis.

### M6 — Residual geometry

Calibrate Stockfish-vs-Reckless disagreement, LC0-vs-alpha-beta-family disagreement, leader stability, top-k overlap, PV divergence, budget sensitivity, and reversal risk.

### M7 — Adaptive compute routing

Route CPU/GPU/time budgets according to expected marginal decision value rather than fixed equal shares.

### M8 — Recursive shard splitting

Represent search regions as move-prefix shards that may be split, transferred, sealed, or retired atomically.

### M9 — VERIFY / RELOCK

Permit intentional independent re-search of selected candidates. Track verification compute separately from exploration compute.

### M10 — CPU/GPU resource scheduler

Turn external resource limits into controller-wide CPU/GPU/time allocations without multiplying a GUI request independently across all three engines.

### M11 — Cross-feed

Ablate controlled information transfer between backends.

### M12 — Native integration

Replace subprocess boundaries only where measured benefits justify tighter coupling.

### M13 — Strength campaign

Run fixed-node, fixed-time, self-play, SPRT-style, and external-engine comparisons. The final product is promoted only on statistically credible strength gains at equal declared resources.

## Immediate PR train

1. **PR #1 — Monorepo bootstrap**: pinned imports, provenance foundation, baseline build and UCI smoke. **Merged.**
2. **PR #2 — Foundation hardening**: read-only CI, authoritative lockfile, derived-tree-safe provenance, pinned engine inputs. **Merged.**
3. **PR #3 — Golden three-engine regression harness**: deterministic corpus, fresh-process recorder/verifier, legal-move cross-checks, manifests. **Merged.**
4. **PR #4 — Finalize frozen golden gate**: commit the pre-controller snapshots and switch CI permanently from record mode to verify mode. **Merged.**
5. **PR #5 — Reckless restricted-root search**: add UCI `searchmoves` / equivalent controller-owned root restriction. **Merged.**
6. **PR #6 — Common telemetry contract**: freeze engine-neutral telemetry vocabulary and backend-specific extension rules. **Merged.**
7. **PR #7 — Per-engine telemetry implementation**: Stockfish, Reckless, and LC0 adapters/emitters without active routing. **Merged.**
8. **PR #8 — Historical Codex review hardening**: retire actionable review debt before introducing the hybrid process shell. **Merged.**
9. **PR #9 — Hybrid UCI shell**: one external UCI endpoint plus three backend process adapters. **Current.**
10. **PR #10 — Root ShardLedger**: pairwise-disjoint exploration allocation.
11. **PR #11 — Shadow execution and replay**: synchronized three-engine runs with no routing intervention.
12. **PR #12 — Residual calibration**: disagreement/convergence features and counterfactual stop labels.
13. **PR #13 — Adaptive budget routing**: active CPU/GPU/time allocation.
14. **PR #14 — Recursive shard splitting**.
15. **PR #15 — Explicit VERIFY / RELOCK**.
16. **PR #16 — CPU/GPU resource scheduler**.
17. **PR #17+ — Cross-feed, native integration, and strength optimization**, each promoted only after isolated ablation.

## Foundation-hardening acceptance gate

PR #2 is complete only when:

- normal pull requests are validated;
- pushes to `main` are validated;
- CI has no source-control write permission;
- engine repository/commit/tree identities are defined only in `vendor.lock.json`;
- provenance checks do not require live derived trees to equal upstream byte-for-byte;
- Reckless's current NNUE is fetched only under an exact locked size and SHA-256;
- Stockfish, Reckless, and LC0 all build;
- all three UCI smoke tests pass;
- no engine search-semantic source file changes occur in PR #2.

The post-merge `main` workflow run is itself part of this gate because validating that trigger is one of the bugs this PR fixes.

## Frozen-golden acceptance gate

The first search-semantic engine change must not begin until:

- `tests/baseline/golden/legal_moves.json` is committed;
- Stockfish, Reckless, and LC0 golden snapshots are committed;
- CI runs `golden-baselines.py --verify`, never `--record`;
- a clean PR run proves the committed snapshots reproduce on the declared baseline profile;
- ordinary unrestricted engine behavior remains the reference against which later semantic changes are reviewed.

## Restricted-root acceptance gate

PR #5 is complete only when:

- unrestricted Stockfish, Reckless, and LC0 golden verification remains unchanged and green;
- Reckless accepts UCI `go ... searchmoves ...` with `searchmoves` last;
- an absent restriction remains unrestricted;
- an explicit restriction resolving to zero legal moves remains zero-root/fail-closed inside Reckless;
- native root ordering is preserved after filtering;
- native and WASM root construction use the same restriction helper;
- helper threads inherit the same restricted root vector;
- Syzygy ranks only the already-filtered root set;
- valid non-empty restricted-root assignments produce authorized best moves and PV root heads in Stockfish, Reckless, and LC0;
- no frozen golden file is regenerated or updated.

## Telemetry-contract acceptance gate

PR #6 is complete only when:

- telemetry v1 is defined as a replayable raw event stream with `search.started`, `candidate.update`, `native.event`, `terminal.fact`, and `search.complete`;
- standard and Chess960 move encodings are explicit;
- sequence ordering and adapter observation time are distinct from backend-reported time;
- MultiPV observations remain `multipv_index` events rather than fabricated atomic ranking snapshots;
- engine evaluation and work values retain explicit semantic provenance and are not treated as interchangeable across backends;
- LC0 defect telemetry is preserved losslessly under native schemas, including raw internal move identifiers;
- terminal facts require independent provenance and perspective; a null best move does not imply terminality;
- controller execution mode remains distinct from the existing EXPLORE / COMPARE / REFINE / VERIFY / RELOCK / STOP phase vocabulary;
- raw telemetry rejects derived residual, overlap, ranking, and routing fields;
- valid Stockfish, Reckless, LC0, Chess960, native-defect, and terminal JSONL fixtures pass;
- deliberately invalid lifecycle/semantic fixtures are rejected;
- the validator uses only Python standard-library dependencies;
- no file under `engines/**`, `tests/baseline/golden/**`, or `vendor.lock.json` changes in PR #6;
- the frozen baseline and restricted-root gates remain green.

## Telemetry-adapter acceptance gate

PR #7 is complete only when:

- Stockfish, Reckless, and LC0 UCI search output maps into telemetry v1 without modifying any engine source;
- shared parsing is syntactic only while engine-specific semantic tags are assigned in backend adapters;
- one physical PV-bearing UCI line produces one `candidate.update`, with remaining evidence retained in that event's native payload rather than duplicated;
- non-PV info lines remain `native.event` and null best moves never manufacture `terminal.fact`;
- LC0 ScoreType is explicit adapter configuration and every supported ScoreType keeps a distinct semantic tag;
- LC0 MultiPV=1 lines without an explicit `multipv` token normalize to `multipv_index = 1` without creating an atomic ranking claim;
- LC0 `player`, `gameid`, and `side` bestmove metadata remains losslessly available under a native schema;
- LC0 hidden defect telemetry is exercised through `--show-hidden`, with SUMMARY required in live integration and ITER parsing covered statically;
- malformed structured defect JSON fails explicitly instead of disappearing;
- adapter-observed time is captured as lines are dequeued, not reconstructed after a search has completed;
- static stdlib-only adapter tests pass;
- live Stockfish, Reckless, LC0, and LC0-defect JSONL streams validate against the frozen telemetry v1 contract;
- existing frozen-golden and restricted-root gates remain green;
- `engines/**`, `tests/baseline/golden/**`, `tests/harness/normalize.py`, `vendor.lock.json`, and `controller/**` remain unchanged.


## Historical Codex-review hardening gate

PR #8 is complete only when:

- all actionable Codex findings from the requested PR history are reconciled in `docs/CODEX_REVIEW_AUDIT.md`;
- Reckless zero-root search remains fail-closed with Syzygy enabled paths guarded against empty roots;
- a Threads=2 zero-root search followed by an unrestricted search in the same process succeeds;
- Reckless build/test commands load the crate-local Cargo target configuration;
- Reckless cannot silently download an unverified fallback NNUE during a monorepo build, the default no-`EVALFILE` source-build path uses the same pinned verifier on Linux, macOS, and Windows, and Cargo tracks the exact resolved NNUE so same-path corruption forces revalidation;
- LC0 defect telemetry is emitted on the normal completed-search path before bestmove, waits for full worker quiescence only when defect telemetry is enabled, preserves ordinary bestmove timing otherwise, and emits no duplicate summary from destructor fallback;
- telemetry v1 rejects non-finite numbers anywhere in common payloads (including unknown nested extension objects/arrays), while keeping event-level `native.data` opaque; it also rejects non-canonical moves, boolean versions, nested derived common fields, invalid terminal provenance, and known work-unit mismatches;
- telemetry stream payloads cannot overwrite immutable event metadata;
- focused regression fixtures/tests exercise each repaired validation boundary, including same-size NNUE cache corruption/recovery and finite-vs-non-finite unknown common telemetry extensions;
- frozen golden, restricted-root, telemetry-contract, static-adapter, live-adapter, and cross-platform Reckless source-build gates all remain green;
- the backend-light/random LC0 regression configuration is explicitly non-strength-qualified. A real inference backend/network/hardware qualification remains mandatory before the strength campaign.


## Hybrid UCI shell acceptance gate

PR #9 is complete only when:

- `allfather-chess` / `python -m controller` is the only externally visible UCI identity and constituent backend `id` / `option` handshake chatter does not leak;
- Stockfish, Reckless, and LC0 are launched transactionally, configured from `config/allfather.validation.json`, synchronized on game/position/Chess960 state, and shut down without orphaned child processes;
- production backend process management uses exactly one stdout reader per engine and demultiplexes protocol waiters from search callbacks so `isready` cannot race an active search reader;
- PR #9 runs in explicit anchor mode: Stockfish alone receives `go`, `stop`, and `ponderhit`; Reckless and LC0 remain managed and ready but do not search;
- state-changing commands cannot desynchronize backends during an active search; duplicate `go` is rejected until the current search completes;
- `isready` returns `readyok` during `go infinite` without terminating the search, and `stop` then produces exactly one anchor `bestmove`;
- backend startup/readiness/process failure fails closed and never silently promotes another constituent engine to active search;
- the deterministic direct-Stockfish node contract and the Allfather anchor contract return the same best move under the same validation configuration;
- the validation configuration remains explicitly non-strength-qualified and no LC0/random-backend result is promoted as a strength claim;
- fast fake-backend process/frontend tests and the real three-engine shell contract are green;
- every pre-existing frozen golden, restricted-root, telemetry, Reckless portability, and historical-hardening gate remains green;
- no file under `engines/**`, `tests/baseline/golden/**`, `schemas/telemetry/**`, or `vendor.lock.json` changes in PR #9;
- PR #9 introduces no ShardLedger, residual calculation, engine voting, Reckless/LC0 shadow search, automatic fallback, or resource-routing policy.
