# Shadow REFINE execution

## Status

PR #17 wired the qualified PrefixShardLedger v2 substrate into the live
one-shell shadow REFINE path. PR #18 added active-mode resource authorization.
M14-D (PR #30) generalizes that shell into optional bounded multi-level
recursion while preserving the same ownership, measurement and authority
firewalls.

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
clean regional terminal nominations
        ↓
optional breadth-first recursive zoom
        ↓
raw refinement/manifest.json
```

REFINE itself carries no outward bestmove authority. M14-C may authorize a
separately frozen hybrid proposal from VERIFY evidence, but M14-D explicitly
rejects `hybrid_authority` profiles with `refinement.max_depth > 2`.
Deeper recursion is therefore evidence-only in this milestone.

## Authority boundary

REFINE is observational only.

It cannot:

- vote on the outward move;
- replace or constrain the anchor;
- start a new stage after the anchor completion boundary;
- authorize a branch from descriptive RELOCK;
- bypass active-mode budget authorization;
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

## Bounded recursive REFINE

The compatibility default remains:

~~~text
max_depth = 2
~~~

where a nominated root is depth 1 and the existing child shell is depth 2.
Every shipped profile that predates M14-D pins that limit explicitly.

A research/active-specialist profile may opt into deeper recursion with:

~~~json
{
  "recursive_nomination_method": "stage_terminal_bestmove_v1",
  "max_depth": 4,
  "max_expansions": 6
}
~~~

The recursive controller is deterministic breadth-first. A deeper expansion is
eligible only when its full prefix is still a SEALED PrefixShardLedger frontier
leaf, the source restricted stage completed cleanly, and a fresh resource
reservation is granted.

`stage_terminal_bestmove_v1` means only that one clean restricted regional
search ended on an owned child. It is a nomination to zoom that region again,
not a global best-move claim.

Each deeper expansion repeats the same sequence:

~~~text
SEALED nominated leaf
  -> reserve REFINE_ORACLE
  -> exact Stockfish perft-1 children
  -> atomic split / transfer
  -> reserve every non-empty owner stage
  -> restricted descendant searches
  -> telemetry containment audit
  -> seal clean children
  -> restore external positions
  -> derive next regional nominations
~~~

Recursion stops on the depth cap, expansion cap, terminal child universe,
resource denial, outward decision boundary, cancellation or failure. Work is
never inherited from a parent's resource authorization.

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

The qualified REFINE stack now establishes that the live controller can safely
execute:

```text
root disagreement
→ exact one-ply child shell
→ pairwise-disjoint descendant search
→ explicit regional nomination
→ bounded repeated exact-prefix zoom
```

It does **not** establish:

- that VERIFY disagreement predicts a bad move;
- that REFINE resolves disagreement;
- that REFINE improves Stockfish;
- that recursive localization saves compute;
- that the recursive nomination policy improves move quality;
- that REFINE is worth its CPU/GPU/time cost;
- that deeper recursion may participate in M14-C hybrid authority;
- any Elo or equal-resource strength improvement.

Those are the questions for active budget integration and later causal/strength
experiments.


## Active-mode accounting

When `mode: active` enables REFINE, the nomination, PrefixShardLedger and raw
artifact semantics above are unchanged. What changes is resource authority:

- every root or deeper Stockfish perft-1 child oracle needs a fresh REFINE reservation;
- every non-empty owner child region at every depth needs its own REFINE reservation before
  dispatch;
- descendant positioning/restoration overhead is charged to the controller;
- denied reservations produce no hidden computation;
- stage settlement uses observed wall duration × configured threads;
- the final route certificate must close with zero open reservations and with
  the REFINE partition inside its declared cap.

Shadow mode remains the deliberately over-budget research observatory. See
`docs/ACTIVE_SPECIALIST_SCHEDULER.md`.
