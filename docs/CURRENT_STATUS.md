# AllfatherChess current build and release status

**Status date:** 26 September 2026  
**Authoritative source commit:** `64aa8fd13c390b9b37b8825d8f39e73d9bdbdbf8`  
**Merged milestone:** PR #36 / ONLINE-1 — clock-derived per-move envelopes and deadline-safe UCI execution  
**Canonical deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
roadmaps remain useful as lineage, but when a status sentence in an older section conflicts
with this file, the current code/configuration and this record take precedence.

## Current CI evidence on main

All five main-branch workflow families completed successfully on the authoritative commit:

| Workflow | Run | Result |
| --- | --- | --- |
| Merge gate | 36193538618 | success |
| Controller shell validation | 36193538737 | success |
| Telemetry contract validation | 36193538611 | success |
| LC0 real-inference qualification | 36193538614 | success |
| Baseline engine validation | 36193538665 | success |

The baseline job built all three real engine trees and passed the retained golden,
restricted-root, ShardLedger, recursive REFINE, specialist-budget, measured-resource,
typed cross-feed, regime, counterfactual, value-of-compute, staged VERIFY, unified
routing, telemetry, ONLINE-1 clock, UCI-shell, M14-C authority, shadow, VERIFY,
COMPARE/RELOCK, and active-routing contracts.

Important observed current-main outcomes are claim-bounded:

- ONLINE-1 real-engine clock contract: **3 legal anchor results with full measured
  envelopes**, plus the zero-clock rejection control.
- M14-G2 real-engine contract: **`BUY_STAGED_VERIFY`** with outward anchor move;
  the shipped validation profile has no production staged/regime models and therefore
  fails closed rather than demonstrating a learned SKIP.
- M14-C real-engine authority contract: the current run completed as
  **`ANCHOR_FALLBACK`**. The mechanism is qualified, but a production-facing
  real-backend positive HYBRID override is still a release gate.
- Active routing contract: **0 authorized stops** in the current real-engine validation
  run. Thresholds must not be weakened merely to manufacture activity.
- LC0 real-inference qualification: pinned network + BLAS backend passed on the
  recorded Ubuntu 24.04 / x86-64 CPU reference environment. That reference remains
  explicitly not a final equal-resource strength platform.

These are integration/qualification facts, not an Elo or superiority claim.

## What is implemented

The repository currently contains, in code and contracts:

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
11. counterfactual decisions and bounded M14-C `movetime_v0` hybrid authority;
12. real-network LC0 BLAS qualification;
13. M14-F structural regime classification/support calibration;
14. M14-G1 same-process staged VERIFY;
15. M14-G2 unified BUY/SKIP value-of-compute routing;
16. ONLINE-1 clock-derived `TimePlan`, deadline fencing, bounded stop/kill, stale-generation
    protection, and replay-bound timing evidence.

## Current profile compatibility matrix

The important release constraint is that these mechanisms are **not yet one qualified
production profile**.

| Profile / capability | Real LC0 inference | Clock TimePlan | Staged VERIFY / G2 | Hybrid outward authority | Current role |
| --- | --- | --- | --- | --- | --- |
| `allfather.online-clock.validation.json` | no — random LC0 | yes | no production composition | no | ONLINE-1 timing/lifecycle qualification |
| `allfather.strength.validation.json` | yes — pinned BLAS/network | no online clock profile | no | no | real-inference reference |
| `allfather.hybrid.validation.json` | no — random LC0 | no | staged extension forbidden with authority | yes, `movetime_v0` only | M14-C authority mechanism |
| `allfather.unified-value.validation.json` | no — random LC0 | no | yes | no | G2 routing mechanism |

Current runtime firewalls are intentional:

- ONLINE-1 cannot grant `hybrid_authority`;
- M14-C authority supports only `request_class = movetime_v0`;
- staged VERIFY cannot coexist with M14-C authority;
- ONLINE-1 is CPU-only and rejects recursive REFINE;
- the shipped G2 profile has null production calibration paths and therefore fails
  closed toward buying more compute.

Do **not** remove these guards to create a release. The remaining work is to qualify a
new composition with explicit evidence semantics.

## Critical path to the first public canary

### 1. ONLINE-2 — real-network hardware-bound online profile

Create and qualify one deployment-facing CPU profile that combines actual inference,
the exact pinned network/backend/options, ONLINE-1 timing, aggregate thread/memory
limits, host identity, and measured process/controller resource evidence.

Planned additions:

- `config/allfather.online.cpu-reference.json`
- `qualification/online-cpu-reference.json`
- `scripts/qualify-online-profile.py`

A passing ONLINE-2 profile proves reproducible operational inference, not strength.

### 2. M14-G3 — compose staged VERIFY with clock-aware hybrid authority

Add a new authorization/evidence version rather than deleting the current firewalls.
The selected terminal source, base/extension stage identity, candidate order, process
generation, TimePlan, route recommendation, resource result, and PRE_ANCHOR freeze
boundary must all be bound.

Required real-backend release tests include both:

- a genuine authorized `HYBRID` output path; and
- deterministic exact anchor fallback for budget denial, partial staged work, timeout,
  stale generation, evidence loss, nonunanimity, illegal proposal, or late publication.

No mixture of partial extension and base terminals may silently authorize a move.

### 3. LOCAL-1 / M15-B — full-game lifecycle qualification

Add a pinned established UCI match runner (Fastchess is the planned reference) and
exercise complete games, not isolated `go` calls. Cover history-sensitive repetition,
castling, promotion, en passant, mate/stalemate, long games, low clocks, restarts,
worker failure, storage pressure, and process/resource leakage.

The initial engineering gate remains a reproducible full-game campaign with no illegal
outputs, duplicate outward moves, leaked workers, or controller-attributable time losses.
Strength statistics are a separate, predeclared campaign.

### 4. ONLINE-3 — package the service and pin the Lichess bridge

Still absent from the repository:

- `deploy/Dockerfile`
- `deploy/compose.yml` or one equivalent deployment target
- `deploy/lichess/config.example.yml`
- `deploy/bin/allfather-online`
- `scripts/release-manifest.py`
- `docs/ONLINE_OPERATIONS.md`

Pin a tested `lichess-bot` revision rather than writing a new Bot API client unless a
specific missing requirement is demonstrated. The first profile stays standard-chess,
ponder-off, one concurrent game, bots-only allow-list, unrated 10+5, and no bridge-owned
book/cloud/tablebase move source.

### 5. ONLINE-4 — network/restart/rollback qualification

Qualify disconnects, duplicate/out-of-order observations, uncertain move submission,
rate limiting, game-end-during-search, service restart, stale output, storage pressure,
stop-new-games control, and rollback to a previous immutable profile.

### 6. Release qualification and ONLINE-RC

Add an aggregate `.github/workflows/release-qualification.yml` that cannot pass because
a required path-scoped job was skipped. Freeze exact source, binaries, engine networks,
controller profile, policy/model identities, bridge revision and operational manifests.

Then cut the first explicitly experimental release and run the restricted canary. The
planned initial batch is 50 completed unrated bot games after the local/full-lifecycle
gates pass. This is operational evidence, not an Elo claim.

## Work that may proceed in parallel

**M14-G4 production SKIP calibration** is required before promoting learned compute
suppression as a production optimization, but it need not block a conservative first
canary that always BUYs when evidence is unsupported and otherwise falls back safely.
G4 must bind train/serve identity, intervention identity, independent position-group
support, untouched qualification data, and an uncertainty-aware shortcut-risk criterion.

M14-H/I native transport experiments and M15-A governed policy evolution remain later
optimization tracks, not first-canary blockers.

## Administrative release gates

These are current repository facts, not code TODOs:

- `main` is currently **unprotected** and the repository has **no ruleset**;
  issues #26 and #27 track the duplicate governance finding. Require the always-present
  `Merge gate / validate` before release work is promoted.
- The Allfather-specific controller code still has **no explicit top-level licensing
  decision**. Upstream Stockfish/LC0/Reckless notices are preserved, but combined
  distribution requires that decision and a deliberate compliance review.
- Operator inputs are still required for the actual deployment host/resource class,
  a fresh Lichess BOT account/token, and the initial opponent allow-list.

## Claim boundary

The repository is now a substantial, test-gated hybrid controller research system and
ONLINE-1 is merged. It is **not yet a deployed bot**, does not yet contain one qualified
real-network + clock + staged-hybrid production profile, and has not established
equal-envelope playing-strength superiority.
