# AllfatherChess UCI Shell v1

## Scope

PR #9 establishes the first external AllfatherChess engine process.

The shell is intentionally a transparent anchor controller rather than a hybrid
search policy:

```text
GUI / tournament
      |
      v
AllfatherChess
      |
      +--> Stockfish  [ACTIVE SEARCH ANCHOR]
      +--> Reckless   [READY / SYNCHRONIZED]
      '--> LC0        [READY / SYNCHRONIZED]
```

All three constituent engines are long-lived managed subprocesses. Only
Stockfish receives search commands in this milestone.

## External UCI surface

The v1 shell supports:

```text
uci
isready
setoption name UCI_Chess960 value <true|false>
ucinewgame
position startpos [moves ...]
position fen <fen> [moves ...]
go ...
stop
ponderhit
quit
```

The external handshake exposes:

```text
id name AllfatherChess
id author AllfatherChess Project
option name UCI_Chess960 type check default false
```

Constituent engine `id` and `option` handshakes are internal and must not
leak to the GUI.

Resource options such as `Threads`, `Hash`, GPU selection, and controller
budgeting are intentionally not exposed yet. Those values become
controller-wide scheduling semantics in a later milestone and must not be
silently multiplied across all three backends.

## Process ownership

Each backend is represented by one production `UciProcess`.

A backend has exactly **one stdout reader**. That reader demultiplexes:

- startup `uciok` waiters;
- `isready` / `readyok` barriers;
- search `info` callbacks;
- search `bestmove` completion.

This is a hard concurrency rule. Independent consumers may not race to dequeue
the same backend stdout stream.

Backend stderr is isolated from the external UCI stream and retained only as a
bounded diagnostic tail.

## Runtime lifecycle

Startup is transactional:

```text
launch Stockfish -> handshake/configure
launch Reckless  -> handshake/configure
launch LC0       -> handshake/configure
ready all
        |
        +-- success -> external shell is READY
        '-- failure -> close every started child and fail startup
```

`ucinewgame`, `position`, and `UCI_Chess960` are synchronized across all
three backends while the shell is READY.

During an active search, state-changing commands and a second `go` are
rejected so the managed backends cannot diverge.

`isready` remains legal during an active search. Its readiness waiter observes
the same stdout stream as the search subscriber, allowing:

```text
go infinite
isready -> readyok
stop    -> bestmove
```

without terminating or stealing output from the active search.

## Anchor search

PR #9 forwards `go` substantially unchanged to Stockfish, including standard
clock/depth/node/movetime/ponder/infinite/searchmoves vocabulary.

Stockfish search `info` and `bestmove` are forwarded outward. Reckless and
LC0 do not receive `go`.

The validation contract requires a deterministic node-limited search through
AllfatherChess to return the same best move as direct Stockfish under the same
validation configuration.

## Failure semantics

There is no routing or fallback policy in PR #9.

If startup or readiness fails, the shell fails closed.

If a managed backend exits unexpectedly, the runtime becomes unhealthy. If a
search is active, the GUI is unblocked with:

```text
info string Allfather runtime failure: ...
bestmove 0000
```

The controller does not silently switch to Reckless or LC0.

## Configuration

`config/allfather.validation.json` is a deterministic regression configuration.
It mirrors the established constituent validation settings and deliberately uses
LC0's backend-light/random validation mode.

It is **not strength-qualified** and must not be used as evidence for hybrid
playing strength or equal-compute claims.

## Explicit non-goals

PR #9 does not implement:

- ShardLedger ownership;
- pairwise-disjoint exploration;
- Reckless/LC0 shadow search;
- residual or disagreement calculation;
- candidate voting;
- automatic backend fallback;
- controller resource allocation;
- VERIFY / RELOCK;
- cross-feed;
- strength optimization.

Those remain later milestones.
