# STRENGTH-S3-B — Real specialist and Stockfish two-root sidecar

Status: experimental unplayed offline diagnostic; not a certified S3 runtime,
resource-qualified match, QualityWitness, Elo improvement or release.

This follows the Strength Rebuild Technical Specification §§6–9, 11, 13.

## Implemented
- The original S3-A native-clock Stockfish UCI frontend remains the sole
  bestmove publisher. Its unrestricted MultiPV1/cache and existing source
  flight are preserved; sidecar engines never write external bestmove.
- The actual exact-source Reckless or BLAS-LC0 UCI engine searches a full legal
  root universe (unless restricted by an external original searchmoves), and
  can nominate one legal nonincumbent move.
- A separate same-Stockfish/NNUE executable searches only the current
  incumbent and one challenger with MultiPV2 and two increasing total-node
  requests. It never writes to or alters the anchor process.
- The finite-state parser accepts a fully legal rank-1 and rank-2 PV at
  matched depth, exact same-score kind, monotone native time/nodes and a
  terminal bestmove from the pair. Mate/bounded/missing pairs cannot
  authorize quality. Source/network/history/nonce/epoch identities are bound.
- The S3-A scheduler reserves comparator budget first, enforces a single
  auxiliary slot and cancels stale helpers by original UCI token.
- Read-only s3b_verify reconstructs the raw proposal, PV scores, full
  history roots, signed source-bounded artifact index and S2 flight.

## Important limitations

The policy contains UNREVIEWED SOURCE_TEST_FIXTURE CPU/wall/RSS allowances.
Linux procfs observations are per-process, not whole-participant cgroups,
and do not include all startup/teardown and descendants. The deliberately
long native clock is an unplayed diagnostic aid, not the measured live
native helper-advice envelope. The harness is not yet a BackendManager-bound
host-qualified S3 runtime. These limitations keep ALL resource, strength,
quality, equal-compute, move-authority, public and release flags FALSE.

Even a real two-round comparator yields COMPARISON_RECORDED_UNQUALIFIED.
A genuine full cgroup host-cost catalog, source-licensed WorkGrants,
BackendManager runtime integration and independent physical verification
remain separate engineering and research gates.

## Usage

From a source-exact clean checkout, build the pinned engine bundle and
install hash-pinned chess==1.11.2. Then:

    export ALLFATHER_S3B_OFFLINE=1
    python3 -m tools.strength_lab.s3b_runner --family reckless
    python3 -m tools.strength_lab.s3b_runner --family lc0

Raw evidence lives under build/test-results/strength-s3b.
Independent re-verification:

    python3 -m tools.strength_lab.s3b_runner --verify PATH_TO_RUN

Regression tests:

    python3 -m unittest discover -s tests/strength_s3b -p 'test_*.py' -v

S4 move-quality/publication authority remains entirely unimplemented.


## Native-anchor stale dispatch, retained negative publication

The initial real workflow run 40621046264 showed the expected shallow
Stockfish incumbent race: the guarded Reckless go was correctly refused
when the anchor changed root epoch before source bytes crossed stdin.
An early optional refusal is not an authority failure or permission to
retry. The latest policy samples the genuine incumbent 450 ms after
first eligible PV observation and still rejects every later epoch change.
Native Stockfish continues independently to produce its real bestmove;
its original S3A flight remains retained even after optional denial.

The real-engine CI uses --require-comparison: it fails rather than
advertising complete two-root implementation when either the actual
specialist proposal or two native Stockfish comparison rounds are missing.
Negative abstentions remain separately source-bound.
