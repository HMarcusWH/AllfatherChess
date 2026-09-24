# Typed cross-feed evidence plane

## Status

This milestone introduces a decision-inert cross-feed evidence layer over the
existing EXPLORE -> VERIFY -> optional REFINE path.

It does **not** add another engine search phase. The cross-feed evidence layer
itself has no move authority. M14-C may separately authorize one already-frozen
hybrid proposal in its narrow active `movetime_v0` profile; otherwise
Stockfish remains the exact fallback authority.

## Why this layer exists

PR #14 already created the expensive common-support operation: each EXPLORE
owner nominates one root, and all three shadow engines independently re-search
the same three-root set under phase VERIFY. PR #17 added the first REFINE child shell when completed VERIFY remained
non-unanimous. PR #18 placed VERIFY / REFINE under one resource scheduler, and
M14-D generalized REFINE into bounded recursive expansion while keeping the
M14-C authority profile pinned to depth two.

The missing capability was a typed way to expose that evidence to later
decision code without:

- treating native engine scores as interchangeable;
- inventing a second duplicate search protocol;
- mutating RootShardLedger / PrefixShardLedger ownership;
- mixing derived evidence into raw replay;
- or granting an observation automatic move authority.

Cross-feed v1 fills only that gap.

## Runtime topology

```text
pairwise-disjoint EXPLORE
        |
        v
existing common-support VERIFY
        |
        +--> optional bounded REFINE
        |
        v
CrossFeedView                 in-memory, immutable, decision-inert
        |
        v
source manifests finalize
        |
        v
crossfeed/manifest.json       sealed derived artifact
```

No engine receives a `go` command because cross-feed is enabled.

## Configuration

Cross-feed is explicit and ablatable:

```json
{
  "crossfeed": {
    "enabled": true,
    "policy": "typed_verify_refine_v1"
  }
}
```

The runtime refuses cross-feed outside `shadow` / `active` mode and refuses
it unless VERIFY is enabled.

REFINE is optional. When REFINE exists, the view and final artifact bind it.
When no REFINE run exists, the artifact records its absence rather than
inventing evidence.

## In-memory data model

`controller/crossfeed.py` defines three main layers.

### CandidateHint

One retained source observation:

- decision-root move;
- observed move;
- source owner / instance / family;
- source phase;
- native rank;
- PV prefix;
- source-native evaluations;
- source-native work counters;
- search id;
- source run id;
- telemetry sequence / observed time;
- source stage disposition.

For VERIFY, the decision-root move and observed move are the same root.

For root-shell REFINE, the decision-root move remains the nominated root while
`observed_move` is the descendant move and the PV prefix is rooted by the
decision candidate. M14-D recursive expansion records remain separately bound
inside the REFINE-v2 artifact; M14-E exposes them through an adapter-only
projection rather than mutating `CrossFeedView`.

### CrossFeedCandidate

One VERIFY candidate plus:

- the EXPLORE owner that originally nominated it;
- all retained VERIFY / REFINE hints for that candidate.

Repeated telemetry updates are compressed to the latest update per
source-search / decision-root / observed-move key. Raw JSONL remains the full
authoritative history.

### CrossFeedView

One immutable run-local view containing:

- run / generation / position identity;
- policy version;
- VERIFY identity and disposition;
- whether VERIFY evidence is complete;
- optional REFINE identity / disposition;
- the three decision-root candidates;
- explicit evidence faults.

The view can later be consumed by the counterfactual decision laboratory
without requiring file I/O on the authority path.

## Score firewall

Cross-feed preserves native semantics exactly.

Examples:

```text
stockfish.uci_cp
reckless.uci_cp
lc0.uci_score.centipawn
lc0.uci_score.Q
lc0.uci_wdl
```

The layer contains no:

- cross-engine score delta;
- centipawn conversion between engines;
- weighted vote;
- universal score;
- winner field;
- correctness label.

A later policy may reason about candidate identity, rank structure,
convergence, provenance, or separately qualified calibrations. Cross-feed v1
does not supply a numeric common scale.

## Ownership firewall

Cross-feed does not mutate either ownership ledger.

The invariant remains:

> Every EXPLORE frontier region has exactly one owner. Duplicate work is legal
> only under an explicit non-EXPLORE phase such as VERIFY or REFINE.

If a Stockfish verifier examines an LC0-nominated root, that duplicate work is
already represented by VERIFY. Cross-feed merely records the resulting
evidence.

## Artifact layout

When enabled and a VERIFY run exists:

```text
<run>/
  manifest.json
  verification/
    manifest.json
    ...
  refinement/                 when REFINE exists
    manifest.json
    ...
  crossfeed/
    manifest.json
```

The cross-feed manifest is derived and is written only after its source
manifests finalize.

It binds:

- parent replay SHA-256;
- VERIFY id and manifest SHA-256;
- optional REFINE id and manifest SHA-256;
- source telemetry stream identities and SHA-256 values;
- generation / position;
- the exact in-memory view;
- one deterministic content digest.

`verify_crossfeed_integrity()` re-checks both the cross-feed digest and the
existing parent / VERIFY / REFINE integrity contracts.

## M14-E adapter projection

M14-E deliberately does not change `CrossFeedView`, its schema, or its
serialization. M14-C already hashes `view.as_dict()` into
`DecisionEvidence`, so adding recursive adapter fields there would silently
change authority-facing evidence identity.

Instead, `adapters/crossfeed/evidence.py` builds a separate
`CrossFeedAdapterEvidence` object from the immutable view plus optional
REFINE-v2 recursive expansion evidence. The adapter projection records complete
source context:

- engine family / owner / instance;
- VERIFY or REFINE phase;
- exact source search and scope id;
- complete source prefix and depth;
- candidate universe and whether that universe is complete;
- source-local MultiPV rank;
- native evaluation/work semantics;
- source stage disposition.

This lets the engine-specific adapters qualify two subprocess-safe proposal
types without creating a new live phase:

```text
VERIFY_SET
REFINE_PREFIX
```

Source rank is explicitly context-local. A rank may be compared only with
another rank from the same family, phase, search id, prefix and candidate
universe. No cross-engine or cross-depth rank arithmetic is allowed.

The initial tactical alarm is similarly narrow: a native evaluation with
`kind=mate` may create a categorical alarm for that same engine family. No
centipawn/Q/WDL threshold conversion is introduced.

The adapter layer is pure. It does not dispatch engines, reserve resources,
mutate either ownership ledger, create a new telemetry phase, call the router,
or participate in `DecisionAuthorization`. Those integrations belong to later
routing milestones.

## Controller cost

Cross-feed launches no solver work.

In active mode, in-memory view construction is charged through the existing
controller-overhead accounting path under label `crossfeed_build`.

The sealed artifact is written only after the anchor decision has completed, so
source-integrity checking and filesystem work cannot delay the outward move.

## Failure behavior

Cross-feed is non-authoritative.

If live view construction fails:

```text
record parent note
-> no CrossFeedView
-> continue existing run
-> Stockfish anchor authority unchanged
```

If final artifact sealing fails:

```text
raw parent / VERIFY / REFINE artifacts remain authoritative
-> no valid cross-feed artifact
-> already-emitted anchor move is unchanged
```

No fallback promotes another engine.

## Validation

Fast tests cover:

- deterministic candidate order;
- collapse of repeated telemetry without losing provenance;
- source-family / owner consistency;
- incomplete / lossy evidence remaining incomplete;
- native score-semantics preservation;
- configuration fail-closed behavior;
- one fake end-to-end run;
- source-tamper detection;
- single Stockfish outward bestmove;
- no new cross-feed engine search.

The real-engine contract uses
`config/allfather.crossfeed.validation.json` and requires valid parent,
VERIFY, REFINE and cross-feed artifacts while proving the recorded outward move
is still the Stockfish anchor move.

## Claim boundary

This milestone establishes:

> Existing common-support specialist evidence can be composed into one typed,
> provenance-bound, replayable cross-feed view and sealed artifact without new
> solver search or new chess decision authority.

It does **not** establish:

- that cross-feed improves move quality;
- that agreement is correctness;
- that disagreement is useful;
- that REFINE repays its cost;
- that native score values are interchangeable;
- that the current candidate compression is optimal;
- that Allfather beats Stockfish, Reckless, or LC0.

M14-E additionally establishes that the existing typed evidence can be
translated deterministically into engine-safe VERIFY-set and REFINE-prefix UCI
proposals without changing the live controller. Later regime/routing milestones
may decide whether any such proposal is worth authorizing and dispatching.
