# STRENGTH-S2 — Native Stockfish observe-only incumbent

**Status: EXPERIMENTAL SOURCE CANDIDATE; NOT E00-QUALIFIED; NO strength,
equal-compute, external-game, publication or release authorization.**

S2 is opt-in only. Existing schema-v1/v2 G3/J12 configurations, the original
three-candidate verification evidence, resource/clock outcomes and UCI frontend
publication behavior are unchanged when the S2 configuration is not selected.

## Run surface

Build the exact source-bound engine first, with the original upstream network
and original Git checkout intact. No alternative download/substitution is
permitted. The v3 configuration lives at
`config/allfather.strength-s2-observe.experimental.json`:

- Exactly **one Stockfish anchor**; MultiPV1, one engine process, no helpers
- `strength.policy=strength_s2_incumbent_observe_v1` and
  `strength.mode=observe_only`; `apply` is not supported here
- Source commit and tracked Git-tree cleanliness are verified when loading
- Hashes of the exact source config, executable and options are carried into
  the search identity. Running from a directory without a full Git checkout
  fails closed rather than fabricating a source SHA.
- The original supported native `go wtime ... btime ... winc ... binc ...`
  (and optional `movestogo` / `searchmoves`) is passed to Stockfish **byte-for-byte**.
  A standalone `go movetime N` is also passed through as-is.
  `ponder`, `infinite`, `nodes`, mixed/duplicate/unknown limits,
  Chess960 and missing side clocks are refused.

Launch the existing UCI frontend using the explicit config:

~~~bash
python3 -m controller --config config/allfather.strength-s2-observe.experimental.json
~~~

This is *not* an authorized live-bot invocation and must be used only on an
offline, suitable experiment host.

## Clock safety and the single writer

S2 reuses `ClockSearch`, the existing UCI input timestamp origin, bounded
generation-specific STOP, PIPE_BUF atomic write, post-output generation fence,
and the original `BackendManager` managed Stockfish process.

`native_anchor_clock_watchdog_v1` does not translate native clocks to a
fixed-movetime command. It computes an absolute hard loss-prevention guard
from the mover's remaining clock **without** using increment as spendable
pre-move time. The softer cutoff exists for STOP/reap headroom. Both are
*watchdog* limits, not commands modifying Stockfish's internal time manager.

A watchdog timeout without a legal terminal engine move remains a protocol
failure (`bestmove 0000`), not a fabricated fallback or a waived game.
No candidate or optional comparison work exists to delay the native anchor.

Only `UciFrontend` writes `bestmove`. The S2 `FinalSelection` discriminator
requires `chosen_line == anchor_line`, `replaces_anchor=false` and
`would_authorize=false`. It is **not** a legacy `HYBRID` or J12 authorization.
The bounded publication evidence is appended in memory immediately *after*
the stdout write; file persistence occurs only at session shutdown.

## Epoch, PV and evidence boundary

Every turn binds an unguessable session and search nonce, the complete
recorded position command (including move history), side, variant,
full-history hash, external-root restriction, exact source/profile/binary
digests and a generation. Intermediate Stockfish `info` PV roots are
accepted only from that active token. Every incumbent change increments
`root_epoch`, including A→B→A; eligible later reports increment `progress_seq`
even if the root remains unchanged.

UCI `score cp`, `score mate`, upper/lower bounds and missing scores are
different cases. Unbounded `cp` or `mate` is a completed report from a
selective search, **not** a mathematically exact minimax value. The S2
`PairFrames` parser is a pure preparatory contract for future S3; no two-root
comparison dispatch or authority is implemented.

On quit, source-identified append-only event-chain journals are durably
written under `build/strength-s2-evidence/`. The journal records the native
clock plan, exact position, incumbent progress, terminal anchor and visible
stdout publication, or an abort. The independent
`controller.strength.integrity.verify_flight` reconstructs hash links,
generation/root epochs and final publication rather than trusting a
producer `passed=true` flag. It **always reports `qualified=false`**.

An evidence-write failure, event overflow, missing stdout receipt, unsupported
variant, stale token or source mutation is a negative experimental result.
It cannot authorize a move replacement or conceal a tournament loss.

## Tests and open acceptance

Source-only/regression CI:

~~~bash
python3 -m unittest discover -s tests/strength_s2 -p 'test_*.py' -v
python3 -m unittest tests.controller.test_uci_frontend tests.controller.test_online_time -v
~~~

This stage includes synthetic UCI race, A→B→A, exact native go,
mixed score-frame, stale event, mutated journal and v1/v2 isolation tests.

**Not yet established:** E00 512-history exact pinned Stockfish equivalence,
measured native watchdog overhead and timeout distribution, real physical
resource receipts on a matching host, independently replayed real game
evidence, and any specialist gain relative to extra Stockfish compute.
Those must be completed and retained before S3/S4 is allowed to claim
experimentally qualified move replacements.

Next work: S3 bounded single proposer and same-evaluator two-root comparison,
still observe-only. S4 is a *separate* quality-and-safety publication contract.
