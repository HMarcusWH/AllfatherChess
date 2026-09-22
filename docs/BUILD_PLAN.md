# Build Plan

## Goal

Build a single UCI chess engine that can exceed the strength/compute frontier of its three constituent engines by routing heterogeneous search more intelligently.

The target claim is empirical, not assumed: Allfather must ultimately outperform **each** constituent engine in head-to-head testing while operating inside the same declared total resource envelope. Solver compute, verification work, controller overhead, CPU/GPU occupancy, memory, and wall-clock effects must be accounted rather than hidden behind three simultaneous full-budget searches.

Shadow research is intentionally different from the final competitive regime. It may overspend compute to collect counterfactual evidence, but no shadow result is itself an equal-compute strength claim.

## Frozen architectural rules

1. The controller is the engine; Stockfish, Reckless, and LC0 are solver backends.
2. Exploration work is controller-owned and non-overlapping by assigned search region.
3. Deliberate overlap is legal only when explicitly tagged as VERIFY work; RELOCK is a derived/control conclusion and never weakens EXPLORE ownership.
4. Engine-native values are not treated as interchangeable without calibration.
5. If a shortcut is not justified, the system buys more compute or falls back.
6. Every behavioral optimization must be ablated against reproducible baselines.
7. Upstream ancestry and current derived-engine source are separate identities: the engine trees may evolve locally without pretending to remain byte-identical to their imported baselines.
8. CI validates committed expectations; it does not record new golden behavior, silently mutate source, or re-vendor engine trees.
9. The final active controller is budget-constrained globally: constituent compute, VERIFY / RELOCK work, and controller overhead all spend from the same declared envelope.
10. Shadow mode is an observatory, not a strength mode: it may collect deliberately over-budget evidence, but it cannot be used to claim equal-compute superiority.

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

### M5 — Shadow three-engine search (implemented)

Run synchronized restricted Stockfish, Reckless, and LC0 shadow searches while a separate unrestricted Stockfish anchor remains the sole outward bestmove authority. Capture raw trajectories, exact owned root sets, run metadata, and ledger snapshots for deterministic replay and later counterfactual stopping analysis.

The shadow milestone is permitted to use extra research compute. It does not yet implement residual calibration, voting, routing, or an equal-budget strength claim.

### M6 — Residual geometry (implemented, partially answerable)

Calibrate leader stability, top-k overlap, PV divergence, budget sensitivity, and reversal risk. Implemented in `controller/residuals.py` and `controller/calibration.py`.

Stockfish-vs-Reckless and LC0-vs-alpha-beta disagreement still have **empty shared support during EXPLORE**. M8 now supplies explicit raw common-support VERIFY evidence on the three EXPLORE nominees, and M9 derives the corresponding pairwise/three-way geometry without weakening the exploration invariant.

### M7 — Adaptive compute routing (implemented for observation compute)

Route CPU/GPU/time budgets according to expected marginal decision value rather than fixed equal shares. Implemented in `controller/budget.py` and `controller/routing.py` as `mode: "active"`.

Scope limit: the router allocates **shadow observation compute**. The unrestricted anchor remains the sole outward decision authority, so no routing decision can change the move. Extending routing to the decision itself requires evidence this milestone does not have.

### M8 — Explicit common-support VERIFY execution (implemented)

Permit intentional independent re-search of the three clean EXPLORE nominees while leaving RootShardLedger ownership pairwise-disjoint. VERIFY v1 is deterministic shadow-mode instrumentation, stored in a separate raw artifact, and cannot affect the outward Stockfish anchor.

### M9 — COMPARE / RELOCK derived analysis (implemented)

Use the common-support VERIFY trajectories to derive scale-free cross-engine comparisons, synchronized three-way convergence descriptors, EXPLORE-to-VERIFY preference changes, candidate-source attribution, and a frozen descriptive `terminal-suffix-v1` RELOCK state. Agreement is evidence, not a correctness certificate, and no live router consumes this artifact.

### M10 — Recursive shard splitting substrate (implemented in PR #16)

Represent search regions as deterministic move-prefix shards with a prefix-free frontier. A sealed leaf may be split atomically into an exact externally-certified child set, leased children inherit ownership, and leased frontier shards may be transferred atomically. A separate compiler maps a full prefix to descendant `position` + final `searchmoves`. PR #16 qualifies the substrate without migrating live shadow execution or letting COMPARE/RELOCK trigger refinement.

### M11 — Shadow REFINE execution + recursive provenance (implemented in PR #17)

Consume completed raw VERIFY disagreement facts as deterministic refinement nominations, mirror completed root-v1 ownership into PrefixShardLedger v2, enumerate one exact perft-1 child shell, partition it pairwise-disjointly, and execute descendant REFINE stages in shadow mode. REFINE remains observational and outside the active equal-resource envelope.

### M12 — Active VERIFY / REFINE + CPU/GPU resource scheduler (implemented in PR #18)

Charge EXPLORE, VERIFY, REFINE, the descendant child oracle, controller overhead, CPU/GPU occupancy, and wall time through one controller-wide envelope. Active specialist work is now reservation-before-dispatch with separate solver, VERIFY, and REFINE partitions; a run may claim compliance only when both the global envelope and the declared specialist partitions remain satisfied.

### M13 — Typed cross-feed + counterfactual hybrid decisions

Formalize the information already produced by EXPLORE -> common-support VERIFY -> optional REFINE as a typed, provenance-bound cross-feed evidence plane.

M13 does **not** add another cross-engine search protocol merely to create "cross-feed". The expensive common-support operation already exists in VERIFY. The new layer must consume existing evidence, preserve native score semantics, preserve EXPLORE ownership, and initially remain decision-inert.

Then freeze counterfactual hybrid decisions from that evidence while Stockfish remains sole outward authority. This creates prospective decision records that can later be scored without hindsight leakage.

### M14 — Decision-relevant calibration, backend/resource qualification, and bounded hybrid authority

Build chess-specific value-of-compute labels; qualify a real LC0 backend/network/hardware profile; separate reserved estimates from measured process/accelerator consumption; and only then permit a tightly bounded hybrid proposal to replace the anchor move.

The first hybrid-authority path must remain fail-closed: missing/incomplete evidence, unsupported request classes, stale generations, invalid budget state, or unavailable frozen proposals all return the Stockfish anchor move.

After active hybrid authority is established, extend recursive REFINE, add engine-specific cross-feed adapters, classify search regimes, and unify resource routing with decision authorization without collapsing those authorities.

### M14.5 — Measured transport / selective native integration

Compare current UCI process boundaries against structured IPC and native adapters. Replace subprocess/text boundaries only where measured latency/control benefits justify the tighter coupling and parity tests show no unexplained semantic drift.

Native integration is an optimization of an already-demonstrated hybrid mechanism, not the mechanism's proof of value.

### M15 — Governed policy evolution + strength campaign

Qualify controller-policy candidates offline under frozen train/calibration/holdout boundaries, then run fixed-node, fixed-time, measured-resource, self-play, paired-match, SPRT-style, and external-engine comparisons.

The final product is promoted only on statistically credible strength gains against each declared constituent baseline under the same declared/measured resource contract.

## Immediate PR train

1. **PR #1 — Monorepo bootstrap**: pinned imports, provenance foundation, baseline build and UCI smoke. **Merged.**
2. **PR #2 — Foundation hardening**: read-only CI, authoritative lockfile, derived-tree-safe provenance, pinned engine inputs. **Merged.**
3. **PR #3 — Golden three-engine regression harness**: deterministic corpus, fresh-process recorder/verifier, legal-move cross-checks, manifests. **Merged.**
4. **PR #4 — Finalize frozen golden gate**: commit the pre-controller snapshots and switch CI permanently from record mode to verify mode. **Merged.**
5. **PR #5 — Reckless restricted-root search**: add UCI `searchmoves` / equivalent controller-owned root restriction. **Merged.**
6. **PR #6 — Common telemetry contract**: freeze engine-neutral telemetry vocabulary and backend-specific extension rules. **Merged.**
7. **PR #7 — Per-engine telemetry implementation**: Stockfish, Reckless, and LC0 adapters/emitters without active routing. **Merged.**
8. **PR #8 — Historical Codex review hardening**: retire actionable review debt before introducing the hybrid process shell. **Merged.**
9. **PR #9 — Hybrid UCI shell**: one external UCI endpoint plus three backend process adapters. **Merged.**
10. **PR #10 — Root ShardLedger**: pairwise-disjoint exploration allocation. **Merged.**
11. **PR #11 — Shadow/replay design contract**: freeze the four-process observatory topology, authority boundary, and replay requirements. **Merged.**
12. **PR #12 — Immediate controller stack**: implement shadow execution/replay, residual/counterfactual calibration, and active adaptive budget routing as three separate layers with separate artifacts and test suites. **Merged.**
13. **PR #13 — PR #12 closure and hardening**: retire replay-discovery and envelope-validation debt before adding deliberate overlap. **Merged.**
14. **PR #14 — Explicit common-support VERIFY execution**. **Merged.**
15. **PR #15 — COMPARE / RELOCK derived analysis**. **Merged.**
16. **PR #16 — Recursive PrefixShardLedger v2 + descendant dispatch qualification**. **Merged.**
17. **PR #17 — Shadow REFINE execution + recursive provenance**. **Merged.**
18. **PR #18 — Active VERIFY / REFINE + CPU/GPU resource scheduler**. **Merged.**
19. **PR #19 — Extended post-PR18 build-plan documentation**. **Merged.**
21. **PR #21 — Typed cross-feed evidence plane, shadow-only**: consume existing VERIFY/REFINE evidence; no new search phase; no outward behavior change. **Merged.**
22. **PR #22 — Counterfactual hybrid decision laboratory**: freeze deterministic hybrid proposals while Stockfish remains outward authority. **Current.**
23. **PR #23 — Prospective chess-specific value-of-compute calibration**: decision-relevant labels with strict no-hindsight boundaries.
24. **PR #24 — Strength-qualified LC0 profile**: real network/backend/hardware identity; preserve fast random-backend CI separately.
25. **PR #25 — Measured process resource accounting**: preserve reservations while adding measured CPU/GPU/memory/controller cost where supported.
26. **PR #26 — Active hybrid decision authority v0**: bounded in-memory authorization at the anchor-completion boundary; deterministic Stockfish fallback.
27. **PR #27 — Multi-level recursive REFINE**: bounded deeper prefix splitting under exact ownership and budget rules.
28. **PR #28 — Engine-specific cross-feed adapters**: source-typed candidate transfer into subprocess-safe VERIFY/REFINE/adjudication operations.
29. **PR #29 — Search-regime classifier**: calibrated state classes that may nominate work but do not authorize moves.
30. **PR #30 — Unified value-of-compute decision router**: combine regime, route-value, resource authorization and separate decision authorization.
31. **PR #31 — Structured IPC experiment**: compare typed local transport against current UCI boundaries.
32. **PR #32 — Selective native integration**: embed only measured-value hooks with process-adapter parity.
33. **PR #33 — Offline governed policy evolution laboratory**: immutable candidate generations, holdout evaluation, rollback and promotion discipline.
34. **PR #34 — Strength campaign infrastructure**: frozen openings, hardware profiles, match manifests, ablations and statistical stopping rules.
35. **PR #35 — External qualification / release candidate**: freeze the first claim-bounded equal-resource release.

The detailed file-by-file plan, authority transitions, and acceptance gates for PR #21 onward live in `docs/EXTENDED_BUILD_PLAN.md`.

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


## Root ShardLedger acceptance gate

PR #10 is complete only when:

- the managed runtime obtains the live legal-root universe from Stockfish `go perft 1` without introducing a second chess move generator;
- the legal-root parser accepts only canonical lowercase UCI roots, requires every depth-1 count to equal one, rejects duplicates, and requires `Nodes searched` to equal the parsed root count;
- terminal positions produce a valid empty candidate universe;
- the legal-root oracle follows synchronized standard/Chess960 move encoding and is cross-checked against the frozen legal-move corpus;
- `RootShardLedger` preserves candidate input order and creates deterministic one-root-per-shard IDs for a declared generation;
- the authorized owner set is explicit and immutable for the ledger;
- partition assignment is atomic, one-shot, exact-coverage, and rejects missing/extra owners, unknown roots, malformed roots, duplicate roots, omissions, and cross-owner overlap before mutating any shard;
- the root-v1 state machine is limited to `UNASSIGNED -> LEASED -> ACTIVE -> SEALED`;
- activation and sealing are owner-atomic, and an empty owner region can never be emitted as a backend dispatch;
- successful mutations advance a monotonic revision and failed mutations leave the snapshot/revision unchanged;
- concurrent conflicting partition attempts cannot create partial or overlapping ownership;
- snapshots are stable JSON-serializable detached copies suitable for later replay work;
- a real-engine qualification partitions a live nonterminal root universe in test code only, then runs Stockfish, Reckless, and LC0 sequentially under their ledger-owned `searchmoves` and requires bestmove/PV heads to remain inside the owned region;
- the round-robin qualification partition remains test-only and is not promoted into production routing policy;
- live gameplay remains the PR #9 Stockfish anchor path and external bestmove selection is unchanged;
- the implementation guarantees assigned-prefix non-overlap only and makes no claim of global board-state/transposition non-overlap;
- no file under `engines/**`, `tests/baseline/golden/**`, `schemas/telemetry/**`, `adapters/telemetry/**`, `config/allfather.validation.json`, or `controller/uci_frontend.py` changes in PR #10;
- PR #10 introduces no concurrent three-engine search, residual/ranking/voting logic, adaptive assignment, shard transfer, recursive split, VERIFY overlap, fallback policy, or resource-routing policy;
- every pre-existing baseline, restricted-root, telemetry, controller-shell, and Reckless portability/hardening gate remains green.


## Immediate controller stack acceptance gate

### Shadow execution and replay

Complete only when:

- the external UCI decision path remains unrestricted Stockfish-anchor authority; no shadow observation may alter, replace, vote on, or otherwise select the outward bestmove;
- shadow execution uses distinct managed engine instances: `stockfish-anchor` for outward authority and `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow` for restricted evidence collection;
- `RootShardLedger` governs only the three shadow exploration owners; the unrestricted anchor is deliberately outside shard ownership and does not weaken pairwise-disjoint ownership among shadow workers;
- every dispatched shadow worker consumes exactly its non-empty `ledger.active_roots(owner)` set through the already-qualified `searchmoves` primitive;
- empty owner regions are not dispatched and terminal empty ledgers remain valid;
- all shadow streams use telemetry v1 with `controller.execution_mode = shadow`, distinct `engine_instance` values, and engine-native score/work semantics preserved;
- raw telemetry remains free of residuals, aggregate rankings, overlap metrics, voting, routing decisions, and calibrated cross-engine score conversions;
- one run-level replay manifest binds the synchronized position, external request, engine/build/config identities, pre-dispatch ledger snapshot, exact shadow root sets, raw stream identities/locations, completion or failure disposition, and final ledger snapshot;
- replay artifacts are deterministic enough to reconstruct what each backend observed and which roots it was authorized to search, without requiring live controller policy;
- shadow process failure is recorded explicitly and never silently promotes another backend to outward authority;
- stop/quit lifecycle terminates all active shadow work without orphaned processes, while the outward UCI frontend still emits at most the anchor's single bestmove for the search;
- fixed-node validation proves semantic non-intervention at the outward Stockfish decision boundary; no timed-search equivalence claim is made unless CPU/GPU resource isolation is separately established;
- base EXPLORE shadow execution introduces no candidate voting, recursive shard transfer/split, cross-feed, or strength claim; deliberate VERIFY overlap is opt-in, separately represented, and cannot affect outward decision authority;
- shadow runs are explicitly marked as research evidence that may exceed the eventual competitive resource envelope;
- every pre-existing baseline, restricted-root, telemetry, controller-shell, ShardLedger, and portability/hardening gate remains green.

### Residual and counterfactual calibration

Complete only when:

- residual features are derived from replay bundles only, never written into raw telemetry or a replay manifest;
- combining two differently-tagged engine values is a runtime error, not a convention: Stockfish cp minus Reckless cp, and LC0 scalar against alpha-beta cp, both raise;
- cross-engine features carry their shared-support size and report undefined-with-a-reason when the support is empty;
- within-engine margins and engine-native work counters always carry their semantics tags;
- counterfactual labels are descriptive and no label is named or usable as correctness;
- derived artifacts are content-addressed and record the exact run ids and content hashes they came from;
- calibration features are past-only, so the same function serves training and live routing;
- calibration is evaluated out of sample on a split by run identity and carries a reliability table;
- calibration fails closed on unknown buckets, low support, foreign extractor versions, mismatched feature sets, and empty evidence;
- synthetic fixtures cover stable agreement, transient disagreement, late reversal, LC0-only divergence, alpha-beta-only divergence, failed and missing streams, terminal positions, Chess960, and malformed telemetry.

### Active adaptive budget routing

Complete only when:

- all solver work, verification reserve, and controller overhead spend from one declared envelope;
- compute is reserved before it is spent so concurrency cannot exceed the envelope;
- controller overhead is charged with a monotonic clock and appears in the ledger;
- engine-native counters are kept per semantics with no scalar total;
- an instability signal may nominate computation but only a calibrated, in-domain, sufficiently supported, low-risk verdict may authorize suppression;
- a denied proposal degrades to continued observation or a hold, never to an improvised action;
- missing calibration, out-of-domain calibration, an exhausted wall budget, or a refused reservation all resolve conservatively;
- a declared but unloadable calibration refuses to construct a router rather than degrading silently;
- routing decisions are deterministic under fixed evidence and a fixed clock;
- every decision is written as an audit certificate with its proposal, gates, calibration verdict, thresholds, and budget snapshot, outside the raw bundle;
- no routing decision can change outward decision authority, proved by fixed-node equivalence with direct Stockfish;
- no Elo or strength claim is made anywhere.
