# AllfatherChess

> **Current synchronized build state:** PR #38 / ONLINE-2 is merged at `c499405fd97600546437c1f91b4c0e9e066023cb`; M14-G3 is the active candidate.  
> See [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) for the exact implemented surface,
> current CI evidence, profile incompatibilities, and remaining release gates.

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
15. **ONLINE-1 clock/deadline safety (PR #36)** — an opt-in active CPU profile derives one immutable per-move `TimePlan` from UCI clocks or movetime, preserves original versus actual bounded anchor requests, closes optional-work windows at the soft deadline, applies generation-scoped stop/kill and hard expiry, and hash-binds clock evidence into replay/resource artifacts. It remains anchor-authoritative, CPU-only, and deliberately incompatible with M14-C hybrid authority and recursive REFINE until later qualification.

Documentation: `docs/CURRENT_STATUS.md`, `docs/ONLINE_RELEASE_PLAN.md`, `docs/ONLINE_TIME.md`, `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/UCI_SHELL.md`, `docs/SHARD_LEDGER.md`, `docs/PREFIX_SHARDS.md`, `docs/SHADOW_EXECUTION.md`, `docs/REPLAY_FORMAT.md`, `docs/RESIDUAL_CALIBRATION.md`, `docs/BUDGET_ROUTING.md`, `docs/VERIFY_RELOCK.md`, `docs/COMPARE_RELOCK.md`, `docs/PREFIX_SHARDS.md`, `docs/REFINEMENT.md`, `docs/ACTIVE_SPECIALIST_SCHEDULER.md`, `docs/CROSS_FEED.md`, `docs/CROSS_FEED_ADAPTERS.md`, `docs/SEARCH_REGIMES.md`, `docs/STAGED_VERIFY.md`, `docs/UNIFIED_VALUE_ROUTER.md`, `docs/RESOURCE_ACCOUNTING.md`, `docs/SEARCH_SPACE_OWNERSHIP.md`, `docs/TELEMETRY_SCHEMA.md`, `docs/TELEMETRY_MAPPING.md`, `docs/THEORY_IMPLEMENTATION_MAP.md`, `docs/CLAIM_LEDGER.md`, `docs/ADVERSARIAL_AUDIT.md`, `docs/CODEX_REVIEW_AUDIT.md`, `docs/UPSTREAM_PROVENANCE.md`, and `LICENSES.md`.

**No strength claim is made.** No Elo experiment has been run. Stockfish remains the exact fallback authority, and only the narrow M14-C active `movetime_v0` profile can emit an already-frozen authorized hybrid proposal. Routing/resource authority is separate from move authority, recursive REFINE is excluded from M14-C beyond depth 2, and shadow mode deliberately overspends compute to collect evidence. `docs/CLAIM_LEDGER.md` labels every claim as PROVED, MEASURED, DERIVED, CALIBRATED, POLICY, or OPEN. The fast LC0 random/backend-light profile remains deterministic regression infrastructure only. PR #24 added a separate pinned real-network BLAS qualification profile and recorded-host reference run. M14-B now binds Linux process-CPU/memory evidence into that reference and into active-run `resource.json` certificates. The reference remains explicitly `strength_campaign_eligible = false` until a fixed competitive platform and the later strength campaign are qualified.


## Route to online deployment

The canonical remaining release sequence is [ONLINE_RELEASE_PLAN.md](docs/ONLINE_RELEASE_PLAN.md).
The post-#36 / ONLINE-1 runtime baseline is synchronized in `qualification/release-baseline.json` at `64aa8fd13c390b9b37b8825d8f39e73d9bdbdbf8`.
The opt-in ONLINE-1 clock/deadline layer is specified in [ONLINE_TIME.md](docs/ONLINE_TIME.md).
The ONLINE-2 real-network CPU reference composition is specified in [ONLINE_PROFILE.md](docs/ONLINE_PROFILE.md):

```bash
make online-time-tests
make online-clock-contract        # after building the three baseline engines
make run-allfather-online-clock   # timing validation only; NOT a production bot

make online-profile-tests
make build-online-cpu-reference   # isolated portable CPU-target bundle
make online-profile-contract      # requires independent LC0 qualification report
make run-allfather-online-cpu-reference
```

The new profile uses caller-supplied clocks to derive a per-move CPU/wall envelope,
retains the original and actual anchor requests, and applies independent soft/hard
deadlines. It never grants hybrid move authority. Legacy profiles are unchanged.
A stuck anchor produces an explicit failed request (`bestmove 0000`), not an invented
legal move. ONLINE-2 now composes the real-network LC0 BLAS path with ONLINE timing under one portable CPU profile. The remaining first-canary critical path is M14-G3 clock-aware staged hybrid authority -> LOCAL-1 full-game qualification -> ONLINE-3 packaging -> ONLINE-4 lifecycle/recovery -> release qualification / ONLINE-RC. Production learned SKIP calibration may proceed in parallel; no strength claim is introduced.

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
```
