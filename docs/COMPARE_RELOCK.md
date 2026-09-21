# COMPARE / RELOCK derived analysis

## Status

Implemented as an **offline derived layer** over the raw common-support VERIFY
artifact.

This milestone does not start engines, change routing, spend active-mode
verification budget, mutate `RootShardLedger`, or alter outward decision
authority.

The data path is:

```text
pairwise-disjoint EXPLORE
        ↓
raw common-support VERIFY
        ↓
verification_analysis.py
        ↓
pairwise COMPARE
three-way convergence descriptors
EXPLORE -> VERIFY preference changes
descriptive RELOCK
        ↓
content-addressed verification-derived artifact
```

## Source firewall

Analysis starts only after both raw objects pass integrity checks:

1. parent replay manifest and streams;
2. verification child manifest and streams.

The child nominees are cross-checked against the completed EXPLORE bestmoves in
the parent replay. A mismatch is a provenance contradiction and derivation
stops.

Corrupt evidence is refused. Honest incomplete VERIFY evidence can be loaded,
but is marked `analysis_eligible = false` and cannot produce a three-way
COMPARE result or a successful/failed three-way RELOCK conclusion.

## Shared support

VERIFY v1 gives all three shadow solvers the same exact three-root set:

```text
V = (
    stockfish EXPLORE nominee,
    reckless EXPLORE nominee,
    lc0 EXPLORE nominee,
)
```

The COMPARE layer reuses `controller.residuals.compare_at()` unchanged.

Defined pair order:

```text
Stockfish <-> Reckless
Stockfish <-> LC0
Reckless  <-> LC0
```

For a valid three-way VERIFY run every pair must have shared support 3.

Derived pairwise fields remain scale-free:

- leader agreement;
- top-k overlap;
- rank agreement;
- PV divergence;
- candidate Jaccard.

No cross-engine score subtraction is introduced.

## Common active window

Checkpoint comparison uses controller observation time, not engine-native work
units.

```text
common_start = max(VERIFY start time per engine)
common_end   = min(VERIFY completion time per engine)
```

Only if `common_end > common_start` are synchronized checkpoints generated.
The configured checkpoint fractions are mapped into that interval.

This does not make nodes, MCTS visits, NN evaluations, CPU-ms, or GPU-ms
interchangeable.

## Three-way state

At every defined checkpoint, and separately at the final state, leaders are
classified as:

- `unanimous`;
- `two_one`;
- `all_different`;
- `undefined`.

`two_one` is a structural descriptor. It is not called a majority winner and
does not authorize a move.

## EXPLORE -> VERIFY transition

Because the three VERIFY roots are exactly the three distinct EXPLORE nominees,
every final VERIFY candidate maps back to the solver that originally introduced
it.

For each verifier the derived artifact records:

- its EXPLORE nominee;
- its final VERIFY leader;
- whether it changed;
- which solver originally nominated the selected VERIFY leader;
- whether it retained its own candidate.

This supports a candidate-adoption matrix without assigning chess correctness.

## Descriptive RELOCK

The frozen definition is:

```text
relock_definition = terminal-suffix-v1
```

For each completed verifier, the analysis finds the earliest time in the final
uninterrupted suffix during which its primary leader equals its eventual final
leader.

If no primary line was reported but a valid final `bestmove` exists, that
verifier's terminal lock begins at completion.

### RELOCK_OBSERVED

Only when all three trajectories are complete and valid, all three final leaders
are the same move, and all three terminal lock starts are defined.

```text
relock_at_ms = max(
    stockfish terminal lock start,
    reckless terminal lock start,
    lc0 terminal lock start,
)
```

### RELOCK_FAILED

All three trajectories are complete and valid, but their final leaders are not
unanimous.

### RELOCK_UNDEFINED

The evidence is incomplete, malformed, missing a required verifier, or otherwise
not eligible for a three-way conclusion.

`RELOCK_OBSERVED` means only that the three independent searches ended on the
same candidate and each stopped leaving that candidate over its observed
terminal suffix.

It does **not** mean the move is correct, optimal, safe to play, or licensed by
RACR/ICW.

## Anchor relation

The unrestricted Stockfish anchor is retained as a descriptive reference:

- anchor final move;
- whether that move was one of the three VERIFY candidates;
- how many verifiers matched it;
- whether a unanimous VERIFY move matched it.

The anchor is authority in the current runtime, not an independent chess-truth
oracle.

## Derived artifact

Default layout:

```text
build/
    replays-verify/
        <run_id>/
            manifest.json
            verification/
                manifest.json
                *.jsonl

    verification-derived/
        verification-derived-<hash>/
            analysis.json
```

The derived artifact contains:

- extractor and RELOCK-definition versions;
- checkpoint parameters;
- exact parent and VERIFY source hashes;
- common-support checkpoints;
- final pairwise comparisons;
- three-way final state;
- EXPLORE -> VERIFY transition rows;
- descriptive RELOCK;
- anchor relation.

The content address covers every raw hash and parameter that can change the
output. Raw replay files are never modified.

## Research sweep

`scripts/verification-evidence-sweep.py` can collect the VERIFY profile across
the frozen corpus and report:

- eligibility;
- final convergence patterns;
- RELOCK status;
- RELOCK fraction;
- candidate-adoption matrix;
- self-retention/change counts;
- unanimous source owner;
- pairwise final leader agreement.

The shipped LC0 verification profile remains backend-light/random, so this
sweep is **mechanism evidence only**, not strength-qualified complementarity
evidence.

## Claim boundary

This milestone establishes reproducible common-support analysis and a frozen
descriptive RELOCK definition.

It does not establish:

- that agreement means correctness;
- that RELOCK predicts a better chess move;
- that LC0 contributes useful strength-qualified information;
- that a foreign nominee improves Stockfish;
- that VERIFY repays its compute;
- that VERIFY should be activated under the competitive resource envelope;
- that Allfather is stronger than a constituent engine.

The next architectural question is whether persistent common-support
disagreement is informative enough to justify recursive localization.
