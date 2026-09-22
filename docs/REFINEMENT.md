# Shadow REFINE execution

## Status

PR #17 wires the qualified recursive PrefixShardLedger v2 substrate into the
live **shadow research path** without changing outward decision authority or the
active equal-resource controller.

The live research chain is now:

```text
pairwise-disjoint EXPLORE
        ↓
common-support VERIFY
        ↓
final VERIFY disagreement facts
        ↓
deterministic REFINE target nomination
        ↓
exact Stockfish perft-1 child shell
        ↓
PrefixShardLedger v2 split + deterministic child partition
        ↓
descendant-position REFINE searches
        ↓
raw refinement/manifest.json
```

The unrestricted Stockfish anchor remains the sole outward bestmove authority.

## Authority boundary

REFINE is observational only.

It cannot:

- vote on the outward move;
- replace or constrain the anchor;
- start a new stage after the anchor completion boundary;
- authorize a branch from descriptive RELOCK;
- run in active mode;
- claim that disagreement predicts chess error.

An in-flight REFINE stage follows the existing
`shadow.on_anchor_complete = drain | cancel` policy. A new REFINE stage may
never be opened after the outward answer has been emitted.

## Nomination v1

The frozen nomination method is:

```text
verify_final_disagreement_union_v1
```

Inputs are only the completed raw VERIFY stage bestmoves.

If all three VERIFY final leaders are the same, REFINE is recorded as
`not_applicable`.

If two or three distinct VERIFY final leaders remain, the target roots are the
distinct final leaders in the already-frozen VERIFY candidate order, capped by
`refinement.max_targets`.

This is a nomination rule, not a correctness rule. No offline RELOCK analysis is
imported into the live controller.

## Root-v1 to PrefixShardLedger-v2 mirror

EXPLORE continues to use `RootShardLedger` v1 unchanged.

After clean EXPLORE + VERIFY, REFINE builds a separate PrefixShardLedger v2 with
the same:

- generation;
- candidate-root universe;
- owner set;
- root partition.

The v2 roots are advanced to SEALED to mirror the already-completed EXPLORE
fact. The raw refinement artifact records:

```text
origin = mirror_of_completed_root_v1
source_root_v1_snapshot
initial_v2_snapshot
final_v2_snapshot
```

This does not claim that PrefixShardLedger v2 executed the original EXPLORE
search.

## Exact child oracle

For each nominated root, the configured Stockfish shadow oracle is temporarily
moved to:

```text
external position + target root
```

and answers:

```text
go perft 1
```

The worker is restored to the external synchronized position in all cases.

The global manager position is never changed and the unrestricted anchor is
never repositioned.

A terminal target has zero legal children. It is recorded as terminal and is not
passed to `split_shard()`, whose contract correctly requires a non-empty child
set.

## Child ownership

For a non-terminal target, the exact oracle children replace the sealed parent:

```text
SEALED target root
    ↓
RETIRED parent
    ├── LEASED child
    ├── LEASED child
    └── ...
```

Children initially inherit the target's EXPLORE owner. Then the frozen
instrumentation partition:

```text
child_index % owner_count
```

is applied through atomic PrefixShardLedger transfers.

No score, prior, residual, RELOCK state, calibration model or history affects
the child partition.

## Descendant dispatch

All child shards under one target share one descendant engine position.

For target `e2e4` and one owner's children `e7e5 c7c6`:

```text
position startpos moves e2e4
go nodes N searchmoves e7e5 c7c6
```

`common/prefix_dispatch.compile_descendant_region()` builds that request while
preserving the external game's existing history, FEN base and variant.

Each owned child shard moves:

```text
LEASED -> ACTIVE
```

at dispatch. It moves to SEALED only after the stage completes cleanly and its
telemetry stays inside the assigned child region.

A failed or cancelled stage is never cosmetically sealed.

## Per-instance position divergence

PR #17 adds a narrow runtime capability for idle shadow workers:

```text
set_shadow_position(instance, descendant)
restore_shadow_position(instance)
legal_moves_at_shadow_position(...)
```

Only instances with role `shadow` may use it. Active searches may not be
repositioned.

The coordinator explicitly tracks:

- instances currently diverged to descendant positions;
- an in-flight descendant perft oracle.

Quiesce, cancellation, worker-error cleanup and generation supersession include
those states. A worker that cannot restore in time is quarantined rather than
being treated as synchronized.

## Raw artifact

REFINE does not change Replay schema v1.

It writes a sibling artifact:

```text
<run>/
    manifest.json
    verification/
        manifest.json
        ...

    refinement/
        manifest.json

        target-000-e2e4/
            stockfish-shadow.jsonl
            reckless-shadow.jsonl
            lc0-shadow.jsonl
        ...
```

The manifest binds:

- parent replay run id + manifest hash;
- source VERIFY id + manifest hash;
- generation and external position id;
- VERIFY final leaders and deterministic targets;
- the root-v1 source snapshot;
- initial/final PrefixShardLedger v2 snapshots;
- exact per-target child-oracle result;
- exact child partition and shard ids;
- descendant position commands;
- stage commands;
- stream hashes and sizes;
- run/target dispositions.

It contains no correctness, value, routing or strength conclusion.

## Telemetry

Existing telemetry v1 is reused.

REFINE stages carry:

```json
{
  "controller": {
    "execution_mode": "shadow",
    "phase": "REFINE",
    "instance_role": "shadow",
    "owner": "...",
    "decision_authority": false
  }
}
```

Each stream is bound to the descendant `position_id`, not the original external
position id.

Candidate heads, PV heads and final bestmoves must stay inside the stage's owned
child set. Escape is a failed stage and quarantines that shadow worker.

## Observation routing

The stdout observer now selects the stream for the **currently active stage**:

```text
active REFINE stage
    → REFINE stream
else active VERIFY stage
    → VERIFY stream
else
    → parent EXPLORE / anchor stream
```

Merely having a finalized VERIFY object is no longer enough to capture later
engine output into the VERIFY stream.

## Integrity

`verify_refinement_integrity()` checks:

- parent replay provenance;
- VERIFY provenance;
- generation/position identity;
- deterministic target nomination;
- frozen owner order;
- root-v1 source snapshot equality;
- canonical unique oracle child sets;
- exact child-index partition;
- pairwise child disjointness and exact coverage;
- descendant position commands;
- stage searchmoves;
- full prefix identities;
- stream hashes/sizes;
- telemetry phase and root containment.

Completed artifacts must contain the complete nominated target set. Honest
incomplete artifacts may contain only a deterministic prefix of that target set.

## Scope limit

Live REFINE v1 goes exactly one level below a nominated root.

PrefixShardLedger v2 can represent arbitrary recursive depth, and the standalone
contract already qualified deeper prefixes mechanically. PR #17 intentionally
does not add repeated recursive scheduling in one controller generation.

## Validation

Fast tests use deterministic fake engines to force:

- unanimous VERIFY → no REFINE targets;
- two-one and all-different target nomination;
- exact child partition;
- live EXPLORE → VERIFY → REFINE execution;
- separate descendant telemetry;
- raw-artifact provenance and tamper detection.

The real-engine contract additionally requires Stockfish, Reckless and LC0 to
execute actual descendant child regions produced by the Stockfish perft oracle.

## Claim boundary

PR #17 establishes that the live shadow controller can safely execute:

```text
root disagreement
→ exact one-ply child shell
→ pairwise-disjoint descendant search
```

It does **not** establish:

- that VERIFY disagreement predicts a bad move;
- that REFINE resolves disagreement;
- that REFINE improves Stockfish;
- that recursive localization saves compute;
- that REFINE is worth its CPU/GPU/time cost;
- that active mode may spend verification reserve on REFINE;
- any Elo or equal-resource strength improvement.

Those are the questions for active budget integration and later causal/strength
experiments.
