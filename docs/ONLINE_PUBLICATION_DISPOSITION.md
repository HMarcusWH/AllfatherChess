# Post-publication clock-work disposition (controller lifecycle repair)

**Scope:** Governed UCI execution/replay classification only. No move-authority,
resource-envelope, engine/profile or live transport changes.

The ONLINE clock's work window may close while the sealed controller still
finalizes VERIFY, resource endpoints or delayed anchor observations. A physical
bestmove already emitted to stdout is irrevocable *as a publication event*;
subsequent optional-work stoppage must not turn it into a retroactive cancellation.

`controller/shadow.py` retains `_ActiveRun.cancelled` for conservative
dispatch/work gating. The replay finalizer separately labels a non-explicit
clock cancellation after a confirmed outward publication as
`published_incomplete` with reason
`optional_clock_work_closed_after_publication`. This label deliberately
does **not** mean a fully completed calculation or a positive resource,
hybrid-authority, calibration, Elo, release or deployment qualification.

An explicit pre-publication stop remains `cancelled`. Worker errors remain
`error`, and a clocked run that genuinely completes remains `completed`.
Incomplete stages, missing resource evidence, route denials and physical
worker-quiescence failures remain independently visible and fail closed.

The executable tests cover these distinct states and repeat the real
fake-subprocess post-bestmove/quiesce barrier. No claim is made until the
exact-head controller, G3 and LOCAL-1 suites independently qualify.

## Late optional VERIFY admission and immutable resource evidence (24 October 2026)

The exact-head LOCAL-1 failure on `e45ed7a6d8d1ad5c8e86f89c8fdbf99684974874`
retained two late LC0 VERIFY observations: `life-history-reuse`
started VERIFY with 3.923ms remaining before the soft cutoff, and
`base-00-06` with 5.243ms. Both later exited with rc=-9 and
retained incomplete physical CPU coverage, while their outward moves
were published before the hard deadline. The original failure remains
**negative evidence**, not a completed VERIFY or valid resource report.

The remediation gates a new *optional* VERIFY phase on the existing
`online_time.quiesce_budget_ms` before the immutable soft deadline.
It does not add a new clock policy field, extend a deadline, or change
the 16-node VERIFY dispatch limit. The guard is repeated before opening
streams, before specialist/WorkGrant reservation, after phase setup
and at the final generation-scoped backend stdin permit.
Undispatched provisional resource stages must be discarded, and
reservations released; partially dispatched three-way VERIFY is
`incomplete`, never `completed`. A fully withheld optional VERIFY
does not independently authorize G3 hybrid selection.

The frozen TimePlan stop/output margins remain reserved. A still-running
or SIGKILL-terminated LC0 stage must never be promoted using terminal
zombie CPU or unrelated process totals. `published_incomplete` remains
a non-cancelling record of timely outward publication and **nonqualifying**
full resource evidence.

This is a source candidate until deterministic regressions and a fresh
physical same-clock LOCAL-1 campaign pass at the new exact head.
