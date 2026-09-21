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


## Immediate controller stack

Fast suites, no engines required:

| Target | File | Proves |
| --- | --- | --- |
| `make shadow-tests` | `tests/controller/test_shadow_runtime.py` | four distinct roles with one anchor; the anchor may not be the perft oracle; a shadow death leaves authority health intact; exact disjoint `searchmoves` dispatch with `searchmoves` last; empty and terminal regions never dispatch; shadow bestmoves never leak outward; exactly one outward bestmove; stop drains; quit orphans nothing; a new position is never observed by a stale generation. |
| `make replay-tests` | `tests/controller/test_replay.py` | manifest completeness, JSON round-trip, sha256 integrity and tamper detection, detachment from live state, absence of derived/policy material, honest failure and terminal evidence, telemetry v1 conformance of every generated stream, and preservation of family/instance identity with distinct engine-native semantics. |
| `make residual-tests` | `tests/controller/test_residuals.py` | deterministic feature extraction; the scale firewall (cross-engine cp subtraction and LC0-scalar-vs-cp both raise); explicit shared support with an undefined reason when regions are disjoint; undefined-not-assumed labels before the first observation; missing streams reported rather than imputed; content-addressed traceable derived artifacts; calibration failing closed on low support, unknown buckets, foreign extractor versions, mismatched feature sets, and empty evidence. |
| `make routing-tests` | `tests/controller/test_budget_routing.py` | concurrent reservations cannot exceed the envelope; reserves are withheld from solver work; controller overhead is charged; native work is never summed across semantics; a stop is impossible without a calibrated, in-domain, supported, low-risk verdict; denied proposals degrade conservatively; decisions are deterministic under fixed evidence and a fixed clock; active mode never touches outward authority. |
| `make verification-tests` | `tests/controller/test_verification.py` | deterministic three-root nomination, disjoint-owner invariants, verify-config fail-closed behavior, parent/child provenance, stream tamper detection, and raw VERIFY integrity. |
| `make verification-analysis-tests` | `tests/controller/test_verification_analysis.py` | exact three-way common support, scale-free pairwise COMPARE, triad classification, EXPLORE→VERIFY adoption attribution, terminal-suffix RELOCK, incomplete-evidence exclusion, content addressing, and tamper refusal. |

`tests/fixtures/replay_fixtures.py` builds ten deterministic synthetic bundles
through the real telemetry adapters and the real manifest shape: stable
agreement, transient disagreement, late reversal, LC0-only divergence,
alpha-beta-only divergence, failed stream, missing stream, terminal position,
Chess960, and malformed telemetry. Some synthetic scenarios deliberately give workers *overlapping* regions.
Live EXPLORE remains pairwise-disjoint, while optional shadow VERIFY now creates
the corresponding explicit common-support overlap on the three EXPLORE
nominees. These fixtures therefore exercise the same structural comparison
library now consumed by the COMPARE/RELOCK derived layer. Dedicated
`verification_fixtures.py` scenarios add unanimous adoption, two-one and
all-different splits, late and bestmove-only RELOCK, incomplete evidence, and
anchor-outside-candidate cases.

`tests/fixtures/fake_uci_engine.py` honors `searchmoves`, node limits, MultiPV
token presence, scripted leader reversal, and abrupt mid-search exit.

## Real-engine contracts

After `make build-baselines`:

| Target | Proves |
| --- | --- |
| `make shadow-execution-contract` | fixed-node outward decision unchanged by shadow logic; three restricted workers overlapping a live anchor search; every shadow candidate, PV head, and bestmove inside its owned region; live roots matching the frozen legal-move oracle; four telemetry v1 streams; replay hashes verified; a real terminal position; no orphan processes. |
| `make active-routing-contract` | the whole pipeline: shadow sweep, derived features, fitted calibration, then active routing. Envelope never exceeded, no reservation left open, controller overhead charged, per-owner stage budgets held, every granted stop had all gates pass, every denied stop degraded to continued observation, policy material absent from raw evidence, fixed-node decision firewall intact. |
| `make verification-execution-contract` | real-engine pairwise-disjoint EXPLORE followed by explicit three-engine common-support VERIFY; identical root restrictions, telemetry-v1 conformance, parent-manifest provenance, no derived material, sole Stockfish outward authority, and no orphan processes. |
| `make verification-analysis-contract` | consumes that exact real VERIFY run; requires three reconstructed common-support trajectories, support=3 for all pairs, deterministic content addressing, a defined descriptive RELOCK status for complete evidence, and byte-identical raw evidence before/after derivation. |

Both contracts write a machine-readable report that includes an explicit
`not_claimed` list.

A routing optimization is not promoted on local speed alone; it must preserve
correctness invariants and survive the declared experimental protocol. No
strength claim is made by any test or contract in this repository.
