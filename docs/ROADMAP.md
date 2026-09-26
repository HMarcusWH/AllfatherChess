# AllfatherChess roadmap

**Status date:** 26 September 2026  
**Current main:** `c301e9986566febfbb7978d55c5a3d3429423cff`  
**Latest merged milestone:** PR #39 / M14-G3 — clock-aware staged hybrid authority  
**Current execution milestone:** LOCAL-1 — full-game lifecycle qualification  
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

That closes the single-move authority question. It does **not** yet establish that the
controller survives complete games, is deployable as a service, or is stronger than a
constituent engine.

## 2. Critical path to the first public canary

```text
DONE
M14-G3 / PR #39
clock-safe staged hybrid authority
        |
        v
NEXT
LOCAL-1
full-game lifecycle qualification
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

The first public canary is an operational experiment, not an Elo or superiority claim.

## 3. LOCAL-1 — full-game lifecycle qualification

### Objective

Prove that the qualified PR #39 composition remains correct across complete games rather
than isolated `go` requests.

### Planned repository surface

The LOCAL-1 candidate now defines this repository surface:

- `qualification/fastchess.lock.json` — exact Fastchess commit/tree provenance;
- `scripts/test-fastchess-source.sh` — exact upstream unit-test contract on its supported Ubuntu 22.04 host;
- `scripts/build-fastchess.sh` — portable lifecycle-runner build on the Ubuntu 24.04 reference host;
- `config/allfather.local-game.validation.json` — exact G3 composition with a dedicated replay root;
- `config/allfather.local-control.validation.json` — same stack with outward hybrid authorization removed;
- `qualification/local-full-game.json` — frozen lifecycle, baseline and acceptance policy;
- `scripts/uci-transcript-proxy.py` — exact external UCI session/request transcript boundary;
- `scripts/run-local-game-campaign.py` — pinned Fastchess campaign driver;
- `scripts/qualify-full-game-lifecycle.py` — game→ply→request→replay/resource/final-decision validator;
- `.github/workflows/full-game-qualification.yml` — dedicated lifecycle/baseline gate;
- `docs/FULL_GAME_QUALIFICATION.md` — semantics, evidence and claim boundary.

The gate remains OPEN until the dedicated workflow passes on the merged tree.

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

LOCAL-1 also freezes a five-arm descriptive same-clock matrix: direct Stockfish, Reckless,
real-BLAS LC0, Allfather-Control (authorization ablation), and Allfather-Hybrid. Every unordered
pair receives a color-reversed two-game mini-match. W/D/L and terminations are retained as a
baseline, but unequal process/resource usage means these results are **not** the M15-B/C
equal-resource strength campaign.

## 4. ONLINE-3 — reproducible service package and bridge

After LOCAL-1 is green, package exactly the qualified engine rather than building new chess
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
