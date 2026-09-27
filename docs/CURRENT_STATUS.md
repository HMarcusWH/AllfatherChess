# AllfatherChess current build and release status

**Status date:** 27 September 2026  
**Authoritative main commit:** `524ec9b25c7f08d981ba7c88318d106e22586295`  
**Merged milestone:** PR #41 / LOCAL-1 — full-game lifecycle qualification  
**Qualified PR head:** `6fc6522f863e2a15c6d4c230fa558cbb993f867e`  
**Tree identity:** both the qualified PR head and merge commit use tree `0a2095ba5dde83292a348cff2495d02bd4e05299`  
**Current execution milestone:** ONLINE-3 — reproducible service package + pinned lichess-bot bridge  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)  
**Detailed deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
plans/specifications retain design lineage, but this file and `ROADMAP.md` take precedence
for current status and forward ordering.

## Current qualification evidence

PR #41 merged as a normal two-parent merge. Its final qualified head and the merge commit
have the same Git tree, so the exact content that passed qualification is the content now
on `main`.

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
| `allfather.online-hybrid.validation.json` | pinned BLAS/network | yes | yes, route-bound staged | **yes, M14-G3** | current integrated authority reference |

The older profiles remain valuable negative/regression controls. M14-G3 is a new composition,
not permission to erase their firewalls.

## Current critical path

### 1. ONLINE-3 — reproducible package + pinned Lichess bridge — **NEXT**

Package the exact LOCAL-1-qualified composition, pin a tested `lichess-bot` revision,
add immutable deployment/release manifests, restricted bridge configuration, operator
documentation and fake-server startup/game-lifecycle smoke tests. Do not change chess policy
to make packaging easier.

### 2. ONLINE-4 — network/restart/reconciliation/rollback qualification

Exercise disconnects, uncertain move submission, duplicate/out-of-order events, service
restart, stale output, rate limits, storage pressure, stop-new-games and rollback.

### 3. Release qualification

Aggregate the required qualification families into one always-present release gate and
freeze exact source/binary/network/profile/model/bridge/package identities.

### 4. ONLINE-RC

Run the first restricted unrated bot canary only after the operational gates pass.

## Parallel work
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
