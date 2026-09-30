# LOCAL-1: full games and five descriptive constituent baselines

**Implementation:** `tools/local_game/`, `tests/local_game/`, `Makefile.local-game` and
`.github/workflows/full-game-qualification.yml`.  
**State:** **QUALIFIED** on PR #41 exact head
`6fc6522f863e2a15c6d4c230fa558cbb993f867e`; merged as
`524ec9b25c7f08d981ba7c88318d106e22586295` with identical tree
`0a2095ba5dde83292a348cff2495d02bd4e05299`.

## Qualification result

- Workflow: `36344180962` / LOCAL-1 run #100.
- Regression suite: **44 passed**.
- Required natural tournament: **28/28 games independently validated**.
- Mandatory failure-injection campaign: passed.
- Forced rule-transition probes: passed.
- Qualification errors: **0**.
- Retained evidence artifact: `10941548187`.
- Artifact SHA-256: `23d2b1f8baa2b535e92e523c3a3dbd79219213127015a708daa50d49c3395d21`.

The manual 200-game soak remains additional engineering-reliability evidence; it is not an
Elo sample and is not required to describe the required LOCAL-1 gate as qualified.

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
campaign, separate forced chess-rule transition probes, freshly executed LC0 / ONLINE-2
prerequisites, and a freshly executed G3 **mechanism** prerequisite. The dedicated M14-G3
workflow remains the sole positive-witness gate that requires an actual non-anchor HYBRID
emission.

The extended soak repeats the ten baseline pairings ten times: **200 baseline games**, plus
the lifecycle/fault/prerequisite evidence in each bounded shard. This count is an engineering
reliability workload, not a strength-study sample size.

## Pinned Fastchess provenance

Fastchess is pinned by repository, commit **and Git tree**. LOCAL-1 currently pins upstream
`ccb85325b1db322658687b8be8cfe9f54c495840` / tree
`fd43662006af6419242d4f9093344df3520c0660`, the upstream crash-safe snapshot that
ignores SIGPIPE from a crashed child so normal EPIPE/error handling can record the failure.
It is the C++ tournament runner from `Disservin/fastchess`, not an unpinned package with a similar name.

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

All actual `setoption` and `go` commands are recorded. The validator mirrors the pinned
Fastchess time-control state machine rather than assuming UCI starts at the nominal base:
Fastchess initializes each side to **base + one increment**. Its PGN always retains the
search elapsed time in exact milliseconds and, normally, `timeleft=true` also retains the
post-move clock as `tl=<seconds>s`. Pinned Fastchess has one source-level exception: its
scoreless-engine early return leaves `MoveData.timeleft` at the default zero, so the two
Allfather wrappers serialize `/0 <elapsed>s, tl=0.000s` even though the internal tournament
clock is nonzero. LOCAL-1 therefore reconstructs the mover clock exactly as
`max(0, transmitted_clock - elapsed) + increment`; a populated `tl=` must equal that result,
while the zero scoreless-wrapper sentinel is accepted only for the two known wrappers and the
matching `/0` comment shape. Opening-book plies leave state untouched, and every following
UCI `go` must equal the reconstructed two-sided state exactly.

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

Fastchess itself is run without `-strict` because Allfather intentionally does not promise
score-bearing `info` lines; upstream Fastchess turns that benign warning into a fatal exit
under `-strict`. LOCAL-1 instead parses `runner.log` and permits only the exact known
"no info score" warning for the two Allfather wrapper arms; every other warning/fatal/error
is qualification-fatal. Empty-valued LC0 options are left at their frozen engine defaults
rather than serialized into Fastchess's key=value CLI, which rejects empty values.

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

The controller keeps engine ownership and replay ownership separate. Once an ONLINE
generation has frozen all engine/process/resource endpoints, its generation-scoped replay
state moves into a finalization registry and the next search may reuse the engines immediately.
Late deferred telemetry remains routed by generation ID to the old bundle; controller shutdown
waits for all pending finalizers (or explicitly records deferred-observation loss) before
detaching callbacks.

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

The outer campaign runner assigns each bounded subprocess tree a unique inherited ownership
token. On **every** runner exit, not just timeout, it scans Linux procfs for live non-zombie
processes carrying that token and binds them by PID + start ticks + PGID. Surviving work is
recorded before emergency cleanup and permanently fails the job; exact token-owned process
identities are then TERM/KILL-cleaned before any later pairing is admitted. Numeric PID/PGID
reuse and zombies therefore cannot be mistaken for owned live work, and cleanup cannot
launder a lifecycle leak into success.

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

## Evidence retention is non-following and report-directed

Prerequisite replay roots are snapshotted before the fresh ONLINE-2/G3 qualification runs.
Only run IDs explicitly named by the new reports and absent from the pre-run snapshot are
retained. Evidence-tree copying walks with non-following stat semantics and accepts only
ordinary directories/regular files; symlinks, FIFOs, devices, sockets and path escapes are
qualification failures. Copied file bytes are rehashed and a sorted whole-tree digest must
remain identical before and after retention.

Fastchess build output is similarly recreated from a clean generated directory. Its
source-test attestation is mandatory during independent validation and is rechecked for
repository, commit, tree, lock hash, Ubuntu 22.04 host identity, compiler record and exact
upstream test contract.

## Positive G3 authority and natural-game coverage are separate

The dedicated **M14-G3 qualification workflow** owns the positive-witness claim: at least one
predeclared real-backend case must complete staged verification, pass DecisionAuthorization,
and actually emit a non-anchor HYBRID move. That requirement is intentionally not
rediscovered inside LOCAL-1.

LOCAL-1 instead freshly executes the exact same G3 profile in local1-prerequisite mode across
the full frozen G3 case set. That prerequisite requires:

- exact source, G3 policy, G3 runtime and ONLINE-2 profile identity;
- valid replay and final-decision evidence for every executed case;
- qualified resource evidence and an outward move inside the hard deadline for every case;
- only supported outward authority states (HYBRID or deterministic ANCHOR_FALLBACK).

A non-anchor HYBRID witness may appear during this prerequisite and is retained if it does,
but its absence is **not** a LOCAL-1 failure. This avoids making lifecycle qualification
depend on rediscovering a host/load-sensitive authority outcome that the dedicated G3 gate
already owns.

Reports are still not trusted alone: LOCAL-1 snapshots the pre-existing replay roots,
requires every report-referenced run ID to have been freshly created by the prerequisite,
retains only those referenced ONLINE-2/G3 bundles, and independently re-verifies replay and
final-decision integrity. The subsequent full-game campaign then validates every played G3
ply against its own replay/resource/decision evidence.

Natural-game HYBRID/override counts are recorded, not manufactured by loosening policy or
choosing positions after results. A zero-override tournament is reported as a coverage
limitation; it neither invalidates the separate dedicated positive-G3 proof nor becomes a
claim that repeated natural non-anchor authority was observed.

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
A separate Ubuntu 24.04 build job then consumes that mandatory host/source-bound attestation,
builds one exact Fastchess + ONLINE-2 qualification bundle, and uploads it once. Because the
build job and game job can land on different GitHub-hosted CPU models, the LC0 BLAS binary in
that bundle is explicitly portable x86_64: `native_arch`, ISPC native-only code, POPCNT, F16C
and PEXT-only assumptions are disabled in the frozen profile. Required games, every soak shard,
and the soak aggregator consume that **same binary bundle** rather than rebuilding independently.

The soak no longer attempts roughly 104 serial Fastchess jobs inside one 330-minute workflow
job. A manual soak dispatch uses **10 parallel bounded shards**, each receiving a
deterministic subset of the same frozen schedule. Each shard is explicitly labeled
`partial_soak_shard` and may report only shard execution success; it must keep
`baseline_is_complete=false` and `full_game_lifecycle=false`.

A separate aggregate job downloads all ten shard artifacts and requires exact source
identity, shard indices 0..9, unique/non-overlapping job IDs, union equality with the frozen
104-job soak schedule, and 208 validated games before emitting the only
`aggregate_soak_complete=true` report. The soak remains engineering reliability evidence;
it is not an Elo sample-size calculation.

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

LOCAL-1 advanced on 27 September 2026 after the exact PR #41 source earned a successful
qualification result. The next release milestone is ONLINE-3 packaging/pinned bridge
qualification; deployed-network recovery and playing-strength claims remain separate gates.
