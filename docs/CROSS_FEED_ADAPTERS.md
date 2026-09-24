# Engine-specific cross-feed adapters

## Status

M14-E qualifies a pure translation layer between typed Allfather evidence and
ordinary UCI restrictions accepted by Stockfish, Reckless and LC0.

The adapters are **not** wired into live routing or move authority in this
milestone.

## Architectural boundary

```text
CrossFeedView --------------------+
                                  |
REFINE-v2 recursive expansions ---+--> CrossFeedAdapterEvidence
                                         |
                                         +--> Stockfish adapter
                                         +--> Reckless adapter
                                         +--> LC0 adapter
                                                  |
                                                  v
                                      typed operation proposals
                                                  |
                                             NO DISPATCH
```

The existing `CrossFeedView` remains unchanged because M14-C hashes its
serialized form into `DecisionEvidence`. Recursive REFINE evidence is projected
into the adapter plane separately.

## Adapter evidence

`adapters/crossfeed/evidence.py` preserves:

- decision-root identity;
- observed move;
- source owner / family / instance;
- VERIFY or REFINE phase;
- source scope and search identity;
- exact source prefix and depth;
- candidate universe;
- whether the candidate universe is complete;
- source-local rank;
- PV prefix;
- source-native evaluations and work;
- stage disposition.

Sealed replay projection first requires the existing cross-feed integrity
contract to pass.

## Operation vocabulary

### VERIFY_SET

Compiles one non-empty duplicate-free subset of the existing cross-feed
candidate roots into:

```text
position <external-position>
go <bounded-limit> searchmoves <candidate subset>
```

The adapter may not introduce a move absent from the typed cross-feed candidate
set.

### REFINE_PREFIX

Compiles one evidence-supported full prefix through the existing qualified
prefix compiler.

For:

```text
e2e4 e7e5 g1f3
```

the result is structurally:

```text
position startpos moves e2e4 e7e5
go <bounded-limit> searchmoves g1f3
```

The prefix must already exist in typed evidence. Adapter code cannot invent a
new descendant branch.

## Engine-specific semantics

The UCI request shape is shared. Engine-specific interpretation remains
namespace-bound:

```text
Stockfish -> stockfish.*
Reckless  -> reckless.*
LC0       -> lc0.*
```

No adapter converts:

- Stockfish cp into LC0 Q;
- LC0 Q/WDL into alpha-beta cp;
- Reckless cp into another engine's scale;
- native work counters into one universal work value.

## Source-rank firewall

MultiPV rank is ordinal only inside one exact source context:

```text
family
phase
search_id
source_prefix
candidate_universe
```

A rank from another engine, depth, prefix or search is not comparable. M14-E
therefore emits `CandidatePriorityHint` records with their complete context
identity rather than a universal priority score.

## Tactical alarms

The first alarm rule is categorical and source-native:

```text
evaluation.kind == "mate"
AND evaluation.semantics starts with the adapter family namespace
    -> TacticalAlarm
```

No cp/Q/WDL thresholds are introduced.

## Authority and resource firewall

The adapter layer does not:

- launch a process;
- reserve CPU/GPU;
- call the router;
- mutate RootShardLedger or PrefixShardLedger;
- create a new telemetry phase;
- participate in `DecisionAuthorization`;
- emit an outward bestmove.

M14-F may classify the situation. M14-G may later decide whether an adapter
proposal is worth resource authorization and dispatch.

## Validation

Fast tests prove:

- deterministic operation identity;
- candidate-subset containment;
- evidence-supported prefix requirements;
- recursive-prefix geometry;
- native alarm isolation;
- context-local rank identity;
- explicit evidence faults fail closed.

The real-engine contract starts the three vendored solver families directly and
requires each to accept:

- a three-root VERIFY_SET;
- a two-root VERIFY subset;
- an evidence-supported REFINE_PREFIX.

Every returned bestmove must remain inside the adapter-declared search region.

## Claim boundary

M14-E establishes subprocess compatibility and semantic separation only.

It does not establish:

- move-quality improvement;
- Elo gain;
- useful route priority;
- tactical-alarm predictive value;
- optimal candidate transfer;
- native tree integration;
- live cross-engine scheduling superiority.
