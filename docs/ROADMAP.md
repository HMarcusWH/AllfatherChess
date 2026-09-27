# AllfatherChess roadmap

**Status date:** 27 September 2026  
**Current main:** `524ec9b25c7f08d981ba7c88318d106e22586295`  
**Latest merged milestone:** PR #41 / LOCAL-1 — full-game lifecycle qualification  
**Current execution milestone:** ONLINE-3 — reproducible package + pinned lichess-bot bridge  
**Current-state authority:** [CURRENT_STATUS.md](CURRENT_STATUS.md)  
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
transitions and replay/resource/process integrity. The project is **not** yet deployable as
a qualified service and has not established superiority over a constituent engine.

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
NEXT
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

The first public canary is an operational experiment, not an Elo or superiority claim.

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

## 4. ONLINE-3 — reproducible service package and bridge — **NEXT**

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

## 5. ONLINE-4 — network, restart, reconciliation and rollback

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

## 6. RELEASE-QUALIFICATION

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

## 7. ONLINE-RC — restricted public canary

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

## 8. Parallel research track

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

## 9. Administrative release gates

These remain separate from chess-controller correctness:

- protect `main` with the always-present merge gate before release promotion;
- resolve duplicate governance tracking in issues #26/#27;
- make an explicit top-level licensing/compliance decision for combined distribution;
- select the actual deployment host/resource class;
- create a fresh Lichess BOT account/token for the canary;
- define the first opponent allow-list.

## 10. Claim discipline

The roadmap uses four distinct levels:

1. **implemented** — code exists;
2. **qualified** — the named contract passed its declared evidence gate;
3. **deployed** — the immutable release operated through the permitted bridge;
4. **strength-supported** — a predeclared comparative campaign supports a bounded chess
   performance claim.

Do not promote one level into the next by wording alone.
