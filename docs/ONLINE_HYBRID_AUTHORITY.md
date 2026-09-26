# M14-G3 — Clock-aware staged hybrid authority

**Status:** merged in PR #39  
**Main commit:** `c301e9986566febfbb7978d55c5a3d3429423cff`  
**Qualified PR head:** `9ea858eed4133ea1bc8e89137176b4e5cf2eb316`  
**Current next gate:** LOCAL-1 full-game lifecycle qualification  
**Roadmap:** [ROADMAP.md](ROADMAP.md)

M14-G3 is the first qualified composition allowed to replace the Stockfish anchor under
an ONLINE TimePlan using the real ONLINE-2 CPU bundle and the G1/G2 same-process staged
VERIFY machinery.

## Authority rule

Authority is fail-closed. A hybrid move is eligible only when:

- the G2 route is exactly the supported `BUY_STAGED_VERIFY` path;
- all required staged VERIFY extension searches complete on the same candidate set,
  process generation and evidence plane;
- the counterfactual proposal is built from the staged terminal plane, never a mixture of
  base and extension terminals;
- the proposal is frozen inside the qualified soft-deadline/anchor boundary;
- TimePlan, legal roots/searchmoves, route identity, resource ledger, process generation
  and physical-measurement state are current;
- proposal move and evidence digest match the authorization/final-decision identities;
- the hard deadline has not expired and no explicit stop/failure revoked authority.

Any failed gate emits the exact Stockfish anchor move unless the request itself has reached
an explicit terminal clock/anchor failure, which remains `bestmove 0000`.

## Positive qualification

The reference qualifier does not accept a bookkeeping-only HYBRID where the proposal
happens to equal the anchor. It runs a fixed predeclared real-backend position set and
requires at least one case where:

1. Stockfish produces anchor move A;
2. complete staged specialist evidence freezes proposal B;
3. B differs from A;
4. the G3 authorization gates pass;
5. B is the move actually written outward.

The exact qualified tree also passes the ONLINE-2 real-network qualification, independent
LC0 real-inference qualification, controller shell, telemetry, merge gate and baseline
engine workflows.

## Timing and publication semantics

The G3 reference profile may use the full 4000 ms outer ONLINE-2 wall envelope rather than
the narrower anchor-only reference cap so real BLAS EXPLORE + staged VERIFY can finish
inside one declared envelope. The frozen fixture uses bounded low-visit/node stages rather
than laundering a larger clock.

The soft deadline closes new optional computation. It does not revoke evidence already
legally frozen before that cutoff. Explicit user stop, stale/superseded generation or hard
failure does revoke authority.

Publication is deadline-safe and atomic at the UCI output boundary. The client-visible
`bestmove` is fenced against the small interval before `ClockSearch.finished` publishes,
so immediate post-move protocol traffic cannot enter the previous measurement interval.

## Resource boundary

PR #39 separates two resource boundaries:

1. backend process endpoints freeze before reuse;
2. controller CPU remains live through resource-relevant route/native-work reconstruction
   and reservation settlement, then the complete interval freezes.

Post-move readiness opens only after the complete measured interval is immutable. A closed
dispatch permit is an expected rejected dispatch, not evidence that a healthy worker failed.

## Replay / audit identity

The final decision is bound to:

- TimePlan/request identity;
- generation/position;
- staged terminal source;
- route decision digest;
- proposal move;
- proposal evidence digest;
- authorization snapshot/result;
- emitted move;
- resource/replay source hashes where available.

Replay recomputes the sealed proposal deadline and identity checks. Recomputing a JSON hash
over a tampered decision is insufficient.

## Deliberate nonclaims

G3 does not promote learned SKIP. `allow_skipped_extension_authority=false` remains part of
the qualified contract.

G3 makes no Elo, move-quality, equal-envelope superiority or deployed-bot claim. It also
does not yet prove complete-game lifecycle correctness. That is LOCAL-1.
