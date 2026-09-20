# Search-space ownership

## Purpose

The controller must prevent three strong solvers from wasting exploration compute on the same assigned problem.

"Zero overlap" therefore has a precise operational meaning: **no two engines may simultaneously own the same exploration shard**.

## Shard model

A shard is a controller-defined chess search region identified initially by a root move and later by a move prefix.

PR #10 implements the root-only v1 shape:

```text
RootShard {
  id
  prefix = [root_move]
  owner
  generation
  ordinal
  state
}
```

Root-v1 states are deliberately limited to:

```text
UNASSIGNED -> LEASED -> ACTIVE -> SEALED
```

Recursive parents/children, budgets, evidence, transfer, split, and VERIFY overlap remain later extensions. See `docs/SHARD_LEDGER.md`.

## Hard exploration invariant

For every active shard `s`:

```text
owner_count(s) <= 1
```

Sibling assignments are pairwise disjoint. At the root, the union of assigned shards must equal the controller's active legal candidate set.

An ownership invariant failure is not a warning. Routing must stop and fall back to a safe declared mode.

### Anchor exception is not an ownership exception

PR #11 keeps an unrestricted `stockfish-anchor` running as the sole outward decision authority while `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow` consume the ledger partition. The anchor is **not an exploration owner** and holds no ledger shard. Therefore its independent unrestricted search may traverse roots also searched by shadow workers without violating the ownership invariant: the invariant forbids duplicate ownership among the three shadow exploration workers, not independent reference/authority work outside the ledger.

That anchor/shadow duplication is deliberate research overhead and must not be misreported as efficient equal-budget play. The eventual active controller must remove or account for such duplicated compute inside its global budget.

## Verification exception

Independent overlap is valuable for checking a candidate discovered by another solver. It is therefore isolated into an explicit phase:

```text
EXPLORE   accidental overlap forbidden
COMPARE   aggregate evidence
REFINE    split/transfer unresolved shards
VERIFY    explicit cross-engine duplicate work allowed
RELOCK    reconcile independent evidence
STOP
```

All verification work is separately accounted.

## Transpositions

Disjoint prefixes do not imply disjoint board-state expansion because distinct move orders can transpose.

PR #10 therefore guarantees **region-level / assigned-prefix** non-overlap only. It does not yet instrument canonical-position fingerprints or claim disjoint internal board-state expansion.

Canonical-position overlap measurement and any global position-ownership table are later optimizations. They will only be introduced if measurements show that transposition duplication is materially expensive. Such a table must encode search-relevant state, including draw-sensitive context, rather than piece placement alone.

## Backend restricted-root capability v1

Before the controller can allocate disjoint root shards, every constituent backend must accept a valid non-empty legal root set and keep its root search inside that set.

For an invocation with authorized root set `S`:

```text
backend root candidate set ⊆ S
bestmove ∈ S
every reported root PV head ∈ S
```

PR #5 adds this primitive to Reckless and verifies the same valid-assignment command vocabulary against Stockfish and LC0.

PR #10 adds the first actual controller ownership layer. The runtime obtains the legal root universe from Stockfish `go perft 1`, then `RootShardLedger` atomically assigns every root to exactly one authorized exploration owner for the generation.

The implemented root-v1 guarantees are:

```text
candidate universe immutable
partition owner keys exact
partition coverage exact
cross-owner root overlap forbidden
failed partition leaves ledger unchanged
activation/sealing owner-atomic
empty owner regions never dispatch
```

PR #10 does **not** dispatch three live searches during gameplay. Its real-engine qualification uses already-qualified `searchmoves` sequentially only to prove that an active ledger region can be enforced by every backend.

PR #11 is the first consumer of `active_roots(owner)` in concurrent shadow execution. It activates only non-empty owner regions, dispatches the exact owned root sets to the three shadow workers, and records the result. It does not change the partition based on search evidence and does not permit shadow results to affect the outward Stockfish anchor.

Malformed-input behavior remains backend-specific, so controller dispatches must remain canonical, legal, deduplicated, and non-empty.

Root-prefix separation also does not imply disjoint internal board-state expansion: independently owned prefixes may transpose later. PR #10 establishes assigned-prefix ownership only; recursive prefixes, measured transposition duplication, transfer, and VERIFY / RELOCK accounting remain later milestones.
