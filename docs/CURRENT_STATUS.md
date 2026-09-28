# AllfatherChess current build and release status

**Status date:** 28 September 2026  
**Authoritative main commit:** `b8bd0fdda4a7e67f6437a74e5373f69ef2c15c78`  
**Merged milestone:** PR #41 / LOCAL-1 — full-game lifecycle qualification  
**Qualified PR head:** `6fc6522f863e2a15c6d4c230fa558cbb993f867e`  
**Tree identity:** both the qualified PR head and merge commit use tree `0a2095ba5dde83292a348cff2495d02bd4e05299`  
**Current execution milestone:** ENGINE-OPT-V2 / PR #44 — measured constituent/profile optimization before ONLINE-3  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)  
**Detailed deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
plans/specifications retain design lineage, but this file and `ROADMAP.md` take precedence
for current status and forward ordering.

## Current qualification evidence

PR #41 remains the qualified behavior baseline. PR #43 subsequently synchronized repository status/docs and merged as `b8bd0fdda4a7e67f6437a74e5373f69ef2c15c78` without changing the qualified chess/controller behavior. ENGINE-OPT-V2 is being developed separately in PR #44; its v2 selection is not yet a qualified replacement.

| Workflow | Run | Result |
| --- | ---: | --- |
| Telemetry contract validation | 36344180892 | success |
| Controller shell validation | 36344180852 | success |
| Merge gate | 36344180834 | success |
| LC0 real-inference qualification | 36344180739 | success |
| ONLINE-2 real-network online profile qualification | 36344180754 | success |
| M14-G3 online staged hybrid authority qualification | 36344180746 | success |
| Baseline engine validation | 36344180968 | success |
| **LOCAL-1 full-game lifecycle and five-arm baselines** | **36344180962** | **success** |

The exact-head LOCAL-1 artifact is GitHub Actions artifact `10941548187`, SHA-256
`23d2b1f8baa2b535e92e523c3a3dbd79219213127015a708daa50d49c3395d21`.
Automated exact-head Codex review was unavailable after the review quota was exhausted, so
no exact-head Codex-review claim is made; the runtime/qualification gates above are the
promotion evidence.

## LOCAL-1 qualification result

The required campaign passed with **44/44 LOCAL-1 regressions**, the mandatory fault campaign
and forced rule-transition probes green, **28/28 required games independently validated**,
and **zero qualification errors**. The same-clock five-arm results remain descriptive rather
than an Elo/equal-resource claim.

Across the 800 validated Allfather-G3 plies in the retained artifact, authority was
**796 ANCHOR_FALLBACK** and **4 HYBRID**. Two HYBRID decisions changed the Stockfish anchor
move. These counts are **MEASURED coverage**, not a strength result.

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
    and the five-arm same-clock descriptive baseline.

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
| `allfather.online-engine-opt-v2.json` | selected BLAS/network profile | yes | no hybrid authority | no | PR #44 optimized CPU reference candidate |
| `allfather.online-hybrid-v2.validation.json` | selected BLAS/network profile | yes | yes, route-bound staged | yes, same G3 policy | PR #44 optimized hybrid candidate |

The older profiles remain valuable negative/regression controls. M14-G3 is a new composition,
not permission to erase their firewalls.

## Current critical path

### 1. ENGINE-OPT-V2 / PR #44 — measured constituent/profile optimization — **NEXT**

Keep the PR #41/LOCAL-1 v1 composition frozen as the control while measuring and repairing
the execution profile. The v2 programme targets LC0 CPU cache/minibatch/prefetch/warmup,
safe phase-specific specialist options, portable constituent build optimization and
owner-specific resource reservation estimates. It preserves the n16 EXPLORE / n16 VERIFY /
n32 staged-VERIFY intervention and does not change G3's fail-closed authority semantics.

The first exact-head ENGINE-OPT aggregate rejected the original p0 LC0 choice after one
frozen rook-endgame bestmove changed across hosted CPU environments. The revised selection
uses the cross-run-stable warm b7/p8 256k profile and now requires three exact-head
confirmation repeats of both baseline and selected profile. In parallel, the ordinary v1 LOCAL-1 control first exposed missing replay bundles when
per-run directory creation exceeded the 100 ms pre-anchor observation budget. After that
directory-only repair, run `36440361529` isolated the remaining failure to anchor telemetry
writer construction. The coordinator now prepares the complete next replay slot off the
clocked path: directory, open anchor stream, and writer thread. The request-time claim is a
same-filesystem rename plus in-memory adapter binding; the missing-evidence gate is unchanged.

The revised selection remains **not a qualified replacement for v1** until a fresh exact-head
ENGINE-OPT aggregate closes, including repeatability, real-process G3-v2 authority/resource
evidence, the ordinary v1 LOCAL-1 control and complete LOCAL-1-v2 lifecycle.

### 2. ONLINE-3 — reproducible package + pinned Lichess bridge

Package the exact LOCAL-1-qualified composition, pin a tested `lichess-bot` revision,
add immutable deployment/release manifests, restricted bridge configuration, operator
documentation and fake-server startup/game-lifecycle smoke tests. Do not change chess policy
to make packaging easier.

### 3. ONLINE-4 — network/restart/reconciliation/rollback qualification

Exercise disconnects, uncertain move submission, duplicate/out-of-order events, service
restart, stale output, rate limits, storage pressure, stop-new-games and rollback.

### 4. Release qualification

Aggregate the required qualification families into one always-present release gate and
freeze exact source/binary/network/profile/model/bridge/package identities.

### 5. ONLINE-RC

Run the first restricted unrated bot canary only after the operational gates pass.

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

The repository now has a **qualified real-network, clock-aware staged hybrid authority
composition that is also qualified across the declared complete-game lifecycle**. It is not
yet packaged/deployed as a bot, network/restart qualified, or supported by an equal-envelope
playing-strength campaign.
