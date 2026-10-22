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
