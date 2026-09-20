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
