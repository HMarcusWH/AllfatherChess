# ONLINE-4A — Offline fault injection and acceptance diagnostics

## Authority boundary

**OFFLINE DIAGNOSTICS ONLY.** This stage does not qualify network recovery,
live-game safety, production deployment, playing strength or BOT conversion.

- Source: exact-commit ONLINE-3A Allfather sealed release, canonical b4 policy.
- Bridge: immutable upstream revision in deploy/lichess/bridge.lock.json,
  with separately audited local game-policy overlay.
- Runtime: localhost fake Lichess, a fresh network-isolated container for
  each experiment, bounded process and resource cleanup, no real tokens.
- Existing ONLINE-3B complete-game qualification remains mandatory.

The simulated server has exactly one mutable authoritative chess board.
The transport trace is a sequence of observations, not a second chess model.
The independent qualifier builds a separate board from recorded commits.

## Fault specification

tests/fixtures/lichess/recovery_scenarios.json contains R00–R14, each with
an exact action, endpoint, one-based request/stream ordinal, eligible colors
and either integration or contract scope. tests/lichess/fault_plan.py refuses
unknown fields and only fires the configured injection once.

Normal ONLINE-3B operation is unchanged when no scenario is provided.
In recovery mode the existing FakeLichess HTTP handler injects faults at the
exact request/commit/response or NDJSON delivery boundary. StreamSessions
provides per-connection queues, a fresh gameFull snapshot on reopening a
game stream, and single-active global event-stream fencing.

Fault classes include precommit disconnect, postcommit lost acknowledgment,
committed move with HTTP 504, HTTP 429 without commit, duplicate/stale states,
incomplete chunk framing, synthetic low-clock interruption and forced
bridge-process termination.

A completed server socket write is NOT evidence the client consumed the
response. We separately record the handler request, authority commit,
response write, intentional close and any subsequent retry.

## Evidence

The real pinned-bridge runner is deploy/lichess/run_recovery.py. Unlike a
positive-only game test, it preserves truthful partial evidence in a finally
block even if the bridge exits, hangs, or the server game remains incomplete.

Each integration scenario produces under build/test-results/online-recovery-v1:

- fault-plan.json and fault-activation.json
- source-manifest.json with exact source SHA and tracked test-code hashes
- transport-trace.ndjson, http-attempts.ndjson, server-commits.ndjson,
  delivered-events.ndjson
- authoritative-game.json, stream-sessions.json, game.pgn
- result.json, including process termination and observations
- sanitized bridge.log when capture succeeded
- replay-index.json and bounded replay/route manifests when available

A replay bestmove is not proof of HTTP submission, and an HTTP submission is
not proof of server acceptance. Missing replay links are explicit and remain
unqualified until a separate durable correlation protocol is built.

scripts/qualify-online-recovery.py independently checks exact source hashes,
one-shot injection witnesses, chess legality, board FEN changes, chronological
request/commit correlations, terminal rules when applicable, and retained
artifact hashes. It does not trust a producer-generated passed flag.

## Separate dispositions

Harness integrity: whether all required faults fired and the evidence was
independently reconstructable.

Observed behavior: SAFE_CONTINUATION, OBSERVED_COMPLETION,
OBSERVED_STOP_OR_UNRESOLVED, or UNSAFE_WITNESS.

Recovery behavior qualified: ALWAYS FALSE in ONLINE-4A.
Network recovery qualified: ALWAYS FALSE in ONLINE-4A.
Public bot release authorized: ALWAYS FALSE in ONLINE-4A.

An unsafe bridge witness is a legitimate diagnostic success if the harness
recorded it faithfully; an untriggered or corrupt fault is a harness failure.
Never collapse these outcomes into a single green/unsafe label.

## CI

.github/workflows/online-recovery.yml contains three jobs:

1. recovery-contracts: fast, engine-free HTTP, scheduler, stream and
   independent-evidence tests.
2. offline-fault-integration: one exact-head sealed release and pinned bridge,
   reused across separate network-isolated scenario containers. Even aborted
   games retain available evidence.
3. independent-recovery-evidence: a fresh exact-head checkout independently
   validates downloaded raw artifacts and emits a separate qualification report.

ONLINE-3B normal games, ONLINE-3A seal and canonical b4 authority remain
unchanged. The offline fixture image is not a production OCI artifact.

## Limitations

Fault selection is deterministic, but engine search duration, bestmove choice
and process scheduling need not be bit-for-bit repeatable. Low-clock tests
represent a labeled synthetic clock override, NOT a verified continuously
decreasing Lichess clock. A fabricated network disconnection cannot prove
what the client received before the close.

ONLINE-4B must implement durable uncertain-move state, authoritative history
reconciliation, bounded restarts and single active-owner fencing, then qualify
these mechanisms independently before any real Lichess account or challenge
is authorized.
