# Root ShardLedger v1

## Scope

PR #10 introduces the first controller-owned chess search-space object.

The implementation is deliberately root-only:

```text
current synchronized position
          |
          v
Stockfish go perft 1
          |
          v
canonical legal root universe
          |
          v
    RootShardLedger
     /      |      \
    /       |       \
 Stockfish Reckless  LC0
   roots     roots   roots
```

The live external UCI path remains unchanged from PR #9:

```text
GUI -> AllfatherChess -> Stockfish anchor -> bestmove
```

The ledger therefore qualifies ownership semantics without yet dispatching
three simultaneous searches.

## Legal-root oracle

The managed runtime obtains the active legal root universe from Stockfish using:

```text
go perft 1
```

Stockfish emits one `<move>: 1` line for each legal root followed by
`Nodes searched: N`.

The controller accepts the result only if:

- every root is canonical lowercase UCI syntax;
- every depth-1 root count equals one;
- no root is duplicated;
- the final node total equals the parsed root count.

A terminal position with `Nodes searched: 0` produces the empty candidate
universe.

The oracle follows the synchronized `UCI_Chess960` setting. The real-engine
qualification explicitly checks castling encoding under both standard and
Chess960 modes.

## Root shard model

One legal root move is one shard.

Each root-v1 shard contains only:

```text
id
generation
ordinal
prefix = [root_move]
owner
state
```

The root-v1 state machine is:

```text
UNASSIGNED -> LEASED -> ACTIVE -> SEALED
```

Recursive split, transfer, budgets, evidence payloads, VERIFY overlap and
cross-feed are intentionally not part of this schema.

Shard IDs are deterministic within a generation:

```text
g000001:r007:e2e4
```

Candidate-root input order is preserved. The controller does not sort roots or
create an implicit move-ordering policy.

## Ownership invariant

The ledger is created with an explicit authorized owner set. In the current
three-backend runtime:

```text
stockfish
reckless
lc0
```

Partition assignment is atomic and one-shot for a generation.

Before any shard changes state, the ledger validates:

- partition owner keys exactly equal the authorized owner set;
- every move is canonical;
- every move belongs to the immutable candidate universe;
- no owner contains duplicate roots;
- no root appears under more than one owner;
- the union of all owner roots exactly equals the candidate universe.

Only after the full partition validates are roots moved from `UNASSIGNED` to
`LEASED`.

Thus the root exploration invariant is structural:

```text
owner_count(root_shard) <= 1
```

A failed mutation leaves revision and snapshot unchanged.

## Owner-atomic activation

Activation and sealing operate on an owner's complete region.

```text
activate_owner(owner)
    all owner's LEASED roots -> ACTIVE

active_roots(owner)
    returns non-empty fully ACTIVE roots only

seal_owner(owner)
    all owner's ACTIVE roots -> SEALED
```

An owner may legitimately receive zero roots when there are fewer candidates
than available engines, but an empty owner region cannot produce a dispatch.

A completely empty terminal ledger is valid and can record an exact empty
partition, but it cannot produce any exploration dispatch.

## Revision and snapshot

The ledger maintains a monotonic revision counter. Revision increments only
after a successful mutation.

`snapshot()` returns a stable JSON-serializable copy containing:

- schema version;
- generation;
- revision;
- whether the generation partition has been assigned;
- authorized owners;
- immutable candidate-root order;
- shard IDs, ordinals, prefixes, owners, and states.

Snapshots are intended to become replay inputs in later milestones. PR #10 does
not yet build the replay system.

## Thread safety

All ledger mutations and snapshots are protected by an internal reentrant lock.

Concurrent conflicting partition attempts cannot produce partial or overlapping
ownership: exactly one complete partition may commit for a generation.

## Qualification

The real-engine PR #10 contract:

1. launches the managed three-engine runtime;
2. obtains Stockfish perft-1 roots for representative frozen positions;
3. cross-checks those root sets against the committed legal-move golden oracle;
4. qualifies Chess960 castling encoding;
5. builds a root ledger for startpos;
6. creates a deterministic **test-only** three-way partition;
7. proves exact pairwise-disjoint coverage;
8. activates each owner region;
9. runs Stockfish, Reckless, and LC0 **sequentially** with their owned roots via
   the already-qualified `searchmoves` primitive;
10. requires bestmove and reported PV root heads to remain inside each owned
    region;
11. seals all shards and records the final snapshot.

The round-robin partition used by that contract is test code only. It is not a
production routing policy.

## PR #11 consumer contract

PR #11 consumes the frozen root-v1 ledger without changing its schema or state machine.

For each non-empty owner region:

```text
assign_partition(...)
activate_owner(owner)
roots = active_roots(owner)
dispatch shadow search with exactly roots
record telemetry / completion disposition
seal_owner(owner)
```

The ledger owners remain the solver families:

```text
stockfish
reckless
lc0
```

The runtime process identities are more specific:

```text
stockfish-anchor   unrestricted, no ledger ownership
stockfish-shadow   owner = stockfish
reckless-shadow    owner = reckless
lc0-shadow         owner = lc0
```

The unrestricted anchor therefore does not appear in the ledger and cannot mutate shard state. Pairwise-disjoint ownership applies to the three shadow exploration workers only.

PR #11 may bind ledger snapshots into a separate replay manifest, but it must not add residuals, rankings, routing scores, budgets, recursive children, transfer state, or verification overlap to `RootShardLedger` v1.

## Transpositions

Root-prefix disjointness does not imply global board-state disjointness.
Different owned prefixes may transpose later.

PR #10 guarantees **assigned-prefix non-overlap**, not global position
ownership. Canonical-position overlap measurement and any future
position-ownership table remain later work.

## Explicit non-goals

PR #10 does not implement:

- concurrent three-engine shadow search;
- changed external move selection;
- residual/disagreement calculations;
- candidate voting or ranking;
- adaptive routing;
- shard transfer;
- recursive shard splitting;
- VERIFY / RELOCK overlap;
- automatic fallback;
- resource scheduling.


## Live consumers

`RootShardLedger` v1 is **unchanged** by the immediate controller stack. It
gained no residuals, budgets, rankings, children, recursive split, verification
overlap, or score calibration.

Its live consumer is `controller/shadow.py`, which per external search:

```text
roots   = runtime.legal_root_moves()          # from stockfish-shadow, not the anchor
ledger  = RootShardLedger(roots, owners=<healthy shadow families>, generation=<search generation>)
ledger.assign_partition(root_index % owner_count)
for each non-empty owner:
    ledger.activate_owner(owner)
    dispatch exactly ledger.active_roots(owner) via searchmoves
    ...
    ledger.seal_owner(owner)
```

Notes on live use:

- the ledger's `owners` are restricted to shadow instances that are healthy at
  qualification time; an excluded owner is recorded in the manifest's `notes`,
  so coverage is always exact over the owners the run actually authorized;
- `generation` is the external search generation, so a ledger belongs to exactly
  one synchronized position;
- an empty owner region is never activated and never dispatched;
- a terminal position yields an empty universe and no ledger dispatch at all;
- pre-dispatch and post-run snapshots are both written into the replay manifest,
  so the partition is reconstructable without live controller state.

Active-mode routing changes only *how much compute* an owner receives. It never
mutates ownership.
