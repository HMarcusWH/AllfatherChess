# Telemetry contract fixtures

These JSONL fixtures exercise the frozen Allfather telemetry v1 contract.

Each file is a stream of raw observations for one search. The validator checks both record shape and cross-record lifecycle invariants. Valid fixtures demonstrate the intended portable vocabulary; invalid fixtures each violate at least one required invariant and must be rejected.

The fixtures are contract examples, not strength measurements and not captured benchmark results.

Run:

```bash
make telemetry-contract
```

Raw telemetry deliberately does **not** contain controller-derived residuals, top-k overlap, aggregate ranking snapshots, routing decisions, or verification conclusions.
