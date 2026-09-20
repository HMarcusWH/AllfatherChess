# Hybrid telemetry schema

Status: draft v0.1.

The common telemetry layer normalizes observations without pretending that Stockfish, Reckless, and LC0 have identical search semantics.

## Common envelope

Every record should eventually contain:

```text
schema_version
search_id
engine
timestamp
position_id
shard_id
phase
budget
iteration
elapsed_ms
nodes_or_playouts
candidate_count
leader_move
runner_up_move
ranking
pv
terminal_fact
engine_specific
```

## Common derived evidence

Controller-side derived fields may include:

- leader changes over a rolling horizon;
- top-k candidate overlap;
- rank correlation;
- PV prefix overlap / divergence;
- score or value margin trend within an engine;
- compute spent per candidate;
- search-result sensitivity to added budget;
- verification reversals.

## Native evidence remains tagged

Stockfish / Reckless examples:
- depth / seldepth;
- alpha-beta score and bound type;
- aspiration fail-low/fail-high;
- LMR / re-search counters;
- TT, pruning, and cutoff counters.

LC0 examples:
- visits;
- policy mass;
- Q/value estimates;
- effective candidate count;
- NN/cache submissions;
- speculative prefetch work;
- batch sizes and NN timing.

A Stockfish TT bound is not an LC0 Q value. The controller may compare calibrated consequences, but the raw values retain native semantics.

## Existing LC0 instrumentation

The pinned LC0 snapshot already includes opt-in defect telemetry from its merged research PR #2. That data becomes an input to the common schema rather than being discarded or renamed blindly.
