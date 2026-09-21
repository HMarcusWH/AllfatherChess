# Replay bundle format v1

## Status

Implemented. `controller/replay.py` writes it; `controller/replay_analysis.py`
reads it. Validated by `tests/controller/test_replay.py` and by the real-engine
contract `scripts/shadow-execution-contract.py`.

## What a bundle is for

Telemetry answers **what each engine observed**.
A replay bundle answers **what experiment was executed**.

That separation is an architectural invariant, not a naming preference:

```text
backend output
    -> raw telemetry v1        (per-search event stream)
    -> replay bundle           (run-level orchestration evidence)
    -> derived residual features
    -> calibrated risk model
    -> routing policy
```

A replay manifest therefore contains **no** residual, ranking, overlap metric,
leader synthesis, or routing decision. Active-mode routing evidence is written
to a sibling `route.json`, never into `manifest.json`. A test asserts this by
scanning the serialized manifest for derived and policy vocabulary.

## Layout

```text
<replay_root>/<run_id>/
    manifest.json           orchestration evidence
    stockfish-anchor.jsonl  telemetry v1 stream, authority search
    stockfish-shadow.jsonl  telemetry v1 stream, restricted worker
    reckless-shadow.jsonl
    lc0-shadow.jsonl
    route.json              active mode only: routing audit certificates
```

`<run_id>` is `<utc timestamp>-g<generation>-<random suffix>`. It is unique per
external search and appears in every `search_id` in the bundle.

A stream file is created **only when a stage is actually dispatched**, so a run
cancelled before dispatch leaves no empty artifact pretending to be evidence.

## Manifest fields

| Field | Meaning |
| --- | --- |
| `schema_version` | replay manifest version (`1`) |
| `run_id`, `generation`, `created_utc` | run identity and the external search generation it belongs to |
| `controller.mode` | `shadow` or `active` |
| `controller.telemetry_execution_mode` | the value shadow streams carry |
| `controller.config_path`, `controller.config_sha256` | exact runtime configuration |
| `controller.partition_method` | how roots were allocated (`root_index_modulo`) |
| `controller.overhead.prepare_ms` | controller work performed **before** the anchor was dispatched, measured from the start of the run's own preparation |
| `controller.overhead.qualification_ms` | controller work before the first shadow dispatch, measured from the same origin — it therefore **contains** `prepare_ms` rather than sitting beside it |
| `position.*` | variant, move encoding, base FEN, move list, position id, reconstructed command |
| `external_request` | the external `go` command and its parsed limits |
| `engines.<instance>` | solver family, process role, binary path, binary sha256, args, options |
| `legal_root_oracle` | oracle instance, root count, dispatched root count, external `searchmoves` restriction, terminal-universe flag |
| `ledger.owners`, `ledger.owner_roots` | authorized owners and their exact regions |
| `ledger.pre_dispatch_snapshot` | ledger state at dispatch time |
| `ledger.post_run_snapshot` | ledger state after sealing |
| `stages[]` | one entry per dispatched search, see below |
| `streams[]` | one entry per telemetry file, see below |
| `shadow_health` | per-shadow-instance liveness and failure text |
| `disposition.run`, `disposition.stop_reason` | how the run ended |
| `notes[]` | explicit controller observations, such as an excluded owner |

### Stage record

A stage is one dispatched search on one instance. It is an **orchestration
fact**. Why it was dispatched is policy and lives in `route.json`.

```text
stage_index        0 for the first dispatch on that instance
instance, engine   process-role identity and solver-family identity
role               anchor | shadow
owner              ledger owner (null for the anchor)
search_id          matches the telemetry stream
command, request   the exact go command and its parsed form
dispatched_roots   exactly ledger.active_roots(owner); empty for the anchor
dispatch_order     global dispatch sequence within the run
completion_order   global completion sequence within the run
dispatched_ms      milliseconds after run start
completed_ms       milliseconds after run start
disposition        completed | stopped | failed | unresolved | running
stop_reason        why it stopped, when it did not complete naturally
bestmove           the instance's own move; for shadows this is evidence only
failure            failure text when the instance died
```

### Stream record

```text
instance, engine, role, path
search_ids[]         one per stage written into this file
event_count, bytes, sha256
complete             did the stream reach search.complete
contract_validatable complete AND no adapter errors AND no dropped events
dropped_events       telemetry lost to queue overflow, recorded not hidden
post_complete_lines  engine output observed after search.complete
queued_peak          high-water mark of the capture queue
live_view_truncated  the in-memory view used by active routing stopped growing
adapter_errors[]     bounded list of translation failures
```

A stream without `search.complete` is real evidence but is **not** a valid
telemetry v1 stream. It is kept and marked `contract_validatable: false` rather
than being given a fabricated completion event.

## Time base

`observed_ms` inside a stream is controller-side observation time measured from
**run start**, not engine-reported time and not wall-clock. Because every stream
in a run shares that origin, `observed_ms` is the one quantity that is
comparable across instances. Engine-native work counters are not.

## Integrity

`verify_bundle_integrity(run_dir)` re-hashes every declared stream and reports
missing files, hash mismatches, and size mismatches. A tampered stream is
detected by the test suite.

## Analysis contract

`controller/replay_analysis.load_bundle()` reconstructs `SearchTrajectory`
objects from the streams. It records rather than repairs:

- a missing stream appears in `missing_streams`, never as imputed data;
- a malformed JSON line appears in `load_errors`;
- an unparseable engine line stays in its `native.event` payload and produces
  no candidate observation;
- a checkpoint before the first observation has no leader, so every label at
  that checkpoint is `None` rather than assumed.


## VERIFY child artifact

Replay schema v1 remains the EXPLORE orchestration contract. Deliberate overlap
does not get inserted into its ledger-owned stage list.

When explicit VERIFY runs, the replay directory gains:

```text
verification/
    manifest.json
    stockfish-shadow.jsonl
    reckless-shadow.jsonl
    lc0-shadow.jsonl
```

The child manifest records the deterministic common candidate set, participant
instances, exact commands, stage dispositions and stream hashes, and binds the
finalized parent `manifest.json` by SHA-256. It intentionally contains no
COMPARE/RELOCK conclusion. See `docs/VERIFY_RELOCK.md`.


## COMPARE / RELOCK derived artifact

Raw parent and VERIFY artifacts remain immutable. Offline analysis writes beside,
never inside, the replay tree:

```text
build/verification-derived/
    verification-derived-<content hash>/
        analysis.json
```

The content address covers extractor/RELOCK-definition versions, checkpoint
parameters, the parent manifest and stream hashes, and the VERIFY manifest and
stream hashes. The derived artifact contains pairwise common-support metrics,
three-way convergence descriptors, EXPLORE→VERIFY transitions, descriptive
RELOCK, and anchor relation. It is not a replay schema extension and is not
consumed by the live controller. See `docs/COMPARE_RELOCK.md`.
