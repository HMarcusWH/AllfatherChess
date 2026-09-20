# Hybrid telemetry contract v1

Status: telemetry v1 contract.

The telemetry layer preserves raw backend observations in one replayable event vocabulary without pretending that Stockfish, Reckless, and LC0 have identical search semantics. Raw evidence is deliberately separated from controller-derived rankings, residuals, overlap metrics, verification conclusions, and routing decisions.

The machine-readable contract is `schemas/telemetry/v1.contract.json`. The standard-library validator is `scripts/validate-telemetry-contract.py`.

## Event lifecycle

A search is a JSONL event stream:

```text
search.started
      |
      +--> candidate.update
      +--> candidate.update
      +--> native.event
      +--> terminal.fact   (only with independent provenance)
      |
      '--> search.complete
```

The five event types are:

- `search.started`: establishes immutable search identity, position reconstruction, variant/move encoding, requested limits/root set, and optional controller context.
- `candidate.update`: one backend candidate/PV observation. It is not an atomic global ranking snapshot.
- `native.event`: lossless backend-specific evidence that cannot honestly be normalized into the common fields.
- `terminal.fact`: independently justified rules/tablebase terminal evidence. A missing best move is not sufficient.
- `search.complete`: terminates the stream and carries the backend best move and optional ponder move.

Every search must begin with exactly one `search.started`, event `sequence` must strictly increase, adapter `observed_ms` must not decrease, identity fields must remain stable, and no event may follow `search.complete`.

## Common identity and ordering

Every event contains:

```text
schema_version
event_type
search_id
sequence
observed_ms
engine
engine_instance
position_id
```

`sequence` is authoritative ordering within one search. `observed_ms` is adapter monotonic receipt/observation time, not an engine-reported clock and not a wall-clock timestamp.

`engine` identifies the solver family (`stockfish`, `reckless`, or `lc0`). `engine_instance` identifies the concrete managed role/process. In PR #11 this distinction is required because both `stockfish-anchor` and `stockfish-shadow` are Stockfish instances with different authority and dispatch contracts. A consumer must not infer authority from the solver family alone.

`search.started` additionally records enough position information for replay. Request limits may carry non-negative numeric values or boolean UCI flags such as `infinite` / `ponder`:

```text
variant
move_encoding
position.base_fen
position.moves[]
request
controller?    (optional until the controller exists)
```

The v1 variant/encoding pairs are:

```text
standard -> uci
chess960 -> uci_chess960
```

This distinction is required because castling move strings differ under Chess960 conventions.

## Candidate updates

A `candidate.update` contains one observed candidate:

```text
candidate.multipv_index
candidate.move
candidate.pv[]
candidate.evaluations[]?
work[]?
engine_time?
native?
```

`multipv_index` preserves the UCI/backend observation. It is not promoted to a durable global rank. Candidate updates from an iterative search are not assumed to form an atomic frame. When a backend omits `multipv` for its primary PV (LC0 commonly does this at MultiPV=1), the adapter normalizes that protocol-default primary line to `multipv_index = 1`.

When a PV is present it is non-empty and `pv[0] == candidate.move`.

The contract does not require MultiPV indices to be contiguous, and `search.complete.bestmove` is not required to have appeared in a previous candidate event.

## Evaluations

Engine values remain source-tagged. A common evaluation entry contains `kind`, `bound`, `perspective`, `semantics`, and either a scalar `value` or WDL components.

Bounds are `none`, `lower`, or `upper`. An ordinary unqualified UCI score uses `none`; it is not relabeled as mathematically exact.

Perspectives may be `root_player`, `white`, `black`, or `unknown`. PR #7 must only promote a backend to a stronger perspective label when source inspection/tests justify it.

Examples of distinct semantics include `stockfish.uci_cp`, `reckless.uci_cp`, `lc0.uci_score.centipawn`, and `lc0.uci_wdl`. Numerically equal values with different semantics are not automatically comparable.

## Work and time

There is no `nodes_or_playouts` field. Work observations are arrays of tagged counters, for example:

```json
{"value": 512, "unit": "nodes", "semantics": "stockfish.uci_nodes"}
```

or:

```json
{"value": 512, "unit": "count", "semantics": "lc0.uci_nodes"}
```

LC0 currently constructs its UCI `nodes` value from playout/visit accounting, while Stockfish and Reckless report alpha-beta search-node accounting. The semantics tag is therefore mandatory.

Backend-reported search time is likewise separate from adapter `observed_ms` and remains semantically tagged.

Every numeric value anywhere in **common telemetry** must be finite, including values inside unknown/future common extension objects and arrays. JSON values that decode to NaN or positive/negative infinity are invalid even when the enclosing field is not otherwise understood by telemetry v1. Booleans remain booleans and are not treated as numeric observations.

The sole exception is the event-level `native.data` payload. That object is intentionally engine-defined and opaque to the common contract; backend-specific schemas may impose stricter rules of their own.

## Native evidence

Native payloads have `native.schema` and `native.data`, and the schema namespace must match the engine: `stockfish.*`, `reckless.*`, or `lc0.*`.

LC0's existing defect instrumentation is preserved as native evidence under `lc0.defect.iter.v1` and `lc0.defect.summary.v1`. Fields such as `leader_move_raw` and `runner_up_move_raw` remain raw internal LC0 encodings. They must not be converted into common UCI candidate moves without a separately tested mapping.

## Terminal evidence

`search.complete.bestmove = null` means only that the backend produced no move. It does not prove terminality; for example, an explicitly empty restricted-root invocation can produce no best move on a nonterminal board.

A `terminal.fact` therefore requires `fact`, `perspective`, and `source`. Terminal perspectives are `side_to_move`, `white`, or `black`. The source identifies the independent rules/tablebase basis. Multiple terminal facts may coexist when multiple independent sources support the same position.

## Controller context

Controller context is optional until the controller exists. Execution mode and controller phase are distinct:

```text
execution_mode = baseline | shadow | active

phase = EXPLORE | COMPARE | REFINE | VERIFY | RELOCK | STOP
```

`shadow` is an execution mode, not a controller phase. PR #11 shadow worker streams use `execution_mode = shadow`; the unrestricted anchor may be recorded separately as reference/authority evidence but remains outside shard ownership. Shadow telemetry does not grant decision authority.

## Raw versus derived

Raw telemetry v1 forbids aggregate `ranking`, leader/runner-up summaries, residuals, top-k overlap, rank correlation, PV-overlap metrics, and routing decisions.

```text
backend output
    -> telemetry v1 raw events
    -> replay / state reconstruction
    -> derived rankings and residuals
    -> routing / verification decisions
```

This separation is an architectural invariant, not a naming preference.

## Replay binding

Telemetry v1 remains a per-search raw event contract. PR #11 does not add run-level orchestration fields to every event. Instead, a separate replay manifest binds the synchronized position/request, engine instances, ledger snapshots, exact authorized shadow root sets, telemetry stream identities/paths, and completion/failure dispositions for one shadow experiment. This preserves raw telemetry v1 while making the multi-process experiment reconstructable.

## Versioning

Consumers must reject unsupported `schema_version` values. Optional native fields may grow under their engine-specific namespaces without changing the common contract. A breaking change to common event semantics requires a new telemetry major version.
