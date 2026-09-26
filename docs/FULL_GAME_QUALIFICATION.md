# LOCAL-1: full games and five descriptive constituent baselines

**Implementation:** `tools/local_game/`, `tests/local_game/`, `Makefile.local-game` and
`.github/workflows/full-game-qualification.yml`.  
**State:** implemented candidate; only an exact-source successful qualification run can
close LOCAL-1. The release/status ledger must not be advanced by source changes alone.

## What is being tested

LOCAL-1 has three evidence families under one frozen policy in
`qualification/local-full-game.json`:

1. **Natural complete-game lifecycle:** eight real games in four color-reversed pairs.
   G3 faces a deliberately cheap Stockfish driver. The cases exercise process reuse, full
   PGN history, explicit engine restart and low clocks. This driver never enters the
   descriptive comparison tournament.
2. **Mandatory lifecycle failures:** deterministic controller/subprocess injections cover
   a later shadow-worker crash, a shadow that misses shutdown, replay-storage failure after
   outward publication, stop/game transition while work is active, and long repeated
   generation reuse. These are lifecycle evidence, not chess-strength games.
3. **Descriptive baselines:** a five-arm round robin: direct Stockfish, direct Reckless,
   direct real-network BLAS-LC0, the Allfather native-clock anchor shell, and Allfather G3.
   Ten unordered pairings, two reversed-color games each: 20 baseline games. Every baseline
   player receives the same tournament clock and no baseline player receives the
   one-node lifecycle-driver cap.

The required natural tournament therefore contains **28 games**, plus the mandatory fault
campaign, separate forced chess-rule transition probes, and freshly executed LC0 / ONLINE-2
/ G3 qualification prerequisites.

The extended soak repeats the ten baseline pairings ten times: **200 baseline games**, plus
the lifecycle/fault/prerequisite evidence in each bounded shard. This count is an engineering
reliability workload, not a strength-study sample size.

## Pinned Fastchess provenance

Fastchess is pinned by repository, commit **and Git tree**. It is the C++ tournament runner
from `Disservin/fastchess`, not an unpinned package with a similar name.

Upstream tests and lifecycle execution use separate hosts deliberately:

- the exact pinned source unit tests run on the upstream-supported Ubuntu 22.04 reference
  host and produce an identity-bound source-test attestation;
- the Ubuntu 24.04 lifecycle job downloads that attestation, checks commit/tree/lock
  identity, then builds the exact pinned source into the tournament runner;
- the build manifest records the compiler actually supplied to Make, binary bytes, license,
  source identity and source-test attestation.

A valid-looking manifest cannot substitute for different runner bytes: the verifier hashes
the produced Fastchess binary and license before consuming campaign evidence.

## The anchor control is a real single-engine control

The ONLINE-2 reference is not an anchor-only cost control: it runs observational
specialists. Nor does the existing runtime support ONLINE TimePlan in `mode: anchor`.

The control therefore uses the unmodified schema-v2 anchor shell with **one** Stockfish
instance and native UCI clock handling. It is labeled `allfather-anchor`. It does not run
idle LC0/Reckless processes or pretend to use G3's TimePlan.

Direct Stockfish versus this control measures the native-clock wrapper configuration. G3
versus this control changes time allocation, specialist compute and move authority together;
subtracting score percentages does not isolate those effects.

## Same tournament clock is not equal compute

Native alpha-beta constituent arms use one thread and 16 MiB Hash. LC0 uses the pinned
CPU/BLAS/network settings with its declared one-searcher environment. Standalone roles use
MultiPV=1; G3 specialists retain the already-qualified MultiPV=3 profile.

All actual `setoption` and `go` commands are recorded. Validation requires the first
transmitted `wtime` and `btime` of every game to equal the frozen base control exactly,
requires exact increments, rejects unauthorized work limits, and bounds later clock
progression against that same control.

The baseline is therefore **same recorded tournament clock on one host**, not M15-B/C
equal-resource superiority. Native Stockfish nodes and LC0 visits are never treated as one
common compute currency.

## Repetition is not a FEN

The reversible opening returns to an already-seen board with nonempty history. The verifier
checks the pinned opening prefix and every complete UCI `position ... moves ...` history.
A bare FEN cannot replace that history even when its piece placement matches.

Campaign identity is explicit:

```text
campaign -> job -> process session -> game ordinal -> search ordinal -> replay run
```

Game ordinals must be contiguous and start at one. Replay directories must agree with their
sealed `run_id`, and a run ID or replay-manifest content identity may not be reused anywhere
else in the campaign.

## Rule transitions are explicit witnesses

Castling, en passant, promotion, repetition, checkmate and stalemate are checked with frozen
forced `searchmoves` witnesses. Each witness is reconstructed with the independent
python-chess rules layer and must retain its matching G3 replay evidence.

These probes are deliberately not included in baseline W/D/L. A forced transition is a
protocol/rules witness, not a natural move-choice result.

## Failure semantics are mandatory

The campaign cannot claim complete lifecycle merely because normal games finish. The frozen
failure suite must also pass:

- **shadow-worker-crash:** a later observational process crash is quarantined while anchor
  authority remains alive into the following game;
- **slow-shadow-shutdown:** a worker that ignores stop cannot observe the next game and is
  quarantined;
- **replay-storage-failure:** a persistence failure remains missing/incomplete evidence and
  may never rewrite a move that already crossed stdout;
- **active-game-termination:** stop/new-game during work linearizes to exactly one outward
  terminal line and allows a fresh following game;
- **long-generation-reuse:** repeated games/plies produce unique finalized generations and
  replay bundles without process or authority leakage.

These use the real controller/process lifecycle around deterministic protocol backends so the
failure can be injected reproducibly. They do not claim real-engine chess strength.

## Cap draws and protocol failures are not successful games

No score resign/draw, tablebase adjudication, `-maxmoves`, SPRT, recovery retry or hidden
move source is enabled in the required natural-game gate.

A natural-game pass requires:

- completed PGN result;
- Fastchess `Termination "normal"`;
- an independently reconstructed chess-rules result equal to the PGN result;
- exactly two reversed-color games per frozen job;
- the exact declared engine pair;
- exactly one legal non-null `bestmove` for every searched ply;
- no stale, duplicate, extra or missing search identity.

A timeout, disconnect, adjudication, forfeit or runner failure is retained as evidence and
cannot become a successful rules termination.

## Runtime/profile derivation

There is no second checked-in copy of the G3 configuration. Each isolated G3 session receives
a derived `runtime.json`; the structural check permits **only** an absolute repository root
and a session-local replay directory to differ from
`config/allfather.online-hybrid.validation.json`.

All timing, reservations, node limits, real LC0 network/backend identity, staged VERIFY and
DecisionAuthorization settings therefore remain the qualified G3 policy. The native-clock
anchor control is an explicit, separate derivation.

No production controller/engine source or authority gate is changed by LOCAL-1.

## Transparent UCI observation and cleanup

Fastchess communicates through a transparent Python recorder for every arm. The recorder:

- forwards original UCI commands and terminal lines without rewriting clocks or moves;
- keeps stderr separate from UCI stdout;
- records monotonically ordered transcript sequence plus game/search ordinals;
- records the exact observer specification, environment and child command;
- queues evidence writes and treats overflow/write failure as failure;
- places the child engine/controller in its own process group;
- enables Linux subreaping and records descendant CPU/cleanup.

The outer campaign runner also discovers campaign-owned detached process groups. On timeout
it signals both the Fastchess group and those detached child groups, escalates to SIGKILL if
needed, and records any remaining group. Emergency cleanup can prevent resource leakage, but
it cannot convert the timed-out job into a pass.

Per-search procfs endpoints are fail-closed: a missing/replaced process identity produces no
valid CPU observation. Every searched ply requires a finite non-negative observation.
Session reaped-subtree CPU includes startup/cleanup and descendants; recorder CPU is reported
separately. RSS is endpoint/process-lifetime evidence, not a claimed exact simultaneous peak.

## Independent G3 resource reconstruction

LOCAL-1 does not accept `resource.json.qualified` or
`route.json.envelope_claim.claimed` as self-authenticating booleans.

For every mapped G3 ply it independently reconstructs:

- measured stage and process completeness;
- stage, engine, controller and physical CPU arithmetic;
- CPU/GPU coverage from measurement settings and available providers;
- route→resource SHA/report identity;
- budget committed totals from the four purpose lanes;
- whole-envelope and solver/VERIFY/REFINE/controller partition caps;
- open reservation settlement;
- TimePlan/anchor `movetime` boundedness;
- route/manifest TimePlan identity;
- wall-envelope, physical-CPU and hard-deadline completion.

The producer-written qualification/claim flags must exactly match those derived predicates.
Ordinary cases require a positive reconstructed claim. A declared low-clock denial may be
reported, but it cannot hide missing measurement coverage, crossed generations, or an
illegal/late move.

## Positive G3 authority and natural-game coverage are separate

The existing G3 prerequisite is freshly executed and must emit an actual non-anchor HYBRID
move. Its report is not trusted alone: LOCAL-1 retains the ONLINE-2 and G3 prerequisite
replay roots and independently checks the referenced replay bundles.

Natural-game HYBRID/override counts are recorded, not manufactured by loosening policy or
choosing positions after results. A zero-override tournament is reported as a coverage
limitation; it does not invalidate the separate predeclared positive-G3 proof, nor does it
claim repeated natural non-anchor authority was observed.

## Evidence layout

Each campaign uses a unique directory below
`build/test-results/local-full-game/<campaign-id>/` and never merges a rerun into an old
directory:

```text
manifest.json
report.json
prerequisites/
  lc0.json
  online2.json
  g3.json
  online2-replays/
  g3-replays/
  g3-positive-replay/
rule-probes/
fault-cases/
life-*/
base-*/
  games.pgn
  runner.log
  fastchess.log
  <arm>.json
  sessions/<arm>/<session>/
    session.json
    uci.jsonl
    runtime.json        # controller arms
    replays/            # G3
```

The campaign manifest hash-binds the exact committed source inputs and complete produced
artifact set. Qualification rejects tracked dirt, untracked importable/source files, and
ignored-but-untracked LOCAL-1 fixture files. Fixtures are enumerated from Git rather than a
filesystem glob.

## Required and soak workflows

The required PR/main gate is intentionally bounded. The optional 200-game soak is separate.

Fastchess source tests are executed first on Ubuntu 22.04 and uploaded as an attestation.
The Ubuntu 24.04 required/soak jobs consume only an attestation matching the exact frozen
commit/tree/lock.

The soak no longer attempts roughly 104 serial Fastchess jobs inside one 330-minute workflow
job. A manual soak dispatch uses **10 parallel bounded shards**, each receiving a
deterministic subset of the same frozen schedule. Each shard retains an independent
manifest/report/artifact bundle. The soak remains engineering reliability evidence; it is
not an Elo sample-size calculation.

## Reproduction

Use a clean committed Linux checkout, the ONLINE-2 build prerequisites and the pinned
python-chess dependency:

```bash
python3 -m pip install --no-build-isolation --no-deps --require-hashes \
  -r qualification/local-game-requirements.txt

make -f Makefile.local-game test
make -f Makefile.local-game build-fastchess
make -f Makefile.local-game qualification
```

For a local serial soak:

```bash
make -f Makefile.local-game soak
```

Or reproduce one CI-style shard:

```bash
make -f Makefile.local-game soak-shard SHARD=0 SHARDS=10
```

Use a virtual environment locally. The pinned `chess` package is test tooling; Fastchess's
MIT license is retained with its build. This does not resolve the separate project-wide
distribution licensing decision.

## Claim boundary

A green exact-head LOCAL-1 workflow supports only the named complete-game lifecycle,
failure-handling, identity, clock, replay/resource and same-clock descriptive-baseline
contracts.

It does **not** establish Elo, equal-resource comparability, superiority, production learned
SKIP safety, or deployed-network reconnect/rollback correctness. Those remain M15-B/C,
M14-G4 and ONLINE-3/4 respectively.

LOCAL-1 status advances only after the exact source has earned a successful qualification
result.
