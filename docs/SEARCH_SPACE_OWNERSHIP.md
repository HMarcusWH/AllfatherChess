# Search-space ownership

## Purpose

The controller must prevent three strong solvers from wasting exploration compute on the same assigned problem.

"Zero overlap" therefore has a precise operational meaning: **no two engines may simultaneously own the same exploration shard**.

## Shard model

A shard is a controller-defined chess search region identified initially by a root move and later by a move prefix.

Conceptual state:

```text
Shard {
  id
  parent
  prefix
  owner
  generation
  budget
  state
  evidence
}
```

Allowed states:

```text
UNASSIGNED -> LEASED -> ACTIVE -> SEALED
                         |
                         +-> SPLIT -> child shards
```

## Hard exploration invariant

For every active shard `s`:

```text
owner_count(s) <= 1
```

Sibling assignments are pairwise disjoint. At the root, the union of assigned shards must equal the controller's active legal candidate set.

An ownership invariant failure is not a warning. Routing must stop and fall back to a safe declared mode.

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

The first implementation will therefore guarantee **region-level** non-overlap and instrument canonical-position fingerprints to measure residual transposition duplication.

A global position-ownership table is a later optimization and will only be introduced if measurements show that transposition duplication is materially expensive. Such a table must encode search-relevant state, including draw-sensitive context, rather than piece placement alone.

## Backend restricted-root capability v1

Before the controller can allocate disjoint root shards, every constituent backend must accept a valid non-empty legal root set and keep its root search inside that set.

For an invocation with authorized root set `S`:

```text
backend root candidate set ⊆ S
bestmove ∈ S
every reported root PV head ∈ S
```

PR #5 adds this primitive to Reckless and verifies the same valid-assignment command vocabulary against Stockfish and LC0.

This is **not yet controller ownership**. The repository still lacks leases, cross-engine exclusion, shard state transitions, recursive prefixes, and VERIFY / RELOCK accounting. Those belong to the later controller and ShardLedger milestones.

Malformed-input behavior is deliberately backend-specific. In particular, Stockfish and LC0 do not share the same behavior when a requested set contains no legal moves. The future Allfather adapter must therefore validate that dispatched controller shards are canonical, legal, deduplicated, and non-empty before invoking a backend.

Root-prefix separation also does not imply disjoint internal board-state expansion: independently owned prefixes may transpose later. PR #5 establishes only the backend primitive required for future root-prefix ownership.
