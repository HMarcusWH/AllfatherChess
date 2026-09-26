# LOCAL-1 — complete-game lifecycle qualification

**Status:** implementation/qualification candidate  
**Predecessor:** PR #39 / M14-G3 clock-aware staged hybrid authority  
**Current repository gate:** LOCAL-1 remains OPEN until the dedicated workflow passes on the merged tree.

LOCAL-1 asks a different question from M15-B/C:

> Can the already-qualified G3 composition play complete games repeatedly without corrupting
> UCI lifecycle, chess history, clock authority, replay identity, resources, or process state?

It does **not** use a small local campaign to claim Elo or equal-resource superiority.

## Frozen tournament runner

The campaign pins Fastchess through `qualification/fastchess.lock.json`.

The lock records upstream repository, tag, exact commit and exact Git tree. The build script
checks out that commit, verifies the tree, builds Fastchess, runs Fastchess's own tests, and
records the resulting binary hash/toolchain in:

```text
build/tools/fastchess/build-manifest.json
```

The runner binary is therefore measured output, not assumed byte-reproducible across arbitrary
compilers.

## Runtime profiles

Two Allfather profiles participate:

- `allfather.local-game.validation.json` — exact G3 composition except for a dedicated replay root;
- `allfather.local-control.validation.json` — the same observation/routing/staged stack with
  outward `hybrid_authority` removed.

The control is an **authorization ablation**, not a pure controller-overhead measurement.
It still pays for Allfather's observation/routing machinery.

The direct constituent arms use the exact ONLINE-2 Stockfish, Reckless and real-BLAS LC0
bundle.

## Five-arm descriptive baseline

The frozen baseline contains:

1. Stockfish
2. Reckless
3. LC0
4. Allfather-Control
5. Allfather-Hybrid

Every unordered pair is played once as a two-game color-reversed Fastchess mini-match:
10 pairings / 20 games total.

All arms receive the same Fastchess chess clock and runner policy. This is deliberately
reported as a **same-clock descriptive baseline**. It is not an equal-resource comparison:
Allfather may consume several backend processes plus controller CPU while a constituent arm
uses one engine process.

The report records W/D/L, pairwise results and termination reasons. Allfather physical CPU
from its existing `resource.json` evidence is also aggregated, but there is no fabricated
direct-engine CPU number to make the table look symmetric.

M15-B/C remains the later equal-resource strength campaign.

## Exact UCI transcript boundary

Fastchess engines are launched through `scripts/uci-transcript-proxy.py`.

The proxy is transparent to chess. It records:

```text
runner -> engine: uci / isready / ucinewgame / position / go / stop / quit
engine -> runner: id / option / info / bestmove / readyok
```

Each line is tagged with a proxy instance, game ordinal, request ordinal and current position.
The child engine still writes to a POSIX pipe, preserving the ONLINE deadline-safe stdout
contract.

This makes duplicate terminal output, missing `bestmove`, stale position history and replay
association directly testable rather than inferred from a final PGN.

## Game → ply → replay identity

For each Fastchess invocation the campaign snapshots the relevant replay root before launch.
Only newly created replay directories belong to that case.

Within one persistent Allfather process, transcript request order and replay generation are
both monotonic. The qualifier pairs them in order and then independently requires exact:

- `position` command equality;
- external `go` command equality;
- PGN history equality;
- emitted-move equality;
- TimePlan deadline success;
- route/resource qualification;
- replay integrity;
- final-decision integrity for the hybrid arm.

This is stronger than matching on FEN alone. Repetition can revisit the same board while
history remains decision-relevant.

## Required lifecycle fixtures

The required campaign covers:

- persistent startpos games;
- a history that already contains castling (`e1g1`);
- a history that already contains en passant (`e5d6`);
- a repetition-sensitive reversible history;
- a FEN-based history that already contains promotion (`a7a8q`);
- a checkmate start state;
- a stalemate start state.

The transition moves are frozen explicitly in `qualification/local-full-game.json`; the
qualifier requires those prefixes to survive into Allfather's actual `position` requests.

The terminal checkmate/stalemate fixtures must finish without sending an engine search.

Cross-game fake-backend tests additionally cover repeated generations, `ucinewgame`, and a
later shadow-worker exit degrading evidence without poisoning anchor authority.

## Required acceptance

The lifecycle gate requires:

```text
0 illegal moves
0 duplicate bestmoves
0 null bestmoves on active searches
0 controller time forfeits
0 missing/reused replay mappings
0 replay-integrity errors
0 unqualified required resource claims
0 process leaks
```

Every required Allfather request must have one replay. Every replay must correspond to one
request. Required measured intervals must remain qualified.

The G3 positive-authority qualifier is rerun before LOCAL-1, so lifecycle qualification does
not become a flaky requirement that a small set of natural games happens to contain a
non-anchor HYBRID move.

## Process cleanup

Before each Fastchess case the harness snapshots relevant processes. After the runner exits,
it waits a bounded cleanup interval and rejects surviving new controller/proxy/ONLINE-2
engine processes.

This catches failures that single-`go` tests cannot see, such as an old generation surviving
into the next game.

## Commands

After installing the same Linux build dependencies used by CI:

```bash
make local-full-game-tests
make build-fastchess
make build-online-cpu-reference
make lc0-strength-contract
make online-hybrid-contract

make local-full-game-run
make local-full-game-contract

make local-full-game-baseline
```

The composite command is:

```bash
make local-full-game-qualification
```

An extended engineering soak is manual:

```bash
make local-full-game-soak GAMES=200
```

The soak game count is a reliability workload, not a strength sample-size calculation.

## Evidence layout

```text
build/test-results/local-full-game/
  required/
    campaign-manifest.json
    report.json
    <case>/
      case.json
      games.pgn
      fastchess.log
      driver.txt
      transcript-*.jsonl
  baseline/
    campaign-manifest.json
    report.json
    baseline-comparison.json
    baseline-*/
      ...
```

Replay bundles remain under the two dedicated replay roots and are uploaded with the
workflow evidence.

## Claim boundary

A green LOCAL-1 result supports:

> The declared G3 engine composition and its authorization-disabled control completed the
> frozen complete-game lifecycle campaign under the pinned runner with the required UCI,
> history, deadline, replay, resource and cleanup invariants.

It does **not** establish:

- Elo;
- superiority over Stockfish, Reckless or LC0;
- equal-resource comparability;
- production learned-SKIP safety;
- online deployment/reconnect correctness.

Those remain M15-B/C, M14-G4 and ONLINE-3/4 concerns respectively.
