# LOCAL-1 implementation audit

See [FULL_GAME_QUALIFICATION.md](FULL_GAME_QUALIFICATION.md) for the campaign contract.
This is a candidate implementation record, not an exact-head CI success certificate.

## Evidence checks strengthened before qualification

The verifier reconstructs the expected complete input set; verifies Fastchess/engine/network
bytes both before and after execution; checks actual transmitted UCI options and clock
limits against the frozen arm policy; and rejects nonfinite/negative resource metrics.
The successful G3 prerequisite replay is copied into the campaign so the independent
validator replays the positive decision rather than trusting a report boolean. Forced
rule probes are reconstructed against their exact frozen fixture and sealed replay.

A failed paired job does not erase its losses or silently rerun. Later independent
pairings are still attempted; the campaign remains failed and preserves all evidence.
Incomplete or invalid baseline rows remain clearly flagged.

## First preflight findings

The first CI preflight on `26793dce0127c866552cb8c733e4cd86fcf3910c` caught two issues:

1. The original stalemate fixture already checked the non-moving king, so it was not a
   legal starting position. The replacement starts the queen on a6 and tests a6g6, which
   must be independently verified as a legal transition to stalemate.
2. Rapid synthetic successive requests did not all receive replay bundles. The existing
   coordinator deliberately runs a later request anchor-only when prior replay
   finalization is still pending. That is not equivalent to twelve finalized audit runs.
   The unit test now specifically tests reuse of finalized runs. The actual Fastchess
   campaign does **not** wait for replay artifacts between moves, and still fails on any
   missing per-ply G3 replay. This preserves the stronger lifecycle gate rather than
   making the test harness conceal a real deployment limitation.

Existing clock/deadline and delayed-replay regression tests remain unchanged. Qualification
may expose further current-controller limitations; those results must remain failures,
not a reason to silently relax the completeness or clock/resource gates.
