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

The soft deadline closes new computation. It does not revoke a proposal already
frozen legally before that cutoff. Explicit user stop, generation supersession, or
hard deadline failure does revoke authority.

## Deliberate nonclaims

G3 does not promote learned SKIP. allow_skipped_extension_authority=false is part
of the contract. It makes no Elo, move-quality, equal-envelope superiority, or
deployed-bot claim. Full-game lifecycle qualification remains LOCAL-1.
