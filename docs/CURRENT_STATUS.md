# AllfatherChess current build and release status

**Status date:** 1 October 2026  
**Authoritative main commit:** `6412f46b5543bb0339f3928a206ea3b8b173ff98`  
**Latest merged milestone:** PR #55 / M14-J J8 — clamp-only adaptive outer resource plan  
**Qualified J6 PR head:** `6aecd0bae7848ca8a9893377fadffb049d336c3a`  
**Frozen fallback:** ENGINE-OPT-V2 / PR #44 remains unchanged  
**Current execution milestone:** PR #56 / M14-J J9 — WorkGrant compatibility scheduler  
**Next milestone after J9:** M14-J J10 — deterministic adaptive allocator  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)  
**M14-J rebuild plan:** [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md)  
**Detailed deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
plans/specifications retain design lineage, but this file and `ROADMAP.md` take precedence
for current status and forward ordering.

## Current qualification evidence

PR #52 / J6 qualified on exact candidate head
`6aecd0bae7848ca8a9893377fadffb049d336c3a` and merged as
`fb690e435da39808adaa5c02345c77d407bc8043`. The J6 resource laboratory itself is green
and complete; it grants no production profile-selection authority.

| Workflow | Run | Result |
| --- | ---: | --- |
| Telemetry contract validation | 36754169780 | success |
| Controller shell validation | 36754169720 | success |
| Merge gate | 36754169457 | success |
| LC0 real-inference qualification | 36754169356 | success |
| ONLINE-2 real-network online profile qualification | 36754169477 | success |
| M14-G3 online staged hybrid authority qualification | 36754169369 | success |
| Baseline engine validation | 36754169685 | success |
| ENGINE-OPT-V2 | 36754169587 | success |
| **Resource profile laboratory v1 / J6** | **36754169712** | **success** |
| LOCAL-1 v1 control | 36754169403 | failed before games: duplicate G3 positive-witness rediscovery |

The retained J6 artifact is `11117218402`, SHA-256
`e8b153b1cb0eeafeb18796931e3cad116c09f8913a3eb61ac7d60806fc7ec0e7`.
Its independent report states `evidence_valid: true`, `lab_complete: true`,
57 candidate operating points, **1368/1368** Stage-A measurements and **72/72** Stage-B
composition batches with zero execution errors. All nine current-v2 reference buckets had
usable positive high-resolution process-CPU measurements. Twenty-four transient affinity
observation faults were retained as observer evidence rather than misclassified as engine
failures. The report explicitly states `promotion_ready: false` and requires J7 frozen
profile selection.

The one red LOCAL-1 control on the PR #52 head was not a full-game regression: LOCAL-1
aborted before any games because its internal G3 prerequisite independently reran the
performance-sensitive non-anchor positive-witness search and did not rediscover one on that
GitHub-hosted worker. The dedicated M14-G3 workflow on the same source head independently
passed and did produce a real non-anchor HYBRID witness. The post-merge repair separates those
orthogonal responsibilities: M14-G3 remains the positive-witness gate, while LOCAL-1 requires
fresh exact-profile G3 mechanism/deadline/resource/replay evidence before lifecycle games.

PR #44 / ENGINE-OPT-V2 remains the frozen orchestration fallback. Its exact-head aggregate
reported `promotion_ready: true`, three repeat confirmations of the selected LC0 profile,
a real non-anchor G3-v2 authority witness, and successful LOCAL-1-v2 lifecycle evidence.
J6 did not rewrite that fallback.

## M14-J J7 selection freeze — MERGED / PR #54

J7 is implemented as an evidence-backed selection layer **beside** the existing J3/J4 runtime
catalog. The runtime-consumed `qualification/resource-profile-catalog-v1.json` remains unchanged,
keeps `selection_enabled=false`, and retains ENGINE-OPT-V2 as the fail-closed fallback.

The frozen J6 evidence is reduced into `resource-profile-evidence-v1.json`, and
`resource-profile-selection-v1.json` deterministically selects one Pareto operating point for each
of the nine family/work-budget groups using the declared lexicographic rule. Seven groups retain
`v2-current`; the two non-v2 selections are Stockfish n64 with `Hash=32` and LC0 n32 with
`MaxPrefetch=0`.

Every J7 row is marked `isolated_resource_profile` with
`composition_qualification=not_established`. J7 grants no runtime profile-selection authority,
resource authorization, outward move authority, deployment authority, or strength/Elo claim.
Stage B remains evidence about the exact compositions it measured; it does not promote the new
Stockfish n64 or LC0 n32 selections into composition-qualified settings.

## M14-J J8 adaptive outer resource plan — MERGED / PR #55

J8 preserves the existing `clock_envelope_v1` `TimePlan` as the clock/deadline and
G3-compatible timing authority. It adds a separate content-addressed `MoveResourcePlan`
below that ceiling. The J8 plan may preserve or reduce the per-move CPU/resource allowance
from live HostCapabilities and the frozen four-slot J3 composition, but it may not extend the
TimePlan wall/CPU/GPU envelope or its soft/hard deadlines.

The J8 validation profile derives from the anchor-authoritative ENGINE-OPT-V2 runtime and
keeps the fixed `conservative_v1` stage behavior. It does not consume J7's isolated
Stockfish `Hash=32` or LC0 `MaxPrefetch=0` selections, create WorkGrants, enable hybrid
DecisionAuthorization, or establish composition qualification/generic-host portability.
Incomplete host capacity falls back to the unchanged ENGINE-OPT-V2 / `clock_envelope_v1`
execution while denying the adaptive resource claim.

## M14-J J9 WorkGrant compatibility scheduler — PR #56 candidate

J9 keeps the merged J8 `MoveResourcePlan` as the per-move ceiling and adds typed
`WorkGrant` authorization for the historical fixed EXPLORE/VERIFY/staged-VERIFY sequence.
The conservative router remains the sole `BudgetLedger` authority: one admitted grant owns
one reservation, one optional engine dispatch, and one release or settlement.

The J3 resource-profile catalog remains unchanged with `work_chunk_ids=[]`. A separate
`work-grant-scheduler-v1` compatibility overlay binds the exact J3 catalog/profile digests
and freezes nine compatibility chunks: three family-specific n16 EXPLORE grants, three n16
VERIFY grants, and three n32 STAGED_VERIFY grants. The inherited CPU reservations remain
legacy compatibility estimates rather than newly promoted measured cost bounds.

A parent `MoveResourcePlan` in FALLBACK bypasses J9 and preserves exact J8 legacy behavior.
On an ADAPTIVE plan, grant denial, expiry, phase-option mismatch, or a closed dispatch window
means no engine write. J9 still does not consume the J7 Hash32/MaxPrefetch0 selections, convert
REFINE, choose computation by value, or grant outward move authority. J10 owns adaptive grant
selection.

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

### 1. M14-J J7 — Freeze qualified resource profiles — **NEXT**

J0-J6 are merged. J7 now consumes the retained exact-head J6 evidence and freezes only
predeclared, repeatable engine/composition operating points into versioned selection/evidence
artifacts. ENGINE-OPT-V2 remains the exact fail-closed fallback until later orchestration
stages qualify.

M14-J still does not authorize arbitrary live hyperparameter synthesis. ResourceAuthorization
remains separate from DecisionAuthorization, and J6 engineering-efficiency evidence is not a
strength claim.

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

The repository now has a **qualified ENGINE-OPT-V2 fallback plus completed J6 exact-head resource-laboratory evidence**. J6 measured candidate efficiency and whole-composition interference under a bound execution domain, but deliberately granted no profile-selection, move-authority, Elo, superiority or deployment claim. J7 is the next promotion boundary.
