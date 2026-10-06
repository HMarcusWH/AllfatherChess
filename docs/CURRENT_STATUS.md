# AllfatherChess current build and release status

**Status date:** 4 October 2026  
**Authoritative main commit:** `ff3d8adc166eb7ef2a9ad57da9d20e1b212df17d`  
**Latest merged milestone:** PR #62 / M14-J J13C — isolated b4 candidate qualification  
**Qualified J6 PR head:** `6aecd0bae7848ca8a9893377fadffb049d336c3a`  
**Frozen fallback:** canonical ENGINE-OPT-V2/J3/J8-J12 remain on b7 pending explicit promotion  
**Current execution milestone:** PR #63 — b4 resource-substrate requalification, evidence only  
**Next milestone:** ENGINE-OPT-V2 b4 canonical promotion + J3→J12 substrate requalification  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)  
**M14-J rebuild plan:** [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md)  
**Detailed deployment plan:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the short-form synchronization point for the live repository. Historical
plans/specifications retain design lineage, but this file and `ROADMAP.md` take precedence
for current status and forward ordering.

## PR #62 b4 candidate qualification — MERGED / PROMOTION-READY

PR #62 merged as `ff3d8adc166eb7ef2a9ad57da9d20e1b212df17d`. Its exact-head
ENGINE-OPT-V2 workflow `37158505596` qualified the isolated
`b4-p0-c256k-cold` LC0 candidate without changing the canonical b7 selection,
J3 resource catalog, or J8-J12 runtimes. The final aggregate reported valid evidence,
candidate qualification, `promotion_ready: true`, no qualification failures and
`QUALIFIED_EXACT_HOST_ONLY`. The same head also closed canonical and candidate matrix
qualification, fresh G3/LOCAL-1 evidence, constituent A/B, J8-J12 regressions and the
standalone LOCAL-1 workflow.

PR #62 deliberately did **not** relabel the historical J6/J7 resource evidence as b4
evidence. That separation is the reason PR #63 exists.

## PR #63 b4 resource-substrate requalification — IN PROGRESS / EVIDENCE FROZEN

PR #63 adds a separate `resource-lab-v2` measurement contract whose LC0 reference is
the already-qualified b4 candidate: `NNCacheSize=262144`, `MinibatchSize=4`,
`MaxPrefetch=0`, no startup warmup. Historical `resource-lab-v1`,
`resource-profile-evidence-v1`, `resource-profile-selection-v1` and the canonical
runtime catalog remain unchanged.

The retained v2 measurement was produced from exact source
`85efb74cf3d9cc039e8a205a63709b22b3328d3b` by workflow `37204469303`.
Artifact `11304204017` has SHA-256
`5198f0cfc05948eb0d8c314fb4bd76d1f2f0b60a41b251773c38adab16f3e834`.
Its independent report retained 57 candidates, **1368/1368** Stage-A measurements and
**72/72** Stage-B batches with zero execution errors; all nine family/work-budget groups
were promotion-eligible and there were no reference native-work blockers.

The frozen deterministic isolated selections are: LC0 n16 → `cache0`, LC0 n32 →
`cache0`, LC0 n64 → `cache2m`; Reckless n16/n64/n256 → `v2-current`; Stockfish
n16/n256 → `v2-current`, Stockfish n64 → `t1-h64`. These are measurement/selection
evidence only: they grant no runtime profile selection, composition qualification, resource
authorization, outward move authority, deployment or strength claim.

### PR #63 lifecycle accounting repair — IMPLEMENTED / REQUALIFYING

The first retained PR #63 LOCAL-1 campaigns exposed two independent stale assumptions in the legacy G3 resource model. Per-move envelope derivation was proportionally shrinking the declared 250 ms controller partition even though legal-root qualification and controller finalization are fixed work; retained controller-purpose maxima were 165.460 ms and 217.362 ms. The repair preserves the 250 ms controller reserve whenever the shrunken CPU envelope can contain it, while low-clock envelopes still clamp fail-closed by reducing solver capacity first.

The same evidence showed that the legacy scalar 500/750 ms specialist estimates materially under-described real BLAS-LC0. Two retained LOCAL-1 artifacts now back a source-controlled calibration: LC0 EXPLORE maxima were 1760/1750 ms, base VERIFY maxima 740/750 ms, and staged VERIFY-extension maxima 1290/1140 ms. The legacy v1 runtime therefore uses explicit owner estimates of Stockfish/Reckless/LC0 = 100/100/1800 ms for EXPLORE and 100/100/1400 ms for VERIFY, while preserving 500/750 ms only as unknown-owner fallbacks. The outer 4000 ms wall / 12000 ms CPU envelope is unchanged. This repair grants no b4 canonical promotion, no new move/resource authority and no strength/Elo/deployment claim.

PR #63 also closes three evidence-quality gaps exposed during requalification. First, J8 now
uses the same fixed-controller clamp semantics as the baseline TimePlan instead of
proportionally shrinking controller work a second time on the adaptive-capacity path. Second,
new TimePlans carry the explicit partition-policy marker `absolute-controller-v2`; pre-marker
replays can use legacy proportional reconstruction only inside a source-controlled,
SHA-authenticated historical archive scope. An absent marker by itself never selects legacy
semantics.

Third, the two retained failed LOCAL-1 campaigns are reduced into independently bound
calibration observations. The first campaign contains **689** completed LC0 EXPLORE CPU
observations, not the previously transcribed 692; one missing `resource.json` is retained as
an exclusion and no measurement is imputed. The maxima remain unchanged, so the selected
1800/1400 ms LC0 reservations do not change. These observations are calibration evidence from
failed campaigns, not upgraded lifecycle/authority qualification.

### PR #63 constituent benchmark repair — IMPLEMENTED / REQUALIFYING

The old constituent gate compared one blocked pass of five Hash settings and rejected the
selected Hash=16 when a single run placed it more than 3% behind that run's fastest setting.
PR #63 now freezes `constituent-hash-v2`: ten complete repeats of the eight-position,
five-hash matrix with position-blocked cyclic ordering plus reversed ordering in the second
half. The **3% efficiency band is unchanged**. Qualification reconstructs the raw rows and
returns `QUALIFIED`, `NOT_QUALIFIED`, or `INCONCLUSIVE`; an interval crossing the band
does not pass. Constituent binary A/B measurements are paired position-by-position and
alternate which side executes first. Producer-written summaries are not authoritative.

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

## M14-J J9 WorkGrant compatibility scheduler — MERGED / PR #56

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

## M14-J J10 adaptive allocation substrate — MERGED / PR #57

J10 leaves J9 rounds 0/1 unchanged and makes only round 2 claim-bearing: after a clean
three-engine n16 VERIFY round, an immutable `AllocationDecision` nominates either the frozen
three-engine n32 staged-VERIFY bundle, `STOP_BUYING`, or `FALLBACK`. The decision carries
neither resource nor outward-move authority; every BUY still requires the exact J9 WorkGrant,
router validation and single BudgetLedger reservation chain.

The retained PR #56 profile-domain artifact contains 796 completed staged interventions, but
most are successive plies from nine long sessions. PR #57 therefore records those rows as
descriptive/mechanism evidence only rather than treating 737 position IDs as independent
calibration groups. A pre-outcome seed corpus freezes 16 independent source groups against a
32-group STOP-promotion minimum. Consequently the source-controlled J10 runtime is intentionally
**BUY-only fail-closed**: the allocator mechanism is exercised while calibrated
`STOP_BUYING` remains unpromoted until leakage-free independent staged/regime models exist.

A J10 BUY is prepared transactionally: all three staged WorkGrants must be proposed and
reserved before any staged search write, and all three effective STAGED_VERIFY option digests
must match before dispatch begins. J7 profile selections, hybrid DecisionAuthorization,
REFINE/crossfeed/counterfactual, host portability, strength/Elo and deployment remain outside
this milestone.

Exact-head PR #57 evidence exercised the real ENGINE-OPT-V2 processes with one J10
`BUY_BUNDLE` decision, **9/9 WorkGrants authorized, 9/9 settled, and zero open
reservations**. The post-merge `main` merge gate, controller shell, ONLINE-2, baseline,
M14-G3 and LC0 real-inference workflows also passed. STOP remains intentionally unpromoted:
16 frozen independent seed groups are still below the source-controlled 32-group minimum.

## M14-J J11 allocation evidence hardening — MERGED / PR #58

J11 does not change chess search or enable HYBRID authority. It adds an append-only
BudgetLedger admission/settlement journal, sealed `resource-plan.json`,
`resource/allocation.jsonl`, `engine-bundle.json` and `orchestration.json`, and an independent
`validate-resource-orchestration.py` verifier. The verifier reconstructs the
MoveResourcePlan, J10 AllocationDecision, J9 WorkGrants, effective-option/native-limit
bindings, admission-time budget affordability, settlement arithmetic, host/composition
capacity and physical-resource evidence without trusting producer-written
`qualified: true`.

`orchestration.json` is the terminal J11 evidence root: it hash-binds the finalized parent
replay, VERIFY/staged-VERIFY manifests, route/resource reports, resource plan, allocation
trace and the exact-head ENGINE-OPT-V2 build manifest. The qualifier independently binds
that engine bundle back to the current source commit and source-controlled build contracts.
This ordering avoids a circular dependency while ensuring the exact stage manifests
used for WorkGrant reconstruction cannot drift. ENGINE-OPT-V2 copies the complete validated
J11 replay into the retained profile-domain artifact and validates that copied bundle in place,
so the qualification report is accompanied by the evidence it attests. DecisionAuthorization gains an optional
orchestration provenance field that is absent from
all historical G3 snapshots, preserving their canonical identity; when populated by the
future J12 composition it requires complete WorkGrant settlement and zero open WorkGrant
reservations. J11 itself keeps the existing runtime prohibition on orchestration plus
HYBRID authority.

## M14-J J12 full orchestrated composition — MERGED / PR #59

J12 is the first milestone allowed to compose the adaptive resource plane with live move
authority. It does **not** run G2 `unified_value_v1` beside J10 as a second allocator.
Instead, the single J10 `AllocationDecision` is projected deterministically into the
existing G3 staged-route vocabulary:

```text
BUY_BUNDLE   -> BUY_STAGED_VERIFY
STOP_BUYING  -> SKIP_STAGED_VERIFY
FALLBACK     -> FALLBACK_ANCHOR
```

The new `orchestrated_clocked_staged_preanchor_v1` policy leaves historical
`clocked_staged_preanchor_v1` untouched. HYBRID authority requires the typed J11
orchestration provenance, complete WorkGrant settlement, zero open WorkGrant reservations,
and a real non-synthetic adaptive host-capacity observation. Hosted-runner synthetic capacity
may exercise the mechanism but is deliberately forbidden from promoting outward HYBRID
authority.

The initial J12 profile preserves the frozen n16 EXPLORE / n16 VERIFY / n32 staged VERIFY
grid, `conservative_v1`, the existing ENGINE-OPT-V2 fallback, and unpromoted
`STOP_BUYING`. J7 Hash32/MaxPrefetch0 selections remain outside the runtime.

The exact PR #59 head passed merge gate, baseline validation, LOCAL-1, ONLINE-2,
M14-G3, LC0 real-inference, ENGINE-OPT-V2 and the resource laboratory. On the
GitHub-hosted worker, J12 correctly reported `NOT_QUALIFIED_HOST_CAPACITY`: synthetic
capacity facts exercised the mechanism but did not promote HYBRID authority.

## M14-J J13 / META-1 — EXECUTION QUALIFICATION MERGED / SUBSTRATE REQUALIFICATION IN PROGRESS

PR #60 merged the test-only `ANCHOR_CONTROL` outward disposition after the unchanged
`DecisionAuthorization` boundary. The control runs the same J12 orchestration,
proposal and authorization machinery but always emits the Stockfish anchor. A granted
control authorization remains granted in evidence; it is not relabelled as fallback.

PR #61 then merged immutable campaign attempts, producer-workspace relocation, pre/post
host-domain binding, independent requalification, paired-opening reporting and artifact
transport/retention tests. The confirmatory 100-game campaign is still blocked until the
selected ENGINE-OPT-V2 substrate itself requalifies on exact head.

The retained PR #61 aggregate produced valid negative evidence: the then-selected
`b7-p8-c256k-warm64` LC0 profile was repeatable but changed one frozen bestmove, and the
unchanged seven-case G3-v2 corpus produced no non-anchor HYBRID witness. J13C therefore qualifies `b4-p0-c256k-cold` only as an isolated candidate overlay from that retained discovery matrix. The canonical b7/p8/warm64 selection, J3 catalog and J8-J12 runtimes remain unchanged in this PR. The candidate keeps the 600/800 ms LC0 reservations and original G3 witness corpus unchanged and must earn a fresh exact-head promotion-ready aggregate before a later explicit canonical migration.

The actual confirmatory 100-game META-1 campaign has **not yet run**. J13B hardens that
execution before any result is collected: every attempt is immutable and bound to merged
`main`, the exact ENGINE-OPT-V2/Fastchess build, the J12 report and one pre/post host
qualification domain. A fresh job independently reconstructs raw campaign evidence rather
than trusting producer-written disposition/result fields.

The frozen experimental design remains unchanged: 50 committed openings, colors reversed,
100 games total, serial execution and no result/Elo/SPRT merge gate. Reporting is by the
50 paired opening blocks plus intervention/suppression and realized-resource telemetry.
GitHub-hosted `NOT_QUALIFIED_HOST_CAPACITY` remains a valid zero-game disposition rather
than being converted into fallback-vs-control data.

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
