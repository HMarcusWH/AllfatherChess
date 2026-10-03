# AllfatherChess roadmap

**Status date:** 3 October 2026  
**Current main:** `2d7d6f19c18bb5ee101199e4a038b7c420405c50`  
**Latest merged milestone:** PR #61 / M14-J J13B — immutable META-1 execution qualification  
**Current execution milestone:** M14-J J13C — isolated b4 candidate qualification before promotion  
**Current-state authority:** [CURRENT_STATUS.md](CURRENT_STATUS.md)  
**M14-J implementation plan:** [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md)  
**Detailed deployment audit:** [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

This document is the canonical **forward execution roadmap**. Historical build plans remain
useful for architecture and design lineage, but future ordering should be read from this file.
The roadmap deliberately separates operational release gates from later playing-strength claims.

## 1. Where the project is now

PR #39 completed the first production-shaped outward hybrid authority path:

```text
UCI clock
  -> immutable TimePlan / resource envelope
  -> disjoint EXPLORE
  -> common-support VERIFY
  -> route-bound same-process staged VERIFY
  -> frozen proposal + evidence digest
  -> clocked DecisionAuthorization
       -> HYBRID
       -> exact Stockfish fallback
  -> deadline-safe atomic bestmove publication
  -> post-output resource/replay sealing
```

The qualified reference composition uses the real ONLINE-2 CPU bundle with Stockfish,
Reckless and BLAS-LC0. It requires a genuine non-anchor HYBRID result in the predeclared
qualification set and retains deterministic fail-closed fallback.

PR #39 closed the single-move authority question. PR #41 / LOCAL-1 then qualified that
composition across the declared complete-game lifecycle, mandatory failure cases, rule
transitions and replay/resource/process integrity. PR #44 / ENGINE-OPT-V2 subsequently
qualified the selected portable engine/profile composition and froze it as the orchestration
fallback.

PR #52 then completed M14-J J6 on exact head
`6aecd0bae7848ca8a9893377fadffb049d336c3a` and merged as
`fb690e435da39808adaa5c02345c77d407bc8043`. The resource laboratory retained a valid
exact-host artifact with 57 candidate operating points, 1368/1368 Stage-A measurements and
72/72 Stage-B composition batches, with no execution errors. It explicitly grants no profile
selection or deployment authority. PR #53 then repaired the LOCAL-1 prerequisite boundary
without changing the retained J6 evidence. PR #54 then merged J7 as a deterministic,
source-controlled selection/evidence layer while leaving the J3 runtime catalog disabled and
unchanged. PR #55 then merged J8: an additive, clamp-only `MoveResourcePlan` below the
unchanged `clock_envelope_v1` TimePlan. PR #56 merged J9: fixed historical optional searches
cross a typed WorkGrant plus the existing BudgetLedger authority before dispatch. PR #57
then merged J10: only the staged round becomes an adaptive bundle nomination, while STOP
remains unpromoted because the frozen independent calibration seed has 16 groups against a
32-group minimum. Exact-head real-process evidence exercised 9 admitted/settled WorkGrants
with zero open reservations. J11 now hardens that path into independently reconstructible
allocation/resource provenance before J12 is allowed to compose orchestration with HYBRID
move authority. The project is **not** yet deployable as a qualified service and has not
established superiority over a constituent engine.

PR #60 subsequently merged the matched-orchestration `ANCHOR_CONTROL` arm and the frozen
50-opening / 100-game META-1 schedule. That merge did **not** execute the confirmatory
campaign. J13B is the final execution-qualification step before the real campaign: immutable
attempt IDs, exact merged-main/source/build binding, pre/post host-domain binding, destructive
evidence mutations, a pinned-Fastchess mini smoke, and a separate independent verifier job.

## 2. Critical path to the first public canary

```text
DONE
M14-G3 / PR #39
clock-safe staged hybrid authority
        |
        v
DONE
LOCAL-1 / PR #41
full-game lifecycle qualification
        |
        v
DONE
ENGINE-OPT-V2 / PR #44
measured constituent/profile optimization
        |
        v
DONE
M14-J J0-J6
orchestration substrate + resource laboratory
        |
        v
DONE
M14-J J7 / PR #54
evidence-backed isolated profile selection freeze
        |
        v
DONE
M14-J J8 / PR #55
clamp-only adaptive outer resource plan / MoveResourcePlan
        |
        v
DONE
M14-J J9 / PR #56
typed compatibility WorkGrants + single-ledger admission
        |
        v
DONE
M14-J J10 / PR #57
round-2 adaptive bundle nomination; STOP calibration gated
        |
        v
DONE
M14-J J11 / PR #58
independently sealed allocation evidence + authority binding
        |
        v
DONE
M14-J J12 / PR #59
single-allocation orchestrated composition + bounded HYBRID authority
        |
        v
DONE
META-1 / J13A / PR #60
matched-orchestration ANCHOR_CONTROL architecture
        |
        v
DONE
META-1 / J13B / PR #61
immutable execution + independent paired verification
        |
        v
IN PROGRESS
M14-J J13C
b4 candidate qualification; canonical b7/J3/J8-J12 unchanged
        |
        v
PENDING
ENGINE-OPT-V2 canonical promotion / substrate requalification
        |
        v
META-1 / J13
100-game qualified-host campaign
        |
        v
ONLINE-PLAY-1
production-profile 10+5 experiment
        |
        v
ONLINE-3
reproducible package + pinned lichess-bot bridge
        |
        v
ONLINE-4
network / restart / reconciliation / rollback qualification
        |
        v
RELEASE-QUALIFICATION
immutable aggregate release manifest and gate
        |
        v
ONLINE-RC
restricted unrated public bot canary
```

The canary and ONLINE-PLAY-1 are operational experiments, not Elo/equal-resource superiority claims. META-1 isolates the value of hybrid move authority under matched orchestration, while M15-B/C remains the separate path to a formal common-resource strength claim.

## 3. LOCAL-1 — full-game lifecycle qualification — **QUALIFIED**

**PR:** #41  
**Qualified head:** `6fc6522f863e2a15c6d4c230fa558cbb993f867e`  
**Merge commit:** `524ec9b25c7f08d981ba7c88318d106e22586295`  
**Shared tree:** `0a2095ba5dde83292a348cff2495d02bd4e05299`  
**Workflow:** `36344180962` / run #100  
**Required games:** 28/28 validated; zero qualification errors.

### Objective

Qualify that the PR #39 composition remains correct across complete games rather than
isolated `go` requests.

### Qualified repository surface

The canonical qualification is implemented as an isolated toolchain rather than a
second production controller:

- `qualification/fastchess.lock.json` — exact Fastchess commit/tree provenance;
- `qualification/local-full-game.json` — frozen lifecycle, failure-case, baseline and soak policy;
- `qualification/local-game-requirements.txt` — hash-pinned independent chess/PGN dependency;
- `tools/local_game/` — runner, transparent proxy, independent integrity/PGN validation,
  forced rule witnesses, failure injection, process/resource auditing and Fastchess build logic;
- `tests/local_game/` — fail-closed contract and subprocess regressions;
- `tests/fixtures/local_full_game/` — frozen opening/endgame/rule-transition fixtures;
- `Makefile.local-game` — standalone qualification entry point;
- `.github/workflows/full-game-qualification.yml` — required gate plus a sharded manual soak;
- `docs/FULL_GAME_QUALIFICATION.md` — exact evidence and claim contract.

The G3 runtime is derived from `config/allfather.online-hybrid.validation.json` at execution
time; LOCAL-1 does not maintain a second hand-edited copy of the authority policy. The gate
closed on the exact PR #41 head above after the required workflow passed.

### Lifecycle coverage

The campaign must cover:

- ordinary startpos games from both colors;
- full move-history propagation rather than bare-FEN-only reconstruction;
- castling rights;
- en passant;
- promotion;
- repetition-sensitive history;
- mate and stalemate terminal handling;
- long games and repeated process reuse;
- low-clock / stop-grace behavior;
- user/game termination while work is active;
- one or more shadow-worker failures;
- replay/storage delay or failure;
- restart between games;
- cleanup of all engine/search/process state.

### Engineering acceptance gate

A qualifying campaign requires:

- zero illegal outward moves;
- exactly one outward terminal `bestmove` for every completed `go`;
- zero unexplained `bestmove 0000`;
- zero stale-generation outputs;
- zero controller-attributable time forfeits in the engineering campaign;
- no post-game live worker/search from the previous game;
- no cross-ply resource contamination;
- complete game -> ply -> replay-run identity linkage;
- retained evidence for failures instead of silently excluding failed games;
- PGN result/termination consistent with the game/replay manifests;
- no weakening of M14-G3 authority, timing, resource, or evidence gates merely to make
  the campaign pass.

### Strength boundary

LOCAL-1 may record W/D/L and controller behavior for diagnostics, but lifecycle acceptance
is not an Elo test. A small campaign cannot become a playing-strength claim by implication.

The qualified campaign also records all ten color-reversed pairings across Stockfish, Reckless, LC0,
Allfather-Anchor and Allfather-G3. That matrix is **same tournament clock**, not equal total
compute. M15-B/C remains the only path to a comparative equal-resource strength claim.

## 4. ENGINE-OPT-V2 — measured constituent/profile optimization — **QUALIFIED / MERGED**

**PR:** #44  
**Qualified head:** `085420843b95f3f2dd206fc1c66bf642cbd49b6d`  
**Merge commit:** `7248f25fc64be4d04a78ec2b1f0c9de2986a11a5`  
**Shared tree:** `80fae798aed99a5e37fcfb7fce321a7e01a2fc56`  
**Aggregate workflow:** `36458789423` / success

ENGINE-OPT-V2 preserved the qualified n16 EXPLORE / n16 VERIFY / n32 staged-VERIFY intervention while promoting portable PGO Stockfish, portable x86-64 Reckless and the cross-run-stable LC0 `b7-p8-c256k-warm64` profile. The selected LC0 profile repeated the frozen 8/8 move vector three times on exact head with median wall time **329.0025–331.5455 ms**, versus the frozen v1 baseline **1291.427–1316.554 ms**.

Promotion also required a real-process non-anchor G3-v2 authority witness, owner-specific physical-resource bounds, the ordinary v1 LOCAL-1 control and complete LOCAL-1-v2 lifecycle qualification. The final aggregate reported `promotion_ready: true` with no errors. These results qualify a profile/build/resource composition; they do not establish Elo or constituent superiority.

## 5. M14-J — Adaptive Resource Orchestration — **J13 / META-1 NEXT**

M14-J moves resource/profile authority above the constituent engines. The meta-controller will select only prequalified operating points, derive move-level resource envelopes from host/game context, issue typed WorkGrants, progressively buy the most valuable next computation, and preserve exact resource provenance for DecisionAuthorization. ENGINE-OPT-V2 remains the fail-closed fallback.

Implementation order:

1. **J0 — DONE** — synchronize the post-PR-44 baseline and freeze the fallback;
2. **J1 — DONE** — immutable resource/profile/game/work-grant contracts;
3. **J2 — DONE** — effective host capability and pressure detection;
4. **J3 — DONE** — qualified resource-profile catalog representing current v2 exactly;
5. **J4/J5 — DONE** — safe game-boundary profile application and resource enforcement;
6. **J6 — DONE / PR #52** — exact-head resource laboratory and whole-composition interference evidence;
7. **J7 — NEXT** — freeze qualified engine/composition profiles from the retained J6 artifact;
8. **J8/J9** — adaptive MoveResourcePlan and progressive WorkGrant scheduler;
9. **J10/J11** — deterministic allocator plus replay/authority provenance binding;
10. **J12** — complete orchestrated-v1 real-engine/lifecycle qualification;
11. **J13 / META-1** — HYBRID vs ANCHOR_CONTROL with matched orchestration;
12. **J14 / ONLINE-PLAY-1** — production-profile 10+5 comparative experiment.

The full file-level rebuild plan is [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md). M14-J does not authorize arbitrary runtime hyperparameter generation, chess-search retuning or learned outward authority. ResourceAuthorization and DecisionAuthorization remain separate hard gates.

## 6. ONLINE-3 — reproducible service package and bridge

With LOCAL-1 green, package exactly the qualified engine rather than building new chess
logic.

Planned surface:

- `deploy/Dockerfile`;
- one deployment definition such as `deploy/compose.yml` or an equivalent systemd target;
- `deploy/bin/allfather-online`;
- `deploy/lichess/config.example.yml`;
- `scripts/release-manifest.py`;
- `docs/ONLINE_OPERATIONS.md`;
- a pinned, tested `lichess-bot` revision and bridge smoke tests.

Initial policy:

- standard chess only;
- ponder off;
- one concurrent game;
- bots-only allow-list;
- unrated/casual 10+5;
- no bridge-owned opening book, cloud analysis, or tablebase move source;
- no automatic resign/draw logic until score provenance is explicitly compatible;
- credentials injected as secrets, never committed or logged.

## 7. ONLINE-4 — network, restart, reconciliation and rollback

Qualify the service around the engine:

- duplicate/out-of-order game events;
- disconnect while thinking;
- disconnect after move send but before acknowledgement;
- uncertain submission reconciliation against authoritative game state;
- rate limits and HTTP failures;
- invalid credentials;
- game-end-during-search;
- service restart;
- stale output after restart;
- storage pressure;
- stop-new-games control;
- rollback to a prior immutable release/profile.

A restart must rebuild correct repetition/history state. It may not silently reduce a game
to a bare FEN if doing so loses decision-relevant history.

## 8. RELEASE-QUALIFICATION

Add an aggregate always-present release workflow that cannot succeed because a path-scoped
required job was skipped.

Freeze:

- source commit/tree;
- all three engine binaries and build identities;
- LC0 network hash and backend;
- controller configuration;
- route/calibration/model identities;
- match/deployment profile;
- bridge revision;
- runtime package/container identity;
- resource/host policy;
- operational manifests.

The release gate consumes the existing qualification families plus LOCAL-1 and ONLINE-3/4.

## 9. ONLINE-RC — restricted public canary

Cut an explicitly experimental immutable release only after the preceding gates pass.

Proposed first canary:

- one concurrent game;
- standard chess;
- unrated 10+5;
- approved bot opponents;
- 50 completed games as an operational learning batch;
- complete per-game PGN and per-ply replay/resource evidence;
- immediate review of any time forfeit, illegal/stale output, missing evidence, failed
  resource certificate, repeated unexpected fallback, or lifecycle inconsistency.

The canary demonstrates permitted online operation. It does not establish equal-envelope
superiority.

## 10. Parallel research track

These may proceed without blocking the conservative first canary.

### M14-G4 — production SKIP calibration

Required before learned compute suppression is promoted. It must bind train/serve
intervention identity, independent position-group support, held-out qualification data,
and uncertainty-aware shortcut risk. Unsupported/OOD evidence continues to BUY or deny
compute fail-closed.

### M15-B/C — strength campaign

This is the formal path to the original strength objective. Run frozen comparative campaigns
against each pinned constituent under a declared common resource regime, report actual
consumption, preserve all losses/timeouts, predeclare analysis, and report effect sizes and
uncertainty. CPU-reference, GPU-profile and broad "best engine" claims are distinct.

### M15-A — governed policy evolution

Only after stable full-game evidence exists: evolve allocation, nomination, verifier choice,
reserve fractions and source-specific evidence as immutable policy generations.

### M14-H/I — measured transport/native experiments

Profile deployed process/UCI boundaries first. Replace IPC/native boundaries only when
measured benefit justifies the additional integration, provenance and licensing risk.

## 11. Administrative release gates

These remain separate from chess-controller correctness:

- protect `main` with the always-present merge gate before release promotion;
- resolve duplicate governance tracking in issues #26/#27;
- make an explicit top-level licensing/compliance decision for combined distribution;
- select the actual deployment host/resource class;
- create a fresh Lichess BOT account/token for the canary;
- define the first opponent allow-list.

## 12. Claim discipline

The roadmap uses four distinct levels:

1. **implemented** — code exists;
2. **qualified** — the named contract passed its declared evidence gate;
3. **deployed** — the immutable release operated through the permitted bridge;
4. **strength-supported** — a predeclared comparative campaign supports a bounded chess
   performance claim.

Do not promote one level into the next by wording alone.
