# AllfatherChess current build and release status

**Status date:** 26 September 2026  
**Authoritative main commit:** `c301e9986566febfbb7978d55c5a3d3429423cff`  
**Merged milestone:** PR #39 / M14-G3 — clock-aware staged hybrid authority  
**Qualified PR head:** `9ea858eed4133ea1bc8e89137176b4e5cf2eb316`  
**Tree identity:** both the qualified PR head and merge commit use tree `397c004a90adf7e1d666ebd10c2fd81617906b23`  
**Current execution milestone:** LOCAL-1 — full-game lifecycle qualification  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)  
**Detailed deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
plans/specifications retain design lineage, but this file and `ROADMAP.md` take precedence
for current status and forward ordering.

## Current qualification evidence

PR #39 merged as a normal two-parent merge. Its final qualified head and the merge commit
have the same Git tree, so the exact content that passed qualification is the content now
on `main`.

| Workflow | Run | Result |
| --- | ---: | --- |
| Telemetry contract validation | 36271238300 | success |
| Controller shell validation | 36271238299 | success |
| Merge gate | 36271238288 | success |
| LC0 real-inference qualification | 36271238258 | success |
| ONLINE-2 real-network online profile qualification | 36271238269 | success |
| M14-G3 online staged hybrid authority qualification | 36271238298 | success |
| Baseline engine validation | 36271238264 | success |

PR #39 also closed its Codex code-review threads before merge. Review automation is evidence
about the reviewed code, not a substitute for the runtime qualification gates above.

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
    staged VERIFY and an explicit fail-closed outward DecisionAuthorization gate.

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

### 1. LOCAL-1 — full-game lifecycle qualification — **NEXT**

Add a pinned established UCI match runner and exercise complete games. Validate legal move
lifecycle, full history/repetition semantics, castling/en-passant/promotion, low clocks,
long games, worker failure, restart/cleanup, storage/replay pressure and process/resource
leakage.

Engineering acceptance is lifecycle correctness, not Elo.

### 2. ONLINE-3 — reproducible package + pinned Lichess bridge

Package the qualified engine, pin a tested `lichess-bot` revision, add deployment manifests,
operator documentation and bridge smoke tests.

### 3. ONLINE-4 — network/restart/reconciliation/rollback qualification

Exercise disconnects, uncertain move submission, duplicate/out-of-order events, service
restart, stale output, rate limits, storage pressure and rollback.

### 4. Release qualification

Aggregate the required qualification families into one always-present release gate and
freeze exact source/binary/network/profile/model/bridge identities.

### 5. ONLINE-RC

Run the first restricted unrated bot canary only after the operational gates pass.

## Parallel work

**M14-G4 production SKIP calibration** may proceed alongside LOCAL-1/ONLINE-3. It becomes
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
composition**. It is not yet full-game qualified, packaged/deployed as a bot, or supported
by an equal-envelope playing-strength campaign.
