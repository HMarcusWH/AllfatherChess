# Tests

Planned test families:

- baseline engine regression;
- UCI lifecycle and process isolation;
- shard-ownership invariants;
- zero accidental exploration overlap;
- telemetry contract validation and deterministic replay of recorded telemetry;
- verification/re-lock accounting;
- fallback behavior;
- fixed-budget strength and compute comparisons.

A routing optimization is not promoted on local speed alone; it must preserve correctness invariants and survive the declared experimental protocol.

The telemetry v1 contract fixtures live under `tests/telemetry/`. Run `make telemetry-contract` to validate both accepted and deliberately rejected JSONL streams without building the engines.

Telemetry adapter syntax/semantic tests run with `make telemetry-adapters`. After all three engines are built, `make telemetry-adapter-integration` captures real Stockfish, Reckless, LC0, and LC0-defect JSONL streams and validates them against telemetry v1.


Controller/process tests run with `make controller-tests` and use deterministic fake UCI backends to exercise startup, one-reader stdout demultiplexing, transactional shutdown, duplicate-go rejection, and `isready` during `go infinite`.

After all three real engines are built, `make hybrid-shell-contract` verifies that the external identity is AllfatherChess, Stockfish remains the transparent anchor, direct and shell node-limited best moves agree, and `stop` completes an infinite search after a concurrent readiness barrier.


Root ShardLedger invariant tests run with `make shard-ledger-tests`. They attack exact partition coverage, overlap rejection, atomic failure, one-shot leasing, owner-atomic activation/sealing, empty terminal ledgers, detached snapshots, deterministic IDs, revision behavior, and concurrent conflicting mutations.

After all three real engines are built, `make shard-ledger-contract` qualifies the managed Stockfish `go perft 1` legal-root oracle against frozen legal-move cases, checks Chess960 castling encoding, builds a live startpos ledger, and sequentially proves that Stockfish, Reckless, and LC0 remain inside their test-only owned `searchmoves` regions.


PR #11 shadow-execution tests must additionally prove:

- a distinct unrestricted Stockfish anchor can search concurrently with `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow`;
- every shadow worker receives exactly its non-empty `RootShardLedger.active_roots(owner)` region and no unauthorized PV/bestmove root escapes that region;
- shadow telemetry uses distinct `engine_instance` identities and validates unchanged against telemetry v1;
- one replay manifest binds the exact position/request, engine identities/configuration, ledger snapshots, owned root sets, telemetry streams, and completion/failure outcomes;
- shadow failures are recorded without transferring outward bestmove authority;
- stop/quit drains all shadow work without orphaning processes or producing extra outward bestmoves;
- fixed-node anchor equivalence remains unchanged by shadow decision logic; timed equivalence is not claimed without separate resource-isolation evidence.
