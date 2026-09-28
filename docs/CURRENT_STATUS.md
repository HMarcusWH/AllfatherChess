# AllfatherChess current build and release status

**Status date:** 28 September 2026  
**Authoritative main commit:** `7248f25fc64be4d04a78ec2b1f0c9de2986a11a5`  
**Latest merged milestone:** PR #44 / ENGINE-OPT-V2 — measured constituent/profile optimization  
**Qualified PR head:** `085420843b95f3f2dd206fc1c66bf642cbd49b6d`  
**Tree identity:** qualified PR head and merge commit share tree `80fae798aed99a5e37fcfb7fce321a7e01a2fc56`  
**Current execution milestone:** M14-J — Adaptive Resource Orchestration  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)  
**M14-J rebuild plan:** [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md)  
**Detailed deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
plans/specifications retain design lineage, but this file and `ROADMAP.md` take precedence
for current status and forward ordering.

## Current qualification evidence

PR #44 / ENGINE-OPT-V2 qualified on exact candidate head `085420843b95f3f2dd206fc1c66bf642cbd49b6d` and was merged as `7248f25fc64be4d04a78ec2b1f0c9de2986a11a5` with the same Git tree `80fae798aed99a5e37fcfb7fce321a7e01a2fc56`. The merged tree therefore matches the tree that closed the final exact-head qualification gates.

| Workflow | Run | Result |
| --- | ---: | --- |
| Telemetry contract validation | 36458789547 | success |
| Controller shell validation | 36458789275 | success |
| Merge gate | 36458789123 | success |
| LC0 real-inference qualification | 36458789157 | success |
| ONLINE-2 real-network online profile qualification | 36458789327 | success |
| M14-G3 online staged hybrid authority qualification | 36458789431 | success |
| Baseline engine validation | 36458789271 | success |
| **LOCAL-1 full-game lifecycle and five-arm baselines** | **36458789252** | **success** |
| **ENGINE-OPT-V2 aggregate** | **36458789423** | **success** |

The retained ENGINE-OPT-V2 aggregate artifact is `10990541766`, SHA-256 `080678710a7df45a44d008df5ef484fbcfa3c01911d1a50a5cb0a96cbc3b07d1`. The exact-head aggregate reported `passed: true`, `promotion_ready: true`, no errors, three repeat confirmations of the selected LC0 profile, a real non-anchor G3-v2 authority witness, and successful LOCAL-1-v2 lifecycle evidence. The ordinary v1 LOCAL-1 control also reran successfully; its retained artifact is `10990165658`, SHA-256 `a57b65603866b8444bfb325b43cd3d9809650910a1eaf389a5e2382b995384e7`.

## LOCAL-1 qualification result

The required campaign passed with **44/44 LOCAL-1 regressions**, the mandatory fault campaign
and forced rule-transition probes green, **28/28 required games independently validated**,
and **zero qualification errors**. The same-clock five-arm results remain descriptive rather
than an Elo/equal-resource claim.

Across the 800 validated Allfather-G3 plies in the retained artifact, authority was
**796 ANCHOR_FALLBACK** and **4 HYBRID**. Two HYBRID decisions changed the Stockfish anchor
move. These counts are **MEASURED coverage**, not a strength result.

## ENGINE-OPT-V2 qualification result

The promoted CPU reference keeps portable PGO Stockfish at 16 MiB hash, portable x86-64 Reckless at 16 MiB hash, and BLAS LC0 network 791556 with the selected `b7-p8-c256k-warm64` profile: `NNCacheSize=262144`, `MinibatchSize=7`, `MaxPrefetch=8`, adaptive prefetch off, defect telemetry off and a 64-node startup warmup.

Across the three exact-head confirmation repeats, the selected LC0 profile preserved the frozen 8/8 move vector with median wall time in the range **329.0025–331.5455 ms**. The frozen v1 baseline measured **1291.427–1316.554 ms** median wall time. The aggregate used the conservative worst-selected / fastest-baseline ratio **0.25672802256728405**. These are profile-efficiency and repeatability measurements, not Elo or playing-strength evidence.

The exact-head LOCAL-1-v2 campaign validated 28/28 required games with no qualification errors. Its descriptive authority coverage was `native=2606`, `ANCHOR_FALLBACK=580`, `HYBRID=289`, with 74 actual anchor-changing overrides. Those counts establish exercised authority coverage only; they do not establish that the overrides improve chess strength.

## What is implemented and qualified

The repository now contains, in code and contract tests:

1. pinned derived Stockfish, Reckless and LC0 source trees with reproducible provenance;
2. frozen constituent goldens and restricted-root parity;
3. one external Allfather UCI/process shell;
4. controller-owned legal-root qualification and pairwise-disjoint RootShardLedger EXPLORE;
5. four-process shadow/replay execution with source-typed telemetry;
6. residual/counterfactual analysis and conservative active resource routing;
7. explicit common-support VERIFY and descriptive COMPARE/RELOCK;
8. PrefixShardLedger v2 and bounded recursive REFINE;
9. specialist reservation-before-dispatch and measured Linux process/controller CPU evidence;
10. typed cross-feed plus engine-specific proposal adapters;
11. M14-C bounded hybrid authority for its historical `movetime_v0` profile;
12. real-network LC0 BLAS qualification;
13. M14-F structural regime classification/support calibration;
14. M14-G1 same-process staged VERIFY;
15. M14-G2 unified BUY/SKIP value-of-compute routing;
16. ONLINE-1 clock-derived `TimePlan`, soft/hard deadline fencing and stale-generation protection;
17. ONLINE-2 real-network hardware-bound online CPU reference composition;
18. **M14-G3 clock-aware staged hybrid authority**, composing the real-network ONLINE profile,
    staged VERIFY and an explicit fail-closed outward DecisionAuthorization gate;
19. **LOCAL-1 complete-game lifecycle qualification**, including full-history game execution,
    explicit rule witnesses, mandatory failure injection, replay/process/resource integrity,
    and the five-arm same-clock descriptive baseline;
20. **ENGINE-OPT-V2 exact-head qualification**, including the cross-run-stable LC0 profile,
    portable Stockfish PGO, owner-specific resource reservations, real G3-v2 authority evidence,
    ordinary v1 lifecycle control and complete LOCAL-1-v2 lifecycle qualification.

## M14-G3 result

PR #39 changes the authority boundary from "hybrid proposals are research/counterfactual
or narrow movetime-only authority" to a qualified ONLINE composition:

```text
TimePlan
  -> disjoint EXPLORE
  -> base VERIFY
  -> G2 BUY_STAGED_VERIFY
  -> same-process staged VERIFY extension
  -> frozen staged proposal
  -> clocked DecisionAuthorization
       -> HYBRID
       -> exact Stockfish fallback
  -> deadline-safe bestmove publication
```

The reference qualifier requires at least one real-backend case where the staged proposal
differs from the Stockfish anchor and that HYBRID move is actually written outward. A
bookkeeping-only HYBRID equal to the anchor is insufficient.

Authority remains fail-closed for stale generation, partial/mixed evidence, failed route or
resource gates, illegal proposals, explicit stop/revocation, late evidence, hard expiry and
other unsupported states.

## Timing/resource/lifecycle boundary after PR #39

PR #39 also hardened the causal boundaries needed before whole-game work:

- a closed shadow dispatch permit is a normal typed rejection, not a backend failure;
- outward publication is atomically fenced against stop/hard-expiry races;
- client-visible `bestmove` is fenced against post-output `isready`/next-command races;
- process endpoints freeze before backend reuse;
- controller CPU remains inside the measured interval through resource-relevant route
  finalization and reservation settlement;
- the complete resource interval freezes before post-move protocol readiness;
- an already-published move cannot later be rewritten as cancelled or replaced by a second
  terminal move;
- replay-only backlog is distinct from physical engine quiescence.

These are lifecycle/resource integrity properties, not playing-strength claims.

## Current profile roles

| Profile | Real LC0 | Clock TimePlan | Staged VERIFY/G2 | Hybrid outward authority | Role |
| --- | --- | --- | --- | --- | --- |
| `allfather.online-clock.validation.json` | random | yes | no composed staged authority | no | ONLINE-1 timing regression |
| `allfather.online.cpu-reference.json` | pinned BLAS/network | yes | no hybrid authority | no | ONLINE-2 real-inference reference |
| `allfather.hybrid.validation.json` | random | historical movetime-only path | no staged composition | M14-C only | bounded authority regression |
| `allfather.unified-value.validation.json` | random | no ONLINE composition | yes | no | M14-G2 routing regression |
| `allfather.online-hybrid.validation.json` | pinned BLAS/network | yes | yes, route-bound staged | **yes, M14-G3** | current qualified integrated authority reference |
| `allfather.online-engine-opt-v2.json` | selected BLAS/network profile | yes | no hybrid authority | no | qualified ENGINE-OPT-V2 CPU reference / frozen orchestration fallback substrate |
| `allfather.online-hybrid-v2.validation.json` | selected BLAS/network profile | yes | yes, route-bound staged | yes, same G3 policy | qualified ENGINE-OPT-V2 hybrid reference / frozen M14-J fallback |

The older profiles remain valuable negative/regression controls. M14-G3 is a new composition,
not permission to erase their firewalls.

## Current critical path

### 1. M14-J — Adaptive Resource Orchestration — **NEXT**

Move host, game-clock, engine-profile and progressive work allocation under one versioned controller resource policy while preserving ENGINE-OPT-V2 as the exact fail-closed fallback. The detailed implementation sequence is frozen in [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md).

M14-J does not authorize arbitrary live hyperparameter synthesis. It introduces prequalified engine operating points, host/game capability detection, typed WorkGrants, a progressive deterministic allocator and replay-bound resource provenance. ResourceAuthorization remains separate from DecisionAuthorization.

### 2. META-1 — authority-value control experiment

Run orchestrated HYBRID against an orchestrated ANCHOR_CONTROL with the same host, allocator, resource plan, constituent work and evidence. Only the final permission for a HYBRID proposal to replace the Stockfish anchor differs. This isolates move-authority value from time-management and compute-allocation differences.

### 3. ONLINE-PLAY-1 — production-profile 10+5 experiment

Compare the exact production profiles at the intended 10+5 operating regime and report actual CPU, wall and native work consumption. This is an operational production-profile comparison, not the M15-B/C equal-resource superiority campaign.

### 4. ONLINE-3 — reproducible package + pinned Lichess bridge

Package the exact qualified orchestrated composition, pin a tested `lichess-bot` revision, add immutable deployment/release manifests, restricted bridge configuration, operator documentation and fake-server startup/game-lifecycle smoke tests.

### 5. ONLINE-4 — network/restart/reconciliation/rollback qualification

Exercise disconnects, uncertain move submission, duplicate/out-of-order events, service restart, stale output, rate limits, storage pressure, stop-new-games and rollback.

### 6. Release qualification

Aggregate the required qualification families into one always-present release gate and freeze exact source/binary/network/profile/allocator/bridge/package identities.

### 7. ONLINE-RC

Run the first restricted unrated bot canary only after the orchestration and operational gates pass.

## Parallel work

**M14-G4 production SKIP calibration** may proceed alongside ONLINE-3/ONLINE-4. It becomes
mandatory before learned compute suppression is promoted, but a conservative canary may
continue to BUY/deny/fallback when shortcut evidence is unsupported.

**M15-B/C equal-resource strength qualification** remains the separate route to any
superiority claim. Online operation or rating does not replace that campaign.

M14-H/I native transport and M15-A governed policy evolution remain later optimization tracks.

## Administrative release gates

- `main` protection / ruleset remains an operator governance task; issues #26/#27 track it;
- combined-distribution licensing/compliance still requires an explicit top-level decision;
- deployment host/resource class, a fresh Lichess BOT account/token and initial opponent
  allow-list remain operator inputs.

## Claim boundary

The repository now has a **qualified ENGINE-OPT-V2 real-network, clock-aware staged hybrid composition with complete-game lifecycle evidence**. PR #44 also qualifies a substantially cheaper, repeatable LC0 CPU profile and preserves the frozen authority/resource contracts. It does not establish Elo, constituent superiority, equal-compute superiority or deployment readiness. M14-J is forward architecture work and has no behavioral authority until its own declared qualification closes.
