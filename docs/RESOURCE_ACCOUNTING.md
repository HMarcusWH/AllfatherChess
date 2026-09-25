# Measured process resource accounting

## Status

M14-B adds physical resource evidence without changing chess authority.

The existing budget protocol remains:

```text
reserve -> dispatch -> settle
```

A reservation answers whether work is allowed to start. A physical measurement
answers what already-started work actually consumed. Measurement can invalidate
a final envelope certificate; it can never authorize work that the reservation
layer denied.

Stockfish remains the sole outward `bestmove` authority.

## Vocabulary

These terms are deliberately distinct:

- **declared** — the cost written into the configuration or reservation policy;
- **reserved** — capacity currently withheld before a dispatch is allowed;
- **measured** — consumption observed from an operating-system counter;
- **accounted** — the value charged into `BudgetLedger.spent_*`;
- **estimated fallback** — a conservative value such as stage wall time times
  configured engine threads used when physical evidence is unavailable;
- **declared fallback** — the original reservation charged when neither a
  physical measurement nor a usable runtime estimate exists;
- **measurement coverage** — whether every required process/stage boundary
  produced valid evidence;
- **qualification-required** — a profile flag saying that missing physical
  evidence makes the strongest envelope claim false.

A measured value never rewrites the reservation that preceded it. Both remain
visible in the ledger.

## Linux CPU provider

The qualification provider is `linux-procfs-v1`.

It reads `/proc/<pid>/stat` and `/proc/<pid>/status` without a third-party
dependency. CPU is derived from process user+system ticks using `SC_CLK_TCK`.
The provider records `starttime` from procfs and requires it to remain
unchanged so PID reuse cannot join two unrelated processes into one fake delta.

The procfs stat parser treats field 2 (`comm`) as a parenthesized field rather
than blindly splitting the whole record. Executable names containing spaces
therefore do not shift the CPU/start-time field indexes.

For memory, the report records endpoint RSS and process-lifetime `VmHWM` when
available. `VmHWM` is explicitly **not** described as a stage-local peak.

## Controller CPU

The controller process is sampled with `time.process_time_ns()`. This counts
CPU consumed by the Python process rather than elapsed wall time, including its
threads.

The run-level process baseline is separate from per-stage engine measurements.
This matters because only summing stage deltas would omit synchronization,
positioning, telemetry/IPC gaps and other process work between named searches.

`resource.json` therefore contains:

- whole-run process CPU for each live backend;
- stage-local measurements for ANCHOR / QUALIFY / EXPLORE / VERIFY / REFINE
  and the REFINE child oracle where those stages occur;
- controller process CPU;
- endpoint memory evidence;
- CPU/GPU measurement coverage.

The physical CPU total is:

```text
sum(run-level backend process CPU)
+
controller process CPU
```

Stage CPU is retained for attribution and settlement but is not added again to
that total.

## Dispatch boundary

Each measured engine stage follows:

```text
reserve
-> process snapshot
-> dispatch
-> engine work
-> terminal process snapshot
-> measured delta
-> settle
```

If the terminal sample is unavailable, the measurement is marked incomplete.
The active ledger may use its conservative estimate/declaration fallback for
development accounting, but a profile requiring measured CPU cannot receive a
qualified physical-resource certificate.

A stopped search is settled only after its terminal `bestmove`/failure
boundary. Settling at the routing checkpoint would omit CPU consumed while the
engine handles `stop` and returns its terminal response.

## Anchor authority boundary

Taking a procfs sample is evidence work and must not delay outward authority.

The anchor start sample is taken immediately before dispatch. The terminal
sample is taken **after** the frontend has written the anchor `bestmove` to the
external UCI stream. The shadow worker, not the authority path, waits for that
sample before sealing the resource certificate.

Thus measurement may fail closed as evidence without withholding the chess
answer.

## resource.json

A measured run writes a sibling artifact:

```text
manifest.json
*.jsonl
resource.json
route.json
```

`resource.json` is atomically written and contains a content-addressed
`report_id`. Active `route.json` records the resource path, SHA-256,
report id, provider, coverage and qualification result.

The strongest active envelope claim requires:

```text
existing reservation / wall / partition gates
AND physical measurement qualification
AND measured physical CPU <= declared CPU envelope
AND (GPU not required OR measured GPU coverage complete)
```

Tampering with `resource.json` therefore breaks the hash bound by
`route.json`.

## GPU boundary

M14-B does not pretend that utilization percentage is device-time accounting.

The current CPU/BLAS reference profiles declare:

```text
require_cpu = true
require_gpu = false
```

If a future profile requires GPU measurement before a qualified provider exists,
coverage is incomplete and the strongest claim fails closed. NVML/ROCm or
another explicit device-time provider is a later extension.

## LC0 real-inference reference

The PR #24 LC0 reference now also requires `linux-procfs-v1` CPU evidence.
The qualification report binds the process/session CPU, qualification-search
CPU and memory observations alongside the already frozen source, network,
backend, runtime options, package/toolchain and host evidence.

This still does **not** establish Elo, move superiority, equal-resource
superiority, or strength-campaign eligibility.

## Claim boundary

M14-B establishes the mechanism needed to ask an equal-resource question
honestly. It does not answer the chess-strength question.

In particular it does not:

- grant a hybrid proposal outward move authority;
- prove that the current reserve fractions are optimal;
- prove a VERIFY/REFINE stage repays its measured cost;
- measure GPU device time;
- prove Allfather is stronger than Stockfish, Reckless or LC0.

Those remain later milestones.

## M14-G1 staged VERIFY resource semantics
M14-G1 adds a second, separately measured VERIFY round only in the explicit
`same_process_staged_verify_v1` research profile.

- base VERIFY and extension VERIFY receive separate specialist reservations;
- extension reservations use the existing VERIFY reserve, but a distinct
  `target_id=staged_extension` lane;
- physical measurements are separately tagged `VERIFY` and
  `VERIFY_EXTENSION`;
- the same backend process may execute both rounds, so process-total CPU includes
  both while stage measurements preserve the intervention boundary;
- a reservation for an extension that never dispatches is released rather than
  settled as spent work;
- anchor completion prevents any undispatched extension stage from beginning.

These accounting facts establish what was authorized/measured. They do not
establish that the extension was useful.

