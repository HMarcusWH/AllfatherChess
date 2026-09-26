# LOCAL-1: full games and five descriptive constituent baselines

**Implementation:** `tools/local_game/`, `tests/local_game/`, `Makefile.local-game` and
`.github/workflows/full-game-qualification.yml`.
**State:** implemented candidate; only an exact-source successful qualification run
can close LOCAL-1. The release/status ledger must not be advanced by source changes alone.

## What is being tested

There are two separate outputs, with one frozen policy in
`qualification/local-full-game.json`:

1. **Lifecycle:** eight real games in four color-reversed pairs. G3 faces a one-node
   Stockfish driver. Cases exercise process reuse, PGN history, explicit engine restart,
   and low clocks. This driver is not used in the comparison tournament.
2. **Descriptive baselines:** a five-arm double round robin: direct Stockfish, direct
   Reckless, direct real-network BLAS-LC0, the Allfather native-clock anchor shell, and
   Allfather G3. Ten pairings, two colors each: 20 baseline games. All baseline players
   receive the same tournament time control. No baseline arm is node-limited.

The required campaign is therefore **28 games**, plus separate forced rule-transition
probes and the existing LC0 / ONLINE-2 / G3 qualification prerequisites.
The extended soak repeats the baseline round robin ten times: **200 baseline games**,
plus the same eight lifecycle games. Neither count is a strength-study sample size.

Fastchess is pinned by commit **and tree**, built separately under `build/tools`, and
its own upstream tests run before use. This is the C++ tournament runner from
`Disservin/fastchess`, not the unrelated PyPI package with a similar name.

## Corrections made while checking the implementation plan

### The anchor control is a real single-engine control

The ONLINE-2 reference is not an anchor-only cost control: it runs observational
specialists. Nor does the existing runtime support ONLINE TimePlan in `mode: anchor`.
The new control therefore uses the unmodified schema-v2 anchor shell with **one**
Stockfish instance and native UCI clock handling. It is labeled `allfather-anchor`.
It does not run idle LC0/Reckless processes or pretend to use G3's TimePlan.

Direct Stockfish versus this control measures the behavior of the native-clock wrapper
configuration. G3 versus this control changes time allocation AND specialist work AND
move authority; subtracting score percentages does not isolate those causal effects.

### Identical clocks are not identical compute

Native alpha-beta engines use one thread and 16 MiB Hash in this reference. LC0 uses
the pinned CPU/BLAS/network settings with one search thread. Native standalone roles
use MultiPV=1; G3 specialists retain their qualified MultiPV=3 settings. G3 uses four
managed engine processes, its own CPU envelope and its unchanged time allocator.

All options, binary/network identities and declared BLAS environment are recorded.
The baseline is explicitly **same tournament clock on the recorded host**, not
M15-B/C equal-compute superiority. It does not label native nodes as a common currency.

### Repetition is not a FEN

The four-ply reversible PGN opening returns to the initial board with nonempty history.
The verifier checks the pinned opening prefix and every complete UCI position history.
A bare FEN cannot substitute for the recorded prefix, even if its board placement matches.
Replay joins include campaign/job/session/game/search identity, not just board position.

### Cap draws and protocol failures are not successful games

No score resign/draw, tablebase adjudication, `-maxmoves`, SPRT, recovery retry, or external
move source is enabled. Games must reach a rule-supported result. A job timeout is a
failure, not a synthetic draw. Nonzero Fastchess exit, illegal/null/duplicate/stale output,
missing replays, lost history, incomplete process evidence and leaked descendants fail.

A declared low-clock lifecycle case can report a denied envelope, but cannot excuse
missing measurement coverage, a crossed generation, or an illegal/late move. Rule probes
are explicitly forced `searchmoves` witnesses, never added to baseline W/D/L.

### Positive G3 authority and natural-game coverage are different

The existing G3 prerequisite must execute an actual non-anchor HYBRID move. Natural-game
HYBRID/override counts are recorded, not manufactured by loosening policy or choosing
positions after results. A zero-override tournament is reported as a coverage limitation,
not proof that repeated non-anchor overrides were exercised in natural games.

## Runtime/profile derivation

There is no second checked-in copy of the 169-line G3 configuration. Each isolated session
gets a derived `runtime.json`; a structural check permits **only** an absolute repository
root and session-local replay directory to differ from the source G3 config. All timing,
reservations, nodes, network/backend identities, staged evidence and authorization policy
remain unchanged. The native-clock anchor control has an explicit separate derivation.

No production controller/engine source or authority gate is changed by this harness.

## Observing without granting authority

Fastchess communicates through a transparent Python recorder for each arm. The recorder:

- forwards original UCI commands and terminal lines, without rewriting clocks or moves;
- keeps stderr separate from UCI stdout;
- records a monotonically ordered transcript and game/search ordinals;
- queues evidence writes, reports overflow/write failure instead of dropping it silently;
- places the child engine/controller in its own process group;
- enables Linux subreaping, waits for normal shutdown, records survivors before emergency
  cleanup, and cannot convert a killed leak into a successful cleanup audit.

The recorder adds measurable harness overhead. Per-search procfs endpoints are marked
missing when a process disappears or changes identity. Session reaped-subtree CPU includes
startup, normal cleanup and descendants; the recorder's own CPU is reported separately.
Session CPU is **not falsely divided into per-game CPU** when a process spans two games.
RSS values are endpoint observations, not a claimed exact simultaneous peak.
G3's own `resource.json` remains a separate, replay-checked per-move measurement.

## Evidence and validation

A unique, non-reused campaign directory is created under
`build/test-results/local-full-game/<id>/`:

```text
manifest.json                 planned AND executed jobs; source/inputs/artifact identities
report.json                   pass/fail, errors, games, plies, baseline W/D/L and costs
prerequisites/                freshly executed LC0, ONLINE-2 and G3 reports/logs
rule-probes/                  forced transition witnesses + their G3 replays
life-*/ and base-*/
  games.pgn
  runner.log
  fastchess.log
  <arm>.json                  declared observer specification
  sessions/<arm>/<session>/
    session.json              terminal process/measurement/cleanup record
    uci.jsonl                 full ordered UCI transcript
    runtime.json              for controller arms
    replays/                  for G3, one run per search generation
```

The validator independently reads PGN with pinned `chess==1.11.2`, checks legality and
rule-supported termination, and proves a bijection between searched PGN plies and observed
UCI `go`/`bestmove` pairs. For G3 it also requires a unique replay for every generation and
runs the existing bundle/final-decision/counterfactual integrity verifiers. Extra,
unfinalized, missing, reused or mutated artifacts cannot count as complete evidence.

`resource.json.qualified` is a coverage flag, not by itself an envelope pass. Ordinary
cases also require `route.json.envelope_claim.claimed`; low-clock denials remain explicit.
Incomplete campaigns retain their planned-but-unexecuted jobs, logs and observed results.
No subsequent clean retry overwrites those directories.

The baselines use SAN PGN for independent parsing. Full UCI move strings remain in the
transcripts/replays. Anchor evaluation info is not relabeled as a G3 move evaluation.
Cross-game move agreement is not computed between different positions.

## Reproduction

Use a clean committed Linux checkout, the same build prerequisites as ONLINE-2 (C++ and
Rust toolchains, Meson/Ninja, protobuf, zlib and OpenBLAS), and the pinned test dependency:

```bash
python3 -m pip install --no-build-isolation --no-deps --require-hashes \
  -r qualification/local-game-requirements.txt
make -f Makefile.local-game qualification
```

Use a virtual environment locally; the reference CI explicitly permits installation on its
ephemeral Ubuntu runner. The `chess` dependency is GPL-3.0-or-later test tooling; Fastchess's
MIT license is retained with its build. This does not resolve the separate project-wide
distribution licensing decision.

The composite command runs tests, builds/tests pinned Fastchess, builds ONLINE-2, freshly
runs LC0/ONLINE-2/G3 prerequisites, runs probes and games, then validates evidence. Its
reference-mode ONLINE-2 prerequisite deliberately checks the GitHub-hosted reference host;
a different deployment host requires its own declared qualification, not a forged runner
label.

```bash
make -f Makefile.local-game test
make -f Makefile.local-game soak
make -f Makefile.local-game verify CAMPAIGN=build/test-results/local-full-game/<id>
```

The verifier expects the campaign's committed source checkout and retained artifacts.
The dedicated workflow exposes `required` and `soak` modes, uses read-only repository
permissions, checks exact PR-head identity, and uploads evidence even after failure.
Path-scoped qualification is not yet an aggregate release gate; ONLINE release aggregation
and branch protection remain their separate roadmap tasks.

## Claim boundary and next step

A pass demonstrates the named lifecycle/observation contracts and supplies descriptive
baseline data. It proves neither stronger moves nor Elo superiority. M15-B/C remains
separate; packaging and the pinned Lichess bridge remain ONLINE-3. LOCAL-1 status advances
only after the exact source has earned a successful qualification result.
