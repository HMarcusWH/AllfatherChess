# Hybrid decision authority

## Status

PR #22 introduced the first deterministic hybrid **proposal** from typed
specialist evidence while preserving Stockfish as the sole outward authority.

M14-C adds the first deliberately narrow live authority transfer: an
already-frozen PRE_ANCHOR proposal may replace the Stockfish move only in an
explicit active hybrid profile and only after a separate bounded
`DecisionAuthorization` gate passes. Any missing or failed gate returns the
exact Stockfish anchor line.

The authority ladder is therefore:

```text
Observation
    |
    v
DecisionEvidence
    |
    v
DecisionProposal
    |
    v
DecisionAuthorization
    |
    v
Outward Decision
```

Current M14-C state:

```text
Observation             IMPLEMENTED
DecisionEvidence        IMPLEMENTED
DecisionProposal        IMPLEMENTED
DecisionAuthorization   IMPLEMENTED / BOUNDED GRANT
Hybrid outward move     IMPLEMENTED FOR QUALIFIED movetime_v0
Stockfish fallback      DETERMINISTIC DEFAULT ON ANY DENIAL
```

## Why the decision layer is separate

The controller already had three very different authorities:

- raw engine observation;
- compute/resource authorization;
- outward Stockfish decision authority.

A hybrid system must not blur those into one object.

In particular:

```text
ResourceAuthorization != DecisionAuthorization
DecisionProposal       != DecisionAuthorization
RELOCK_OBSERVED        != DecisionAuthorization
three-way agreement    != chess correctness
```

PR #22 adds a typed proposal layer while preserving those separations.

## Source evidence

The policy consumes two source families.

### CrossFeedView

The typed cross-feed view supplies:

- run / generation / position identity;
- the exact common VERIFY candidate set;
- original EXPLORE nominator for each candidate;
- source-typed VERIFY / optional REFINE observations;
- native score semantics;
- source work counters;
- evidence-loss / truncation state.

### VerificationTerminalEvidence

The final verifier choices come from the authoritative VERIFY stage terminal
facts:

```text
VerificationStage.bestmove
```

for live execution, and:

```text
verification/manifest.json
    stages[].bestmove
```

for sealed replay.

The policy deliberately does **not** infer a verifier's final choice from the
last `candidate.update`. A `search.complete.bestmove` may differ from the
last information line.

## Policy v1: unanimous_verify_v1

The first policy is intentionally narrow.

```text
typed evidence carries an explicit fault
    -> NO_PROPOSAL_EVIDENCE_FAULT

VERIFY incomplete in either typed view
    -> NO_PROPOSAL_VERIFY_INCOMPLETE

terminal move missing
    -> NO_PROPOSAL_TERMINAL_INCOMPLETE

terminal move outside the exact common candidate set
    -> INVALID_EVIDENCE

three terminal bestmoves not unanimous
    -> NO_PROPOSAL_NONUNANIMOUS

all three terminal bestmoves identical
    -> PROPOSED(move)
```

There is no:

- two-out-of-three vote;
- weighted engine vote;
- Stockfish priority vote;
- numeric score averaging;
- LC0 value-to-centipawn conversion;
- correctness label.

If the common candidate set is:

```text
Stockfish nominee: e2e4
Reckless nominee:  d2d4
LC0 nominee:       g1f3
```

and final VERIFY is:

```text
Stockfish -> g1f3
Reckless  -> g1f3
LC0       -> g1f3
```

the policy may freeze:

```text
PROPOSED g1f3
source_owner = lc0
```

That source-owner field means only that LC0 originally introduced the candidate
during pairwise-disjoint EXPLORE.

It is not a claim that LC0 caused the other engines to converge or that the move
is objectively best.

## RELOCK relationship

`controller/verification_analysis.py` remains an independent derived analysis
layer.

Its `terminal-suffix-v1` states:

- `RELOCK_OBSERVED`;
- `RELOCK_FAILED`;
- `RELOCK_UNDEFINED`.

PR #22 does not make those states authorization conditions.

The counterfactual policy reads the underlying terminal VERIFY facts directly.
Tests require the independent COMPARE/RELOCK extractor to agree on terminal
unanimity when both views are otherwise eligible.

That is a consistency check, not an authority transfer.

## Pure decision module

`controller/decision.py` owns no:

- engine process;
- UCI stdout;
- replay writer;
- routing policy;
- budget reservation;
- file I/O.

It defines immutable types:

```text
DecisionCandidate
VerificationTerminalEvidence
DecisionEvidence
DecisionDisposition
DecisionEvaluation
DecisionProposal
DecisionAuthorization
CounterfactualDecision
```

`DecisionAuthorization` is deliberately a separate type.

In PR #22 its constructor rejects any authorization grant.

## Frozen evidence digest

The live proposal cannot depend on the final
`crossfeed/manifest.json`, because that artifact is intentionally written only
after the anchor completes.

Instead, the policy computes a deterministic SHA-256 over canonical in-memory
inputs:

```text
CrossFeedView
+
VerificationTerminalEvidence
+
decision evidence version
```

This becomes the proposal's `evidence_digest`.

After finalization, the same evidence is rebuilt from sealed raw sources and
must reproduce the same digest and policy result.

## PRE_ANCHOR versus POST_ANCHOR

Proposal timing is part of the evidence.

The shadow worker performs all policy work outside the coordinator lock.

Only the final causal stamp is published under the same lock used by the anchor
completion boundary:

```text
compute evidence / evaluate policy
        |
        v
acquire coordinator lock
        |
        +-- anchor_completed is false -> PRE_ANCHOR
        |
        +-- anchor_completed is true  -> POST_ANCHOR
        |
        v
publish immutable DecisionProposal
```

This means a slow policy cannot block the anchor reader thread.

The two proposal classes must never be mixed in later value-of-compute or
strength analysis.

A POST_ANCHOR proposal is still useful counterfactual research evidence, but it
could not have influenced that real-time decision under the observed deadline.

## Runtime topology

```text
EXPLORE
   |
   v
VERIFY
   |
   +--> optional REFINE
   |
   v
CrossFeedView
   |
   v
VerificationTerminalEvidence
   |
   v
DecisionEvidence
   |
   v
unanimous_verify_v1
   |
   v
DecisionProposal
```

Independently:

```text
Stockfish anchor
     |
     v
UciFrontend._on_search_complete()
     |
     v
bestmove
```

Those paths do not meet in PR #22.

`controller/uci_frontend.py` is intentionally unchanged by this milestone.

## Sealed artifact

After the anchor and source evidence finalize:

```text
<run>/
  manifest.json
  verification/
    manifest.json
  refinement/                  optional
    manifest.json
  crossfeed/
    manifest.json
  decision/
    counterfactual.json
```

The decision artifact binds:

- parent replay SHA-256;
- VERIFY manifest SHA-256;
- cross-feed id;
- cross-feed manifest SHA-256;
- cross-feed content SHA-256;
- frozen proposal;
- anchor terminal move and completion time;
- descriptive proposal-vs-anchor relation;
- deterministic content digest.

## Deterministic replay

`verify_counterfactual_integrity()` replays the decision one layer above the
cross-feed contract.

It:

1. validates parent replay;
2. validates VERIFY;
3. validates cross-feed;
4. reconstructs CrossFeedView from sealed raw telemetry;
5. reconstructs terminal VERIFY facts from the VERIFY manifest;
6. rebuilds DecisionEvidence;
7. re-runs `unanimous_verify_v1`;
8. requires the stored proposal semantics and evidence digest to match;
9. checks PRE/POST_ANCHOR timing against the recorded anchor completion;
10. recomputes the proposal/anchor relation;
11. recomputes the artifact content digest.

A modified proposal cannot become valid merely by recomputing its JSON hash.

## Counterfactual relation to the anchor

The anchor is attached **after** the proposal is already frozen.

The policy does not receive the anchor move as an input.

The final artifact may record:

```text
proposal_matches_anchor
would_change_outward_move
```

These are descriptive counterfactual fields only.

`would_change_outward_move = true` does not mean the hybrid proposal is
better.

That question is deferred to prospective labels and strength testing.

## Resource accounting

The decision policy starts no engine search and consumes no solver reserve.

In active mode, its computation is charged through the existing controller
overhead path as:

```text
counterfactual_decision_build
```

A later active-authority milestone must include this cost inside the declared
decision deadline/resource envelope.

## Failure behavior

Any live policy construction failure results in:

```text
no DecisionProposal
+
parent note
+
Stockfish anchor behavior unchanged
```

Any derived artifact-sealing failure results in:

```text
raw replay / VERIFY / cross-feed remain authoritative
+
the affected derived audit artifact is invalid
+
the already-emitted move is never rewritten
```

In PR #22 that emitted move is necessarily Stockfish. Under M14-C it may be
the previously authorized HYBRID move or the deterministic anchor fallback.
Persistence failure never promotes Reckless, LC0, or a stale proposal and never
creates a second outward decision.

## Research sweep

`scripts/counterfactual-decision-sweep.py` reports structural counts only:

- valid / invalid artifacts;
- policy dispositions;
- PRE_ANCHOR / POST_ANCHOR counts;
- proposal equal to anchor;
- proposal different from anchor;
- proposal source owner.

It does not score correctness or Elo.

Those frozen records are intended to become input to the next
value-of-compute/calibration milestone.

## Claim boundary

PR #22 can establish:

> Given qualified typed common-support evidence, AllfatherChess can freeze and
> independently replay a deterministic hybrid move proposal while preserving
> Stockfish as the sole outward decision authority.

It does not establish:

- that unanimity predicts the objectively best move;
- that a proposal differing from Stockfish is an improvement;
- that agreement is better than disagreement;
- that VERIFY or REFINE repays its resource cost;
- that LC0's current backend is strength-qualified;
- that the system improves Elo;
- that a DecisionProposal should receive live DecisionAuthorization.

The next calibration milestone evaluates whether purchasing specialist evidence
changes later frozen decisions in a useful, resource-justified way.


---

## M14-C — bounded active authority v0

M14-C does not turn proposal generation into authority. The live sequence is:

```text
EXPLORE / VERIFY / optional REFINE
        |
        v
typed CrossFeedView
        |
        v
DecisionEvidence
        |
        v
DecisionProposal        (must be frozen PRE_ANCHOR)
        |
anchor completion boundary
        |
        v
DecisionAuthorization   (bounded in-memory only)
      /      \
 HYBRID      denied
   |           |
proposal    exact Stockfish fallback
      \       /
       outward bestmove
```

The callback may inspect only already-owned memory. It may not dispatch an
engine, run an oracle, start VERIFY/REFINE, read or write an artifact, load a
calibration, or take a procfs sample before stdout emission.

### v0 request class

Live hybrid authority is intentionally restricted to exactly one positive
`go movetime N` limit (with optional `searchmoves`) inside the declared
wall envelope. `nodes`, `depth`, clock controls, `ponder`, `infinite`,
unknown tokens, and mixed limit sets remain Stockfish-authority requests.

### Authorization facts

A grant requires, simultaneously:

- a `PROPOSED` move frozen before the anchor boundary;
- proposal/evidence digest identity and current run/generation/position;
- complete, fault-free VERIFY evidence;
- membership in the qualified legal-root universe and any external
  `searchmoves` restriction;
- the supported request class and a bounded anchor request;
- an anchor reservation;
- budget, partition and wall state still inside the declared envelope;
- zero open indispensable specialist reservations and complete settlement;
- accounted declared GPU budget;
- enabled/available physical measurement with no already-known completed-stage
  measurement failure;
- current healthy backend generation;
- no previously latched anchor-only fallback.

These are authorization conditions, not chess-correctness claims.

### Resource timing

The anchor's terminal physical resource sample remains deliberately
**post-output**. M14-C must not move procfs IO in front of `bestmove`.
Consequently the live gate uses only already-known resource state. The sealed
`resource.json` can later invalidate an equal-resource experimental claim,
but it cannot retroactively change a move that was already played.

### Actual-decision artifact

`decision/counterfactual.json` remains the PR #22 proposal-versus-anchor
research artifact. M14-C additionally writes `decision/final.json` after
output, binding the actual `HYBRID` or `ANCHOR_FALLBACK` choice to the
available replay, VERIFY, cross-feed, route, resource, and counterfactual source
hashes.

A hybrid move different from the anchor root is emitted without the anchor's
optional `ponder` continuation. Anchor fallback preserves the original anchor
line byte-for-byte.

### Claim boundary

M14-C establishes a replayable authority mechanism. It does **not** establish
that `unanimous_verify_v1` chooses a stronger move, that three-way agreement is
a correctness certificate, or that Allfather is stronger than any constituent
engine. Those remain strength-campaign questions.
