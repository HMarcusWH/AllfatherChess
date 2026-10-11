# ONLINE-4B — source-locked, guarded offline Lichess recovery

## Scope and release boundary

**OFFLINE ONLY**. This work remains disconnected from Lichess. It does not
authorize public accounts, BOT conversion, live tokens, production deployment,
playing-strength claims or network recovery.

The original Stockfish/Reckless/BLAS-LC0 bundle, canonical b4 profile,
HYBRID move authority, clock-derived envelopes, UCI protocol and ONLINE-3A
release seal remain unchanged. The pinned upstream lichess-bot remains byte-
identical in build/vendor/lichess-bot.

## Source-locked upstream adaptation

deploy/lichess/recovery_overlay.py verifies the original Git blob IDs for
lib/lichess.py, lib/engine_wrapper.py and lib/lichess_bot.py at the exact
frozen df7e730d upstream commit. Its string substitutions refuse missing,
duplicate or modified anchors. The patch and the four recovery modules
are copied into an entirely disposable staging checkout. The untouched
upstream checkout and upstream Git identity are verified before each stage.
Source-and-patch hashes are retained in recovery-overlay-manifest.json.

The guarded copy adds only:
- outbound request admission and a global 429 cooldown;
- a legal non-null UCI gate before HTTP move submission;
- durable move-intent recording before any move POST;
- server-history observation before any new search;
- bounded worker restart of already active games and deduplication of
  repeated gameStart events.

It never chooses a chess move, alters controller search, substitutes an
opening book, or grants the bridge resignation/abort authority.

## Durable SQLite protocol

MoveJournal uses WAL, synchronous FULL, BEGIN IMMEDIATE and a persistent
per-scenario writable directory mounted separately from the temporary
Allfather release. Each session owns an exclusive OS flock and a fresh
database epoch. A stale child cannot submit further API calls because every
operation checks its epoch in SQLite.

Move intent states:

PREPARED -> SUBMITTING -> ACK_OBSERVED -> CONFIRMED
                      \-> UNKNOWN_ACK

CONFIRMED requires observing the matching UCI move in an authoritative
full server history. An HTTP 200 is only an observed acknowledgment; a
server snapshot omitting a pending move does NOT prove the request will
never commit. Any unresolved intent blocks another engine search or move
submission. Duplicate HTTP attempts are refused before reaching the socket.

A fresh authoritative history must be a legal, prefix-extending sequence
from startpos and may not regress behind previously confirmed history.
Conflicting move identities, stale game events, missing durability,
bad session ownership, terminal-to-active regressions and journal corruption
all fail closed. The canonical position identity is reconstructed from
the exact standard start FEN and complete UCI history.

## Global API containment

The pinned upstream normally handles 429 per endpoint and can repeatedly
attempt an abort. The guarded adapter instead durably stores an account-wide
cooldown of at least 60 seconds, or a longer valid Retry-After. New API calls
are blocked during that cooldown; already-open passive streams can remain
connected. Abort, resign, chat, takeback, upgrade and outbound matchmaking
are prohibited in this offline policy regardless of cooldown state.

The simulated low-clock R12 case previously caused bestmove 0000 and a
/ move/0000 HTTP attempt. The new move gate rejects null/illegal results
before any HTTP operation, persisting an unavailable-engine decision. It
does not fabricate a substitute move.

## Stream interruption and process restart

The old bridge's individual game-stream worker does not automatically
resume after a truncated chunk. ONLINE-4B's supervised offline runner
terminates the old process and cleans all detached descendants inside
its private PID namespace before activating a new session epoch.
The source-locked adaptation starts workers for active games returned
at startup. Each worker must obtain a fresh gameFull state and reconcile
that full authoritative move history before any new search.

On loss of a move acknowledgment, restarting can confirm a move that
the server already committed; if confirmation is missing or ambiguous,
the journal stays unresolved and further submissions are suspended.

## Qualification evidence and claims

The v2 runner uses the same isolated fake Lichess server and source-locked
four-process Allfather UCI release as ONLINE-4A, preserving independent
chess-authority logs, HTTP attempts, injected faults, full PGN, server
commits, guard events, session epochs, actual SQLite journal, sanitized
bridge logs and bounded replay evidence.

The independent v2 qualifier reconstructs legal moves and verifies the
complete history, durable intents, source/overlay hashes, disallowed API
operations, actual restart evidence and rate-limit containment. It has
separate fields for:

- harness_qualified
- containment_qualified_offline
- recovery_qualified_offline
- network_recovery_qualified (ALWAYS FALSE here)
- public_bot_release (ALWAYS FALSE here)

A safe suspension may satisfy containment but never counts as successful
continuation. A green CI job is not production certification.

See qualification/online-recovery-v2.json and
.github/workflows/online-recovery-v2.yml for the frozen matrix. The next
milestones remain a production image/host/licensing audit and an expressly
authorized live canary; neither occurs here.

## Exact-head diagnostic repairs and acceptance rules

The first ONLINE-4B full integration run exposed two verifier defects:
R12 was incorrectly required to produce `bestmove 0000` under a
synthetic low clock, even when Allfather instead produced legal moves.
R13's actual `-9` bridge exit was omitted from the earlier
ONLINE-4A witness adapter. The corrected verifier accepts whichever
legal outcome R12 actually produces while independently prohibiting
all malformed/null UCI HTTP move submissions. A deterministic guard
unit test rejects null/illegal moves with zero network requests.

R13 now records the actual SIGKILL delivery to the bridge process
group, its process-exit return code, and verified descendant-process
drain before activating a different durable owner epoch. A label alone,
a forged exit code, or a second stream without new moves cannot
qualify recovery.

An observed server checkmate is not enough to certify that the client
processed the final state. After the server records a terminal position,
the offline supervisor performs a bounded, post-game *observational*
journal barrier. Its report contains the authoritative final ply,
durable journal ply, terminal flag, and any server-accepted moves still
unconfirmed. A missing or timed-out barrier makes the completed-game
qualification fail closed. This barrier does not change chess clocks,
search budgets, or the move selection policy.

The independent verifier certifies `RECOVERED` only if the second
owner epoch actually prepared and submitted a new move, received
a matching server commit, and persisted its authoritative
confirmation. Reconciling only an earlier epoch's accepted move
is insufficient. Required restart scenarios must pass for **every**
required color, rather than just one successful representative.

Verdicts are deliberately distinct: `NORMAL_COMPLETION` for
the no-fault baseline, `SAFE_CONTINUATION` for a faulted but
successfully continued single session, `RECOVERED` for genuinely
new post-restart play, `SAFE_SUSPENSION` when explicit guard evidence
shows bounded containment, `INCONCLUSIVE` when evidence is
insufficient, and `UNSAFE` on an invariant violation.
An inconclusive result is not qualified containment.

The synthetic low-clock override remains a diagnostic fixture,
not evidence about production clock rates. No offline result here
grants public-play, deployment or BOT account authority.
