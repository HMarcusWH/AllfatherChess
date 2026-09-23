# Global budget and active routing

## Status

Implemented as `mode: "active"`. It allocates **shadow observation compute**
inside one declared envelope. It cannot change the outward move.

## What the envelope is for

The product objective is equal-envelope superiority, not additive brute force:

```text
declared total budget B

Stockfish alone -> B
Reckless alone  -> B
LC0 alone       -> B

Allfather ->
    anchor work
  + shadow solver work
  + VERIFY / RELOCK work
  + controller overhead
  <= B
```

`controller/budget.py` makes `B` real rather than rhetorical.

## Accounting rules

**Compute is reserved before it is spent.** Reservation and the ceiling check
happen under one lock, so two workers cannot each observe "enough budget" and
both proceed past the ceiling. A test starts twenty-four threads on a barrier
against a ten-slot ceiling and asserts exactly ten grants.

**Controller overhead is charged, never free.** The routing ledger preserves
its named overhead lanes, while M14-B also samples whole-controller CPU with
`time.process_time_ns()`. The physical certificate therefore does not confuse
elapsed wall time with controller CPU and does not lose IPC/telemetry work merely
because it occurred outside one named routing context.

**Reserves are withheld and partitioned.** `verification_reserve_fraction`,
`refinement_reserve_fraction`, and `controller_overhead_reserve_ms` are
withheld from ordinary solver/anchor capacity. VERIFY and REFINE have distinct
purpose caps: solver work cannot eat either specialist reserve, VERIFY cannot
borrow REFINE capacity, and REFINE cannot borrow VERIFY capacity. The same
specialist fractions partition a declared GPU envelope.

**Engine-native counters are never summed across semantics.** The ledger keeps
`stockfish.uci_nodes`, `reckless.uci_nodes`, and `lc0.uci_nodes` separately and
exposes no scalar total. Alpha-beta nodes and LC0 visit-derived counts are not
the same quantity, and the API refuses to imply otherwise.

**Wall time is a deadline, not an additive quantity.** `wall_ms` is tracked
separately from `cpu_ms`; exhausting it triggers anchor-only fallback.

### What is charged how

| Lane | Charge |
| --- | --- |
| `anchor` | reserved before routing work starts; on the Linux qualification platform the terminal procfs CPU delta is the settlement value, otherwise the declaration is retained as a labelled fallback |
| `shadow:<owner>` | physical process CPU for the completed stage when coverage exists; wall × configured threads remains an explicitly labelled estimated fallback |
| `controller` | named routing charges plus a separate whole-controller `process_time_ns` measurement in the physical certificate |
| `verify:...` | reservation-backed VERIFY; physical process CPU is primary settlement, wall × threads is fallback only |
| `refine:...` | reservation-backed REFINE stage/oracle; physical process CPU is primary settlement |
| `controller:refine_*` | named controller-side positioning/restoration attribution; whole-controller CPU captures uncategorized runtime/IPC overhead as well |

## The routing pipeline

```text
cheap observation  ->  route proposal  ->  admissibility gate  ->  action
   (Intuition)           (nomination)          (Wisdom)
```

An instability signal may **nominate** more computation. It can never by itself
**authorize** suppression. This is the ICW/RACR separation, enforced by types:
`RouteProposal` carries no authority, `RouteAuthorization` is produced only by
the gate.

### Admissible actions

| Action | Meaning |
| --- | --- |
| `CONTINUE` | keep this worker observing |
| `HOLD` | keep the current state under observation, spend nothing new |
| `EXTEND` | dispatch another node-limited stage for this worker |
| `STOP_WORKER` | stop this observational worker and return its budget |
| `FALLBACK_ANCHOR` | stop all shadow work, leave the envelope to the anchor |
| `ABSTAIN_BUY_COMPUTE` | refuse the shortcut and buy more compute instead |

Abstention is operationally meaningful here: it *spends*, it is not a no-op.

### Gates

`STOP_WORKER` requires the conjunction of:

```text
calibration_present    AND calibration_validated
                       AND calibration_in_domain
                       AND support       >= stop_min_support
                       AND reversal_risk <= stop_max_reversal_risk
                       AND observed_work >= min_observation_nodes
                       AND observation_current
                       AND observation_drained
                       AND observation_intact
```

`observation_current` fails when the live event view has stopped tracking the
stream. A frozen prefix always looks maximally stable, so a truncated view is
exactly the state in which a stability-based stop would be most wrong.

`observation_drained` fails when the engine has already reported something this
observation did not see. Engine lines are timestamped on the stdout reader
thread and translated to disk on the writer thread, so a received line can carry
an `observed_ms` earlier than a checkpoint and still be invisible to it -- and
the JSONL would later show a leader reversal *before* a decision that never saw
it. Each stream keeps an enqueued-versus-applied watermark; the checkpoint
drains briefly first (which costs nothing when there is nothing in flight),
records the residual as `observation_backlog` on the decision, and withholds
suppression while it is non-zero.

A blocking barrier was considered and rejected: holding the routing checkpoint
until the writer thread catches up trades a live decision deadline for
bookkeeping, which is the worse failure. Failing closed on the residual is the
cheaper half of that trade. Measured over 153 contract decisions the backlog was
zero every time, so on this hardware at this event rate the guard never fires --
it is correct and, so far, untested by real data.

The watermark is reserved **before** the item is published to the writer's
queue. Incrementing it afterwards left a window in which the line was already
queued -- or already applied, making the subtraction negative and clamping to
zero -- while a checkpoint read "drained". Counting first can only over-report,
which costs a conservative denial rather than an unsound stop.

`observation_intact` fails when a stream has permanently lost evidence: a
dropped queue entry or a failed adapter translation. Unlike a backlog this never
clears, and it is invisible to the backlog gate precisely because the queue
drains afterwards. The finalized manifest records the same thing as
`contract_validatable: false`, but only once the run is over, which is far too
late for the decision that used it.

### Thresholds are validated, not just parsed

Every gate above is a comparison against a declared number, so an out-of-range
value does not misconfigure a gate -- it deletes it. `stop_max_reversal_risk: 2`
passes any risk; `stop_min_support: -1` passes any support;
`stop_min_stability_fraction: -1` passes any stability. All three are refused at
startup, along with non-finite values, a non-positive checkpoint interval, and
negative compute estimates. A policy that calls itself conservative has to be
unable to say those things.

`calibration_validated` requires the model to carry a non-empty held-out
evaluation. A model whose deterministic split left zero test rows reports
`brier_score: null` and "this model is not validated out of sample" in its own
artifact; it may be loaded and consulted, but it may not license suppression.
This is the promotion discipline made executable: in-sample confidence is not
evidence.

### Engine-native work

Node and visit counters are cumulative *within* one search and restart at zero
on the next `go`, so an owner that received extensions reports several
independent sequences. The ledger therefore keeps a maximum per stage and sums
those maxima per semantics; keeping one scalar per semantics reduced with `max`
reported a three-stage lane's largest single stage as its whole output. Each
lane publishes `native_work_by_stage` next to its totals so the arithmetic can
be checked rather than trusted.

There is still deliberately no scalar total across semantics: summing alpha-beta
nodes with LC0 visit-derived counts would assert an equivalence nothing here has
established.

### A reservation the coordinator cannot spend is returned

The router reserves an extension's compute before handing the command to the
coordinator. When the coordinator cannot run it -- the anchor completed in the
intervening race, the worker is no longer eligible, the backend refused -- the
reservation covers a stage that will never exist. Holding it denies capacity to
real work and leaves finalization to settle spend that never happened, so the
coordinator rolls it back through `release_undispatched`.

`EXTEND` / `ABSTAIN_BUY_COMPUTE` require:

```text
stage_budget    (stages_dispatched < max_stages_per_owner)
AND envelope    (a reservation of stage_cpu_ms_estimate succeeds)
AND not_in_fallback
```

A denied stop degrades to `CONTINUE` — never to an improvised action. A denied
extension degrades to `HOLD`.

### Fail-closed cases

- no calibration configured: every stop is denied, and the run records a note
  saying so;
- calibration fitted but never evaluated out of sample: every stop is denied;
- calibration declared but unloadable, foreign, or feature-mismatched:
  `build_router` refuses to construct, rather than silently degrading into an
  uncalibrated policy that still looks configured;
- bucket below its support floor, or unknown: out of domain, conservative prior;
- wall envelope exhausted: anchor-only fallback, all shadow workers stopped;
- reservation refused: the work is not dispatched.

The shipped `config/allfather.active.validation.json` declares
`"calibration": null` on purpose. A committed configuration must not assume a
fitted artifact exists.

## Audit certificate

Every decision is written to `route.json` beside the raw bundle, in the RACR
audit shape `(route, cost, residuals, certificates, thresholds, provenance,
disposition)`:

```text
observation   what was seen (past-only features)
proposal      what was nominated, and why
gates[]       each gate, whether it passed, and its detail
granted       whether authorization was given
action        what was actually done
calibration   the risk verdict, its bucket, its support, its in-domain flag
thresholds    the declared policy constants for this run
budget        available solver CPU and wall remaining at decision time
```

Run-level fields record the policy name, the envelope, the calibration model's
provenance and out-of-sample evaluation, the full budget snapshot, and the
denial list — the unresolved-stress memory.

`route.json` is deliberately **not** part of the raw manifest. A test scans the
serialized manifest for routing vocabulary and fails if any appears.

## The envelope claim is separate from the accounting

Reservations staying inside `B` is necessary but not sufficient. If the outward
request is not itself bounded by the envelope — `go infinite`, a `movetime`
larger than `wall_ms`, a GUI clock, or a node limit that bounds work but not
wall time — then the search as a whole was not bounded either, however tidy the
budget snapshot looks.

The controller must not respond by constraining the anchor: the external request
is the caller's, and narrowing it would breach the decision firewall. What it
does instead is refuse to *claim* compliance. `route.json` carries:

```text
envelope_claim.anchor_request_bounded   was the outward request within the envelope
envelope_claim.anchor_request_reason    why, in words
envelope_claim.anchor_cost_reserved     was the anchor's cost actually recorded
envelope_claim.gpu_accounted            does a declared GPU envelope have a per-stage estimate
envelope_claim.reservations_within_envelope
envelope_claim.specialist_partitions_within_caps
envelope_claim.claimed                  all of the above, wall compliance, and nothing less
```

`anchor_cost_reserved` matters because the anchor is already searching by the
time the router runs: if its reservation is refused, the cost becomes an
unrecorded obligation that no later settlement can capture. `gpu_accounted` is
false when a GPU envelope is declared with no per-stage GPU estimate, since
reserving only CPU would let a GPU-backed worker consume arbitrary accelerator
time while the ledger reported itself compliant.

The ledger's clock is seeded from the external `go`, not from the router's own
construction: legal-root qualification runs before the router exists, and a
self-started clock would hand a slow oracle a second full envelope. That
already-elapsed preparation and qualification time is charged to its own
`qualification` lane rather than left outside the accounting.

The envelope binds the FIRST shadow stage, not only extensions: preparation
and legal-root qualification can spend the wall envelope before any stage
exists, and an initial dispatch is refused once it has.

Note what the audit certificate's `claimed` field actually requires: a bounded
outward request, a reserved anchor cost, GPU accounting, reservations inside
the CPU/GPU envelope, AND wall-time compliance. `budget.within_envelope` is only the reservation/accounted-spend part. M14-B additionally requires a qualified physical resource certificate and measured physical CPU within the declared CPU envelope before the strongest `claimed` bit can be true. A report that asserts the latter and describes the
former is claiming more than it checked; the active contract now asserts every
component.

"From the external `go`" means from the start of the run's own preparation,
which is where the run clock is taken. Until round nine it was taken *after*
the replay directory was created, so pre-anchor filesystem work -- bounded by
`shadow.prepare_budget_s`, and so up to 0.25 s as shipped -- was invisible to
`wall_ms_elapsed` and uncharged. One exception remains and is deliberate: time
spent in the quiesce barrier draining the previous generation is not charged
here, because that generation's compute was already charged to its own ledger.

## Authority boundary

Routing governs shadow observation compute. The unrestricted anchor remains the
sole outward decision authority, exactly as in shadow mode. Stopping a shadow
worker returns budget; it never elects a different bestmove. The real-engine
contract asserts that the outward fixed-node move in active mode equals the
direct Stockfish move under the same configuration.

## Declared thresholds

No numeric threshold is inherited from the ICW/NSG or NeRD work. Every constant
lives in `config/*.json` under `routing`, and every decision record repeats the
thresholds it was judged against.

## Claim status

- **PROVED** by tests and contracts: the envelope is never exceeded, including
  under concurrency; controller overhead is charged; a stop is impossible
  without a calibrated, in-domain, supported, low-risk verdict; a denied stop
  continues observation; decisions are deterministic under fixed evidence; the
  outward move is unchanged.
- **POLICY**: the `conservative_v1` rules and every threshold value.
- **OPEN**: whether this routing improves chess strength at equal declared
  resources. Nothing in this milestone tests that.


## Active specialist work

PR #18 closes the previous accounting gap. `mode: active` may enable VERIFY
only with a positive `verification_reserve_fraction`, and may enable REFINE
only with VERIFY enabled plus a positive `refinement_reserve_fraction`.

Every specialist computation follows:

```text
nominate → authorize → reserve → dispatch → settle
```

The router records these operations in `route.json.specialist_actions`.
REFINE's Stockfish perft-1 child oracle is charged from the REFINE partition;
descendant engine stages require their own REFINE reservations; per-instance
positioning/restoration is charged as controller overhead. A denied reservation
means the work is not dispatched.

Actual CPU settlement is intentionally unclamped. If a stage exceeds its
estimate, the full wall×threads estimate is recorded. This can make
`specialist_partitions_within_caps=false` even when the global envelope still
has spare capacity, and such a run cannot claim envelope compliance.

See `docs/ACTIVE_SPECIALIST_SCHEDULER.md`.
