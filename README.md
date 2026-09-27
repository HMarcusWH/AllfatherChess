# AllfatherChess

> **Current synchronized build state:** PR #39 / M14-G3 is merged at `c301e9986566febfbb7978d55c5a3d3429423cff`; LOCAL-1 full-game lifecycle qualification is next.  
> See [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) for the exact implemented surface and
> [docs/ROADMAP.md](docs/ROADMAP.md) for the canonical forward execution order.

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

1. **shadow execution and replay** — an unrestricted `stockfish-anchor` is the default/fallback authority while `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow` search pairwise-disjoint ledger-owned regions and emit replayable telemetry;
2. **residual and counterfactual calibration** — a derived layer turns replay bundles into scale-free disagreement/stability features and a calibrated reversal-risk model, with an enforced firewall against mixing engine evaluation scales;
3. **active adaptive compute routing** — a conservative policy allocates shadow observation compute inside one declared resource envelope, where an instability signal may nominate computation but only a calibrated, in-domain, sufficiently supported, low-risk verdict may authorize stopping;
4. **explicit VERIFY evidence** — after a clean three-owner EXPLORE run, the three distinct owner bestmoves form one deterministic common candidate set and the same three shadow processes independently re-search it. In shadow mode this remains deliberately over-budget research instrumentation; in active mode it is reservation-backed. VERIFY itself carries no outward authority;
5. **COMPARE / descriptive RELOCK analysis** — offline analysis reconstructs the three common-support trajectories, derives scale-free pairwise and three-way convergence geometry, attributes EXPLORE→VERIFY candidate adoption, and freezes a terminal-suffix RELOCK definition. The result is derived evidence only and is not consumed by routing;
6. **recursive prefix-shard substrate** — `PrefixShardLedger` v2 qualifies deterministic move-prefix ownership, atomic split/transfer semantics, a prefix-free frontier, and descendant-position UCI dispatch;
7. **bounded recursive REFINE (M14-D)** — completed raw VERIFY disagreement seeds one-shell REFINE, and clean restricted regional terminals may nominate SEALED child prefixes for deterministic breadth-first re-expansion. Every deeper oracle/stage receives fresh resource authorization, exact Stockfish perft children, atomic PrefixShardLedger split/transfer, containment validation and restoration. All legacy/hybrid profiles remain pinned to depth 2;
8. **active specialist scheduling** — active VERIFY, each REFINE child oracle, and every REFINE descendant stage require explicit reservations from separate specialist CPU/GPU partitions inside the same run-wide envelope. Resource authority is distinct from chess decision authority;
9. **measured physical resource accounting (M14-B)** — reservations remain admission authority while Linux procfs measures backend CPU/RSS, controller process CPU is measured independently of wall time, and `resource.json` is hash-bound into the active route certificate. Missing required physical evidence fails the measured-envelope claim closed;
10. **bounded hybrid decision authority (M14-C)** — only an already-frozen PRE_ANCHOR proposal in the qualified active `movetime_v0` profile may replace the Stockfish move after a separate fail-closed `DecisionAuthorization` gate. Every denial preserves exact Stockfish fallback. M14-D forbids deeper-than-one-shell REFINE in this authority profile;
11. **engine-specific cross-feed adapters (M14-E)** — a separate adapter-only evidence projection preserves source family/search/prefix/candidate-universe context, exposes bounded `VERIFY_SET` and evidence-supported `REFINE_PREFIX` UCI proposals for Stockfish, Reckless and LC0, and keeps source ranks/alarms native and context-local. The adapters do not dispatch, reserve resources, route, or participate in move authority;
12. **search-regime classifier (M14-F)** — sealed typed evidence is summarized into multi-label structural hypotheses such as RELOCK-based stable convergence, VERIFY disagreement, native mate rupture, and recursive REFINE productivity. Unsupported policy-diffusion/endgame/time-critical hypotheses remain explicit rather than inferred from fake proxies, while a separate support model can mark novel structural buckets out-of-domain. Regimes do not route work or authorize moves;
13. **same-process staged VERIFY / serve-compatible value-of-compute (M14-G1)** — after one clean base VERIFY round, an explicitly configured research profile may buy a fresh larger-budget VERIFY round on the same three managed solver processes and exact candidate universe. Base and extension work receive separate reservations/measurements; the extension is sealed separately, cannot coexist with hybrid move authority, and trains a distinct past-only decision-change model rather than reusing the PR #23 whole-run calibration.
14. **unified value-of-compute routing (M14-G2)** — the live `unified_value_v1` router evaluates the clean base VERIFY state before the staged extension. It may skip that extension only when the exact staged decision-change bucket has held-out support, predicted decision-change risk is below the declared threshold, and the M14-F regime-support bucket is in-domain. Otherwise it fails closed to buying more compute; actual dispatch still requires the existing specialist resource authorization. A completed extension may update the frozen counterfactual decision evidence, but route authority remains separate from resource and move authority.
15. **ONLINE-1 clock/deadline safety (PR #36)** — an opt-in active CPU profile derives one immutable per-move `TimePlan` from UCI clocks or movetime, preserves original versus actual bounded anchor requests, closes optional-work windows at the soft deadline, applies generation-scoped stop/kill and hard expiry, and hash-binds clock evidence into replay/resource artifacts. The historical ONLINE-1 validation profile remains anchor-authoritative and CPU-only.
16. **ONLINE-2 real-network online CPU reference (PR #38)** — packages portable Stockfish/Reckless builds with the pinned BLAS-LC0 network/backend identity under ONLINE timing/resource contracts while retaining Stockfish outward authority.
17. **M14-G3 clock-aware staged hybrid authority (PR #39)** — composes ONLINE-2, G1/G2 staged VERIFY, frozen route-bound proposal evidence, clock-aware DecisionAuthorization, deadline-safe outward publication, deterministic Stockfish fallback, and post-output resource/replay sealing. The qualification requires a genuine real-backend non-anchor HYBRID emission.

Documentation: `docs/README.md`, `docs/CURRENT_STATUS.md`, `docs/ROADMAP.md`, `docs/ONLINE_RELEASE_PLAN.md`, `docs/ONLINE_TIME.md`, `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/UCI_SHELL.md`, `docs/SHARD_LEDGER.md`, `docs/PREFIX_SHARDS.md`, `docs/SHADOW_EXECUTION.md`, `docs/REPLAY_FORMAT.md`, `docs/RESIDUAL_CALIBRATION.md`, `docs/BUDGET_ROUTING.md`, `docs/VERIFY_RELOCK.md`, `docs/COMPARE_RELOCK.md`, `docs/PREFIX_SHARDS.md`, `docs/REFINEMENT.md`, `docs/ACTIVE_SPECIALIST_SCHEDULER.md`, `docs/CROSS_FEED.md`, `docs/CROSS_FEED_ADAPTERS.md`, `docs/SEARCH_REGIMES.md`, `docs/STAGED_VERIFY.md`, `docs/UNIFIED_VALUE_ROUTER.md`, `docs/RESOURCE_ACCOUNTING.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/TELEMETRY_MAPPING.md`, `docs/THEORY_IMPLEMENTATION_MAP.md`, `docs/CLAIM_LEDGER.md`, `docs/ADVERSARIAL_AUDIT.md`, `docs/CODEX_REVIEW_AUDIT.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

**No strength claim is made.** No equal-envelope Elo/superiority campaign has been completed.
PR #39 establishes a qualified real-network, clock-aware staged hybrid authority composition,
not that its HYBRID choices are stronger. Stockfish remains the deterministic fallback on
any denied/unsupported authority state. The existing random/backend-light LC0 profiles remain
regression infrastructure; the real BLAS/network identity is separately pinned by ONLINE-2
and reused by M14-G3. Full-game lifecycle, deployment, and the formal strength campaign remain
separate gates. `docs/CLAIM_LEDGER.md` labels the evidence boundary explicitly.


## Route to online deployment

The canonical forward sequence is [ROADMAP.md](docs/ROADMAP.md); the deeper deployment audit
remains [ONLINE_RELEASE_PLAN.md](docs/ONLINE_RELEASE_PLAN.md). The synchronized release
baseline is `qualification/release-baseline.json`.

The currently qualified integrated profile is documented in
[ONLINE_HYBRID_AUTHORITY.md](docs/ONLINE_HYBRID_AUTHORITY.md). On a clean Linux checkout,
install the ONLINE-2 real-inference build dependencies first, then reproduce the same
prerequisite order used by the dedicated G3 workflow:

```bash
make online-hybrid-tests
make build-online-cpu-reference   # creates build/online-cpu-reference and builds real BLAS LC0
make lc0-strength-contract        # proves the same real LC0 backend/network identity
make online-hybrid-contract       # consumes the frozen ONLINE-2 bundle
make run-allfather-online-hybrid
```

PR #39 completed M14-G3. The remaining first-canary path is now:

```text
LOCAL-1 full-game lifecycle qualification
    -> ONLINE-3 reproducible package + pinned lichess-bot bridge
    -> ONLINE-4 network/restart/reconciliation/rollback qualification
    -> aggregate release qualification
    -> ONLINE-RC restricted unrated bot canary
```

M14-G4 production SKIP calibration and M15-B/C equal-resource strength work proceed on
separate promotion tracks. ONLINE operation is not a substitute for the strength campaign.

### LOCAL-1 full-game qualification candidate

The current LOCAL-1 candidate is documented in
[docs/FULL_GAME_QUALIFICATION.md](docs/FULL_GAME_QUALIFICATION.md). It pins Fastchess,
derives the playing G3 runtime from the already-qualified online-hybrid profile, records the
exact Fastchess↔engine UCI boundary, independently parses chess/PGN state, and joins every
validated G3 ply back to replay/decision/resource evidence.

It also freezes a **descriptive five-arm same-clock baseline**:

```text
Stockfish
Reckless
LC0
Allfather-Anchor   # one Stockfish process through the legacy native-clock shell
Allfather-G3       # full real-network staged hybrid composition
```

This is not an equal-resource experiment: Allfather-G3 uses a different time-allocation and
multi-process compute topology. The wrapper arm therefore must not be read as an isolated
causal estimate of "controller tax".

```bash
make local-full-game-tests
make build-fastchess
make local-full-game-qualification
```

The manual 200-game engineering soak is workflow-sharded so a single serial job cannot time
out and erase the campaign evidence.

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
make run-allfather-refine                # compatibility profile: VERIFY-triggered depth-2 REFINE
make run-allfather-recursive-refine      # active evidence-only bounded recursive REFINE
make verification-analysis               # offline COMPARE / descriptive RELOCK derivation
make verification-evidence-sweep         # research-only common-support corpus sweep
make shadow-evidence-sweep               # collect replay bundles over the frozen corpus
make residual-calibration                # derive features, then fit a reversal-risk model
make run-allfather-staged-verify         # M14-G1 research profile: base VERIFY -> same-process extension
make run-allfather-unified-value          # M14-G2: live base-round route -> staged VERIFY or skip
```

Shadow mode is a research observatory. It intentionally exceeds the eventual competitive resource envelope and carries no equal-compute claim.

## Run active routing

`config/allfather.active.validation.json` ships with `"calibration": null`, which is fail-closed: with no fitted model the policy can never authorize suppression. Point `routing.calibration` at a model produced by `make residual-calibration` to enable gated stopping.

For the active specialist validation profile:

```bash
make run-allfather-active-specialist
```

That profile enables reservation-backed VERIFY and compatibility depth-2
REFINE under explicit specialist reserves. The separate recursive validation
profile exercises deeper M14-D zoom without hybrid move authority. Both remain
validation infrastructure, not strength-qualified engine configurations.

## Validation

```bash
make controller-tests                    # fast, no engines required
make prefix-shard-tests                  # recursive ownership + descendant dispatch compiler
make prefix-shard-contract               # real engines: two-level split + depth-3 restriction
make refinement-tests                    # live shadow REFINE planning/lifecycle/integrity
make refinement-execution-contract       # real engines: VERIFY disagreement -> descendant REFINE
make recursive-refinement-contract       # deterministic live multi-level scheduler + resource gate
make active-specialist-tests              # fast specialist reserve/settlement integration
make active-specialist-contract           # real engines: active VERIFY/REFINE under one envelope
make crossfeed-adapter-tests              # pure engine-specific translation + semantic firewalls
make crossfeed-adapter-contract           # real engines: adapter-generated VERIFY/REFINE restrictions
make regime-tests                         # structural multi-label classifier
make regime-calibration-tests             # support/domain calibration and split firewall
make regime-contract                      # deterministic classifier + fail-closed OOD contract
make shadow-execution-contract           # real engines: concurrency, containment, telemetry, replay
make verification-execution-contract     # real engines: disjoint EXPLORE -> common-support VERIFY
make verification-analysis-contract      # derive COMPARE / RELOCK from that exact raw run
make active-routing-contract             # real engines: sweep -> derive -> calibrate -> route
make staged-verification-tests           # fast staged causal/runtime boundaries
make staged-value-of-compute-tests       # base-feature / future-label firewall
make staged-decision-calibration-tests   # position-group support + fail-closed OOD
make staged-verify-contract              # real engines: same-process base -> extension mechanism
make unified-value-router-tests           # fail-closed route-value gates
make unified-value-router-contract        # real engines: route -> resource auth -> staged evidence update
make online-hybrid-tests                  # M14-G3 clock/evidence/authority regressions
make build-online-cpu-reference           # prerequisite: portable real-network bundle
make lc0-strength-contract                # prerequisite: prove real LC0 backend/network
make online-hybrid-contract               # real-network G3 positive HYBRID + fallback qualification
```
