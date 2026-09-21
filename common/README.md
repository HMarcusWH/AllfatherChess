# Common model

The common package contains engine-neutral primitives shared by later controller layers.

Implemented now:

- `SearchIdentity` — immutable telemetry search identity;
- `TelemetryStream` — sequence ownership, monotonic observation-time checks, and search lifecycle events;
- canonical start-position FEN used by adapter integration tests.

Also implemented now:

- `search_request` — syntactic reconstruction of `position` / `go` commands, a
  stable `position_id`, and restricted `go` construction with `searchmoves`
  last;
- `residuals` — scale-free structural comparison primitives plus the scale
  firewall. Combining two differently-tagged engine values raises
  `ScaleMixingError`, so cross-engine centipawn arithmetic is a type error
  rather than a convention.

Future controller types will include:

- SearchRegion beyond root prefixes
- SolverSnapshot
- Verification / re-lock state

Engine-native scores and bounds remain tagged and are not silently converted into a universal evaluation. Raw telemetry is intentionally lower-level than derived residual types, which live in `controller/`.
