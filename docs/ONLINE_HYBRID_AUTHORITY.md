# M14-G3 - Clock-aware staged hybrid authority

M14-G3 is the first composition allowed to replace the Stockfish anchor under an
ONLINE TimePlan. It reuses the exact ONLINE-2 real-network CPU bundle and the
G1/G2 same-process staged VERIFY machinery.

## Authority rule

Authority is fail-closed. A hybrid move is eligible only when:

- the G2 route is exactly BUY_STAGED_VERIFY;
- all three staged VERIFY extension searches complete on the same candidate set and generation;
- the counterfactual proposal is built from the staged terminal plane, never a mixture of base and extension terminals;
- the proposal is frozen before the soft compute deadline and before anchor completion;
- the TimePlan, legal roots/searchmoves, resource ledger, process generation and physical-measurement state are current; and
- the hard deadline has not expired and no explicit stop/failure invalidated authority.

Any failed gate emits the exact Stockfish anchor move. A hard clock/anchor failure
remains an explicit bestmove 0000.

The reference qualification does not accept a bookkeeping-only HYBRID where the
proposal happens to equal the anchor. It runs a fixed, predeclared opening-position
set and requires at least one real Stockfish/Reckless/BLAS-LC0 case where the
authorized staged proposal is different from the Stockfish anchor and is the move
actually written outward.

The G3 reference profile may use the full 4000 ms outer ONLINE-2 wall envelope (rather than ONLINE-2's 2000 ms anchor-only reference cap) so real BLAS EXPLORE + staged VERIFY can finish inside one declared envelope. All other TimePlan semantics remain pinned.\n\nReference-host evidence showed that a 256-visit real-BLAS LC0 EXPLORE could consume the entire 3650 ms soft window under concurrent anchor load. G3 therefore does not manufacture a larger clock: its integration fixture uses 16-node/visit EXPLORE, 16-node base VERIFY, and 32-node staged VERIFY, with a conservative 750 CPU-ms specialist reservation per verifier stage.\n\nThe soft deadline closes new computation. It does not revoke a proposal already\nfrozen legally before that cutoff. Explicit user stop, generation supersession, or
hard deadline failure does revoke authority.

## Deliberate nonclaims

G3 does not promote learned SKIP. allow_skipped_extension_authority=false is part
of the contract. It makes no Elo, move-quality, equal-envelope superiority, or
deployed-bot claim. Full-game lifecycle qualification remains LOCAL-1.
