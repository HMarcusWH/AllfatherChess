# Recursive PrefixShardLedger v2

## Status

PR #16 adds a recursive ownership substrate and descendant UCI-dispatch
qualification. It deliberately does **not** migrate the live shadow controller
away from `RootShardLedger` v1.

Current split:

```text
RootShardLedger v1
    live EXPLORE consumer
    root-only
    unchanged

PrefixShardLedger v2
    recursive prefix ownership
    split / transfer / seal substrate
    qualified offline + real-engine contract
    one-shell REFINE consumer in PR #17
    bounded multi-level live REFINE consumer in M14-D / PR #30
```

This isolates recursive geometry from the already-hardened four-process
controller lifecycle.

## Prefix semantics

A prefix shard

```text
(m1, m2, ..., mn)
```

denotes all continuations whose move sequence from the current external
position begins with that prefix.

Examples:

```text
(e2e4)
(e2e4, e7e5)
(e2e4, e7e5, g1f3)
```

A root-v1 shard is therefore the depth-1 special case.

## State machine

v2 uses:

```text
UNASSIGNED -> LEASED -> ACTIVE -> SEALED -> RETIRED
```

`RETIRED` is structural: a sealed parent becomes retired only when it is
replaced by an exact child frontier. Retired parents remain historical evidence
and are never dispatchable.

PR #16 does not add arbitrary retirement of unexplored regions.

## Exact root partition

The immutable legal-root universe is assigned once, atomically, with the same
hard requirements as RootShardLedger v1:

- authorized owner keys exact;
- canonical lowercase UCI moves;
- no per-owner duplicates;
- no cross-owner overlap;
- no unknown roots;
- union exactly equals the candidate-root universe.

Failure leaves the snapshot and revision unchanged.

## Prefix-free frontier invariant

Recursive ownership must not recreate overlap through ancestry.

For any two distinct frontier shards `a` and `b`:

```text
a is not a strict prefix of b
b is not a strict prefix of a
```

Thus the following cannot both be frontier regions:

```text
e2e4
e2e4 e7e5
```

Once `e2e4` is split, the parent is retired before its children become the
frontier.

This guarantees assigned-prefix non-overlap. It does **not** guarantee that
different prefixes cannot later transpose to the same board state.

## Per-shard activation and sealing

Recursive prefixes cannot in general be batched into one root-level
`searchmoves` command, so v2 removes the owner-atomic assumption from the new
ledger.

```text
activate_shard(shard_id, owner)
seal_shard(shard_id, owner)
```

Both require exact ownership and exact state.

## Atomic split

A split is legal only for a sealed frontier leaf owned by the caller.

```text
SEALED parent
    |
    | exact externally-certified child set
    v
RETIRED parent
    +-- LEASED child 0
    +-- LEASED child 1
    '-- ...
```

Children:

- extend the parent by exactly one canonical move;
- inherit the parent owner;
- preserve the supplied oracle order;
- receive deterministic IDs from generation + full prefix.

The ledger does not generate chess moves. The caller supplies the exact legal
children from an external oracle.

Any malformed child, duplicate, existing-prefix collision, state error, or
ownership error rejects the entire split before mutation.

## Atomic transfer

```text
transfer_shards(ids, from_owner=A, to_owner=B)
```

is valid only for leased frontier leaves. Active, sealed, retired, duplicate,
unknown, or wrongly-owned inputs reject the entire batch.

Transfer changes ownership only. It does not activate work and carries no
routing meaning.

## Deterministic identity

Shard IDs are derived from generation and complete prefix:

```text
g000007:d001:e2e4
g000007:d002:e2e4.e7e5
g000007:d003:e2e4.e7e5.g1f3
```

Identity therefore does not depend on the order in which unrelated branches
were split.

Snapshot ordering is deterministic DFS:

```text
root input order
-> child oracle order
-> recursive child order
```

## Descendant UCI compilation

A recursive prefix cannot be enforced from the original position by passing
only its last move as `searchmoves`.

For prefix:

```text
e2e4 e7e5 g1f3
```

`common/prefix_dispatch.py` compiles:

```text
position startpos moves e2e4 e7e5
go nodes N searchmoves g1f3
```

More generally:

```text
position moves = external_position.moves + prefix[:-1]
searchmoves    = prefix[-1]
```

Existing external game history, base FEN, and variant are preserved.

The compiler is syntactic only. Legal-prefix certification remains the oracle's
responsibility.

## Real-engine qualification

`scripts/prefix-shard-contract.py` proves the substrate against live engines:

1. obtain the start-position legal-root universe from Stockfish `go perft 1`;
2. build PrefixShardLedger v2 and apply a deterministic test partition;
3. seal `e2e4`;
4. move the oracle to `position startpos moves e2e4`;
5. obtain the exact reply universe and split `e2e4`;
6. transfer `e2e4 e7e5` atomically to a different owner;
7. activate/seal it;
8. obtain exact third-ply children after `e2e4 e7e5`;
9. split again;
10. compile `e2e4 e7e5 g1f3` into descendant position + one restricted root;
11. run Stockfish, Reckless, and LC0 sequentially against that same descendant
    request;
12. require bestmove and every reported PV head to stay on `g1f3`.

This is capability qualification only. It is not concurrent exploration,
adaptive refinement, replay-v1 integration, or a strength experiment.

## Snapshot schema v2

The snapshot contains only structural state:

- schema version;
- generation and revision;
- authorized owners;
- immutable candidate-root order;
- frontier IDs;
- all shards in deterministic DFS order;
- full prefixes;
- parent/child links;
- root/sibling ordinals;
- owner and state.

It contains no residuals, scores, rankings, budgets, route values, VERIFY
results, or RELOCK conclusions.

## Non-goals of PR #16

PR #16 does not:

- change `controller/shards.py`;
- replace RootShardLedger v1 for initial EXPLORE;
- add a learned or score-driven split policy;
- choose which recursive leaf deserves another expansion;
- mutate Replay v1;
- add active VERIFY;
- add a global transposition/position-ownership table;
- change outward bestmove authority;
- make an Elo or equal-resource strength claim.

PR #17 supplied the first one-shell integration. PR #18 added active resource
authorization. M14-D / PR #30 now reuses the same ledger for bounded repeated
live splits. The ledger itself still carries no routing meaning: recursive
nomination lives in `controller/refinement_policy.py`, resource authority lives
in the router/budget layer, and outward decision authority remains separate.
See `docs/REFINEMENT.md`.
