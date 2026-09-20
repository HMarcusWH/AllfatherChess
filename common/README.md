# Common model

The common package contains engine-neutral primitives shared by later controller layers.

Implemented now:

- `SearchIdentity` — immutable telemetry search identity;
- `TelemetryStream` — sequence ownership, monotonic observation-time checks, and search lifecycle events;
- canonical start-position FEN used by adapter integration tests.

Future controller types will include:

- Position identity
- SearchRegion / ShardId
- Budget and hardware-resource state
- SolverSnapshot
- Candidate ranking
- Route phase
- Verification / re-lock state

Engine-native scores and bounds remain tagged and are not silently converted into a universal evaluation. Raw telemetry is intentionally lower-level than future SolverSnapshot/ranking/residual types.
