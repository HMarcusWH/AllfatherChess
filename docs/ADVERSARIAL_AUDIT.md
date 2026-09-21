# Adversarial audit

Written before finalizing the immediate controller stack. Each question is
answered against the code and the tests, not against intent.

## Did we accidentally give Allfather more resource budget than the benchmark?

**Yes in shadow mode, deliberately and declared.** Shadow mode runs an
unrestricted anchor plus three restricted workers concurrently. That is research
overhead and is documented as such in `docs/SHADOW_EXECUTION.md`, in the sweep
tool's output, and in the contract's `not_claimed` list.

**In active mode the envelope binds shadow work**, and the anchor is charged its
declared reservation, so the ledger cannot report "within envelope" while
ignoring the anchor. But this is accounting, not validation: no equal-resource
benchmark match has been run. That remains OPEN.

## Did shadow output gain decision authority?

No. Shadow searches use a no-op `on_info` and an internal `on_complete`; nothing
in their path writes to stdout. `test_shadow_bestmove_never_leaks_outward`
scripts every shadow to prefer `g2g3` while the anchor prefers `e2e4` and
asserts the outward line is `bestmove e2e4`. The real-engine contracts assert
the outward fixed-node move equals direct Stockfish's.

## Did we conflate anchor and shadow Stockfish?

No. They are separate processes with separate `engine_instance` values, separate
roles, separate health semantics, and separate telemetry streams, while sharing
the `stockfish` solver family for score/work semantics. The config loader
rejects a second anchor instance and rejects an owner whose instance has the
wrong family.

## Did we use anchor perft in a way that delays outward search?

No, and this was a real finding. In shadow/active mode the oracle is
`stockfish-shadow`; naming the anchor as oracle is a configuration error. Beyond
that, the audit found a genuine defect: engine binaries were sha256-hashed on
*every* run inside `prepare_run`, which runs synchronously before the anchor is
dispatched, delaying the outward search by ~700 ms on this checkout. Binaries
are now hashed once at construction. `prepare_ms` and `qualification_ms` are
recorded in every manifest so the cost cannot hide again.

## Did any shadow bestmove leak to UCI?

No. See above. Additionally the frontend's info/complete callbacks are gated on
the active generation, so even a late anchor callback from a superseded search
cannot print.

## Can an old shadow generation continue after a new position arrives?

No. `position`, `ucinewgame`, and `setoption UCI_Chess960` all pass through
`_shadow_quiesce()`, which cancels the run, stops every dispatched worker, and
joins the worker thread before any state is synchronized. Every callback also
checks the generation token.
`test_new_position_cannot_be_observed_by_a_stale_generation` asserts each run's
evidence belongs to exactly one synchronized position.

## Can a shadow crash kill a healthy anchor search unnecessarily?

No. `BackendManager._handle_exit` routes a shadow-role exit to observational
health and returns; only anchor/managed roles escalate to `_notify_failure`.
Authority health excludes shadow instances entirely. Synchronization skips dead
shadows instead of failing.

## Can a shadow crash silently promote another engine?

No. A crash records a failure, marks the stage `failed`, and sets that owner's
completion event. No promotion path exists: the outward bestmove comes from the
anchor's `on_complete` only.

## Did we accidentally compare LC0 work directly with alpha-beta nodes?

No. Telemetry keeps `lc0.uci_nodes` at unit `count` and the alpha-beta counters
at unit `nodes`. The derived layer carries `work_semantics` alongside every work
value. The budget ledger stores native work per semantics and deliberately
exposes no scalar total. A test asserts the unit sets differ.

## Did we subtract Stockfish cp from Reckless cp without calibration?

No — it is impossible to do accidentally. `within_engine_margin` calls
`require_same_semantics`, which raises `ScaleMixingError` on mixed tags, and
`unresolved_set` refuses mixed-semantics margins. Two tests assert the refusal,
including LC0 scalar against alpha-beta cp.

## Did we place residuals in raw telemetry?

No. Raw streams are produced by the unchanged PR #7 adapters, and the unchanged
telemetry v1 contract already forbids the derived vocabulary. A test greps every
generated stream for residual/overlap/routing terms.

## Did we place routing decisions in replay raw evidence?

No. Route decisions go to `route.json`. `test_route_evidence_lives_beside_but_outside_the_raw_bundle`
and the active-routing contract both scan the serialized manifest for policy
vocabulary and fail if any appears. Stage records hold *what* was dispatched;
`route.json` holds *why*.

## Did we turn the observation partition into hidden routing policy?

No. `partition_roots` is `root_index % owner_count` and takes only the root
sequence and the owner list. A test asserts identical output for identical
input and pins the exact expected buckets. Active-mode routing changes *how much
compute* an owner receives, never *which roots* it owns.

## Can budget accounting exceed B through concurrency?

No. Reservation and the ceiling check occur under one lock in
`BudgetLedger.reserve`. The concurrency test grants exactly the number of slots
the ceiling permits and asserts the envelope holds.

## Does controller overhead disappear from accounting?

No. `controller_overhead()` charges measured time to the `controller` lane
inside `B`; `authorize_initial` and every checkpoint are wrapped. The manifest
separately records `prepare_ms` and `qualification_ms`. A test asserts the
controller lane is non-zero after a real run.

**Known gap:** the telemetry capture queue-put that runs on each engine's reader
thread, and the Python-level cost of writing anchor info lines to stdout, are
not individually charged to the controller lane. They are microsecond-scale per
line but they are real, and they are not currently measured.

## Can stop/quit leave orphan processes?

No. `quit` closes the coordinator (cancel plus join) before closing processes,
and `UciProcess.close` escalates to `kill` after a bounded wait. Both the fast
test (`pgrep` on a unique tag) and both real-engine contracts (`pgrep` on the
engine binaries) assert no survivors.

## Can callbacks race after completion?

Guarded, not merely hoped. `record_completion` no-ops once a stage has left
`running`; `_on_shadow_complete` rejects mismatched generations; the stream
writer counts post-complete lines instead of feeding them to a completed
adapter; and the reader thread never holds a process lock while invoking a
callback, so there is no lock-order inversion between the runtime lock and the
coordinator lock.

## Did we weaken any existing PR #1–#11 invariant?

No. `engines/**`, `vendor.lock.json`, `tests/baseline/golden/**`,
`schemas/telemetry/v1.contract.json`, and `config/allfather.validation.json` are
byte-identical to `main`. The legacy anchor profile still loads as before, the
pre-existing controller tests pass unchanged, the frozen golden gate passes, and
no CI step was removed or relaxed.

## Did docs claim more than tests prove?

Each doc carries a claim-status section, and `docs/CLAIM_LEDGER.md` separates
PROVED / MEASURED / DERIVED / CALIBRATED / POLICY / OPEN explicitly. The known
risk is drift: a future change to a policy default would need the ledger updated
with it.

## Did we convert "stable in our replay" into "correct"?

No. Every label is named for what it observes: `later_leader_changed`,
`stable_to_end`, `reversal_within_horizon`. The calibration artifact carries a
`claim` string saying it estimates an engine changing its own mind and licenses
no strength claim. `docs/RESIDUAL_CALIBRATION.md` states the same.

## Did we make an Elo claim without an Elo experiment?

No. No Elo experiment was run and no strength claim appears anywhere. Both
real-engine contracts write an explicit `not_claimed` list into their reports.

## A gap this audit found late

The first version of the active-routing contract fitted its calibration from
only four runs. The deterministic by-run split put all of them in the training
set, so the model reported `test_rows: 0` and `brier_score: null` — its own
artifact said it was not validated out of sample — and the router used it
anyway to authorize stops.

Two fixes, both kept:

1. `calibration_validated` is now a **gate**. A model with no held-out
   evaluation may be loaded and consulted but may never license suppression.
   In-sample confidence is not evidence.
2. The contract now collects ten distinct evidence runs and fails outright if
   the fitted model has no held-out rows.

This is recorded rather than quietly fixed because it is the exact failure mode
the source material warns about: promoting an exploratory result to a serving
claim without frozen criteria on untouched tests.

A third fix followed from the second: the split itself hashed each run id
independently, which makes the *size* of the holdout a random variable. At the
contract's ten runs it would leave nothing held out about 5.6% of the time, so
the new gate would have blocked routing at random. The split now strides over
sorted run ids, which is deterministic and guarantees a non-empty holdout from
two runs upward. A gate is only as good as the determinism of the evidence it
reads.

## Fourteen findings from code review

An automated review found fourteen defects after the first push. All fourteen
were verified against the code and fixed; none was waved off. The ones that
mattered most were not the crashes but the quiet ones:

1. **Train/serve feature skew.** Training computed `elapsed_fraction` against
   the observed replay span and stability over ten sampled checkpoints; serving
   computed them against the declared wall envelope and over every raw update.
   The buckets were not comparable, so "in-domain, low-risk" did not mean what
   it claimed. There is now one shared `past_only_features` function over one
   input, and the feature that could not be computed identically in both paths
   was removed rather than patched.
2. **Right-censored labels.** A horizon running past the end of a trajectory was
   labelled "no reversal". Every completed search therefore donated guaranteed
   negatives to exactly the settled buckets that authorize suppression. Fixing
   it cut the sweep from 1200 rows to 360 and raised the measured base rate from
   0.028 to 0.094 — the previous model understated reversal risk more than
   threefold. (These are per-sweep counts recomputed from the derived artifact
   in this tree, not fixed constants; re-running the sweep moves them.)
3. **A dispatch race.** Checking "has the anchor finished?" at the top of the
   dispatch path left a real window: stream creation is not free, and a stage
   could still be launched against a decision already emitted. The check and the
   dispatch are now committed under one lock.
4. **A stuck worker was abandoned rather than handled.** A worker ignoring
   `stop` left the caller free to synchronize state into a process still running
   the previous generation, and its run never finalized. It is now recorded as a
   shadow failure, excluded from synchronization, and its bundle released.
5. **Budget under-reporting.** A stop released the worker's whole reservation,
   recording none of the CPU it had just spent producing the observations that
   authorized the stop. Only the unspent remainder is released now.
6. **Envelope claims without a bounded request.** `go infinite` produced a tidy
   in-envelope budget snapshot for an unbounded search. The controller still
   does not constrain the anchor — that would breach the decision firewall — but
   it now refuses to *claim* compliance and records why.
7. **Colliding content addresses.** Neither the derived id nor the model id
   covered all of their inputs, so different artifacts could overwrite each
   other. Both are now addressed over everything that determines them, and
   feature extraction re-verifies bundle hashes before trusting provenance.
8. **Multi-stage workers overwrote themselves** in the derived layer, and two
   stages of one instance were compared as if they were different engines.

Two further items — an external `searchmoves` restriction being ignored by
shadow qualification, and a live event view that froze silently at its cap —
were also real and are fixed.

The uncomfortable part is that several of these were in code this audit had
already reviewed once and passed. An adversarial checklist written by the same
author who wrote the code will miss what that author did not think to doubt.

## Fourteen more findings from a second review

A second automated review of the pushed branch found fourteen further defects.
All fourteen were verified against the code and fixed. Grouped by what they
would actually have cost:

**The calibration was trained on the wrong population.** Buckets pooled the
unrestricted anchor with every shadow family, so a bucket could reach its
support floor on anchor evidence and then license stopping an LC0 worker that
had almost none of its own. Buckets are now scoped by solver family and anchor
rows never train at all (`bucketed_reversal_risk_v3`; v2 artifacts are refused
at load rather than reinterpreted). This is the finding that mattered most, and
it changed the conclusions — see the entry below.

**The envelope could be claimed when it had not been respected.** Four separate
ways: the ledger clock started after legal-root qualification, so a slow oracle
got a free extra envelope; the qualification CPU was never charged to anything;
a failed anchor reservation was noted and then ignored, leaving the run free to
report compliance while carrying an unrecorded obligation; and a declared GPU
envelope was never reserved or settled against, so a GPU-backed worker could
consume arbitrary accelerator time inside a ledger that called itself compliant.
The ledger is now seeded from the external `go`, qualification is charged to its
own lane, and both `anchor_cost_reserved` and `gpu_accounted` are conjuncts of
the `claimed` flag rather than decoration beside it.

**Spend was silently rounded down.** Settlement clamped measured consumption to
the reservation, so a stage that outran its estimate freed capacity it had
already spent. It now settles unclamped.

**A stored model was trusted rather than validated.** A calibration file could
declare a negative risk, a fabricated support count, or more positives than
observations, and every one of those passes each suppression gate unchallenged.
Buckets and the prior are validated at load.

**Two failure paths were wired wrong.** `RoutingError` and `BudgetError` subclass
the builtin `RuntimeError`, not the controller's, so an invalid policy escaped
the startup handler with a traceback *after* the engine processes had started,
leaking them. And `lc0_score_type` was unvalidated until the telemetry writer
thread reached it, which surfaced as an adapter error after the LC0 search had
already been dispatched — turning a typo into a run with no LC0 evidence rather
than a refusal at startup.

**Three accounting and eligibility gaps.** An infinite or NaN envelope was
accepted, which disables the ceiling it describes while the run still reports
itself compliant. The router's checkpoint loop visited owners with no dispatch
state, so it could reserve an extension the coordinator would silently drop.
`record_native_work` was never called in production, so the audit format
promised engine-native counters that a real run did not carry. Streams the
manifest marks `contract_validatable: false` were still feeding training rows,
even though a dropped leader flip reads as stability — the one direction that
authorizes suppression.

**One provenance bug.** Fitting from an existing artifact recorded the CLI's
default horizon rather than the artifact's, so the model id and provenance could
describe a horizon its own labels never used. The horizon is now read from the
artifact, and an explicit mismatch is rejected instead of silently recorded.

### What the calibration fix cost, and why that cost was not paid down

Scoping the buckets shrank per-bucket support roughly threefold, which was
anticipated. What was not anticipated is that it also **inverted the fitted
direction**. Of the two buckets left with enough support to serve a decision,
the never-flipped one carries the *higher* measured reversal risk (0.265 at
support 32) and the just-flipped one the lower (0.033 at support 28) — the
opposite of what the routing gate assumes, and the opposite of what the pooled
v2 fit reported.

The consequence is that authorized stops went 4 → 1 → 0 across the three
corrections, and zero is now structural rather than marginal: each servable
bucket fails exactly one of the two remaining gates, so the conjunction cannot
be satisfied by any well-supported bucket at all.

The support floor was not lowered and neither threshold was moved. Tuning either
one would have manufactured stops out of a model whose own held-out evidence
does not support them, which is the specific failure this audit exists to catch.
Whether the inverted ordering is real or an artifact of when each bucket is
populated is recorded as open in `docs/CLAIM_LEDGER.md`.

## Eight more findings from a third review

A third review of the pushed branch found eight further defects. All eight were
reproduced against the code before anything was changed, and each carries a test
that fails without its fix.

**Two of them let the generation barrier be crossed.** The stuck-worker sweep
iterates `active.owners`, but owner states are created only *after* legal-root
qualification returns -- so a state mutation arriving while the oracle was still
answering found nothing to fail, and the caller went on to send `position` to a
process still running the previous generation's `go perft 1`. And the oracle
itself was only required to be Stockfish-family and not the anchor. A `managed`
instance satisfies both, but `managed` is authority-critical: its timeout takes
the authority failure path and can fail the outward search, and
`record_shadow_failure` would not have excluded it from synchronization anyway.
The oracle must now have role `shadow`, which is also what makes the first fix
work.

**One discarded the outward decision.** Finalization waited for the anchor's own
completion bounded by `drain_timeout_s` -- the *shadow* drain timeout. Shadow
stages are node-limited and finish in well under a second; a `go movetime 60000`
anchor does not. The wait expired, the authority stream closed and the run was
cleared while the outward search was still going, so the anchor's `bestmove` had
nowhere to land and a normally completed search was recorded with an unresolved
authority stage and an invalid authority stream. The wait now lasts as long as
the anchor can still answer -- it is alive and the coordinator is open -- with
both polled rather than assumed, so a dead anchor or a closing controller
releases the worker instead of hanging it.

**One let the online decision and the offline audit disagree.** Engine lines are
timestamped on the stdout reader thread and translated on the writer thread, so
a line can be received, carry an `observed_ms` earlier than a routing
checkpoint, and still be invisible to that checkpoint. The JSONL would later
show a leader reversal *before* a decision that never saw it. The stream now
keeps an enqueued-versus-applied watermark; the router drains briefly, records
`observation_backlog` on the decision, and refuses to suppress while anything is
in flight. Measured over 153 contract decisions the backlog was zero every time,
so this is a real fail-closed guard rather than an always-deny in disguise. The
`owner_events` docstring, which claimed online and offline could not disagree,
was wrong and has been corrected rather than left standing.

**One deleted the conservative policy from a config file.** The suppression
thresholds were converted to numbers and never range-checked, so
`stop_max_reversal_risk: 2`, `stop_min_support: -1` or
`stop_min_stability_fraction: -1` did not misconfigure the corresponding gate --
each made it vacuously true. Every threshold is now range-checked at startup.

**One skipped the audit for a whole class of runs.** Every qualification exit --
a terminal position, a dead oracle, an external `searchmoves` that leaves no
shadow root -- returned before the router's run was opened. The anchor had
already been dispatched and already spent its compute, but no ledger existed, no
anchor cost was charged, `on_run_end` never ran and no `route.json` was written.
The lifecycle is now idempotent and runs for every active search.

**One understated the work it reported.** Engine node and visit counters restart
at zero on each `go`, and the ledger kept one scalar per semantics reduced with
`max`, so a lane that ran three stages reported its largest single stage as its
whole output. Counters are now maxed within a stage and summed across them, and
`route.json` carries the per-stage decomposition so the arithmetic can be
checked rather than trusted. On one contract run this took a Stockfish lane from
200,074 to 216,075 node-equivalents; the two 8,000-node extensions had been
entirely invisible.

**One selected training data silently.** In fit-only mode `--derived-id` was
ignored and the artifact was chosen by taking the lexicographically greatest
content-hash directory -- neither the newest nor the one that was asked for. An
explicit id is now resolved or the run fails, and with no id the most recent
artifact is used *and named*.

## Twelve more findings from a fourth review

A fourth review found twelve further defects. **Two of them were holes in the
round-three fix**, which is worth stating plainly rather than filing under
"more findings".

**The observational layer could take down the outward search.** The telemetry
observer ran before the authoritative callback, so an exception in it skipped
`on_complete` entirely. For the anchor's `bestmove` that left the frontend in
SEARCHING forever: the engine simply never answered. This is the exact failure
the authority/observation split exists to prevent, and it was reachable from any
bug in the replay writer. The observer is now wrapped, the authority callback
always runs, and the failure is recorded as evidence. Recording it is itself
guarded, because `record_shadow_failure` raises for the anchor -- which would
have reintroduced the same bug one layer down.

**The round-three backlog guard had a race, and a blind spot.** The watermark
was incremented *after* `put_nowait` published the item, so a checkpoint in that
window read `pending_events() == 0` with a line already queued -- and if the
writer had already applied it, the subtraction went negative and clamped to
zero. The counter is now reserved before publication and rolled back on a
rejected enqueue, so it can only over-report, which costs a conservative denial
rather than an unsound stop. Separately, the live gates saw only the 50,000-event
tracking cap: a dropped queue entry or a failed adapter translation drained away,
after which backlog was zero and both gates passed. Round two had already
established that a dropped leader flip reads as stability -- that reasoning had
been applied offline only. There is now an `observation_intact` gate, and unlike
a backlog it never clears.

**A trajectory ended at its last `info` line, not at its completion.** `span_ms`
discarded the `search.complete` timestamp, so every horizon was measured against
a short span and a completed search that emitted no candidate update at all was
reported as zero-length. Two such searches exist in this repository's own sweep.
Relatedly, `search.started` was always stamped `observed_ms=0`, so an extension
dispatched seconds into a run claimed it began at run start while its own
candidate and completion events carried the real clock.

**Per-checkpoint views used the run's final stage.** `shadows()` selected the
last stage per instance once for the whole run and reused it at every
checkpoint, so at a checkpoint before an extension was dispatched the selected
stage had no observations and the worker looked silent at a time when it had in
fact reported a leader. There is now `shadows_at(t)`, and the whole-run view is
defined in terms of it.

**The served model was not the model that was measured.** Bucket risks were
rounded to six decimals on write while the evaluation was computed from the
unrounded values, and the difference is in the permissive direction: 1/21
serializes as `0.047619`, which passes a threshold of `0.047619` that the fitted
`0.0476190476...` denies. Full precision is now persisted.

**Support could be inflated by copying.** A bundle copied under a second
directory name carries the same `run_id` and contributed identical checkpoint
rows, so a bucket could cross `stop_min_support` on one run's evidence.
Duplicate run ids are refused before fitting.

**Three lifecycle and config gaps.** An extension the coordinator could not
dispatch left the router's reservation open, denying capacity to real work and
leaving finalization to settle spend that never happened. `drain_timeout_s * 4`
was used as a hard cap on every normally progressing shadow stage, turning a
stop-wait bound into an undocumented runtime limit; stages now have their own
declared `stage_timeout_s`. And configuration and binary hashes were captured
when the shadow coordinator was constructed -- after every process had already
started -- so they are now taken at config load, before anything is launched.
A config that enables `UCI_Chess960` as a startup option is refused outright
rather than leaving the engines searching FRC while every replay recorded
`variant: standard`.

### What the span fix cost, and why that cost was not paid down either

The correction is small in magnitude on this evidence -- a median span extension
of 0.1 ms -- but it moved the numbers, and not in the flattering direction. The
36-run sweep now yields 350 labelled rows and 140 training rows (was 360 and
150), and critically `lc0|n3|s0|f1` fell from support 28 to **24**, dropping
below `stop_min_support = 25`. Exactly one bucket is now servable, the
held-out in-domain rate is **0.00**, and the fitter prints that the model is
out-of-domain everywhere.

The inverted direction survived unchanged: never-flipped ~0.22 against
just-flipped ~0.03. So the round-two conclusion stands, on slightly worse
evidence.

The support floor was not lowered. A model that is out-of-domain on its entire
held-out split is a model that should authorize nothing, and making it authorize
something by moving the number it is measured against would be the precise
failure this audit exists to catch.

## Nine more findings from a fifth review, four of them mine

A fifth review found eight defects; checking one of them turned up a ninth that
the review had not seen and that was entirely my own. **Four of the eight trace
directly to round-four work**, which is the honest summary of this round.

**The envelope claim ignored the clock.** `claimed` conjoined the anchor bound,
the anchor reservation, GPU accounting and CPU/GPU ceilings -- and never asked
whether the run had taken longer than `wall_ms`. A slow legal-root oracle alone
can carry a run past its declared wall envelope with every reservation still
inside its ceiling. The elapsed time is now both recorded and a conjunct.

**CPU spend was wall-clock time.** A shadow engine configured with `Threads: 4`
running for 400 ms consumed roughly 1600 CPU-ms and was charged 400, so the
ledger could report compliance after the processes had already exceeded
`cpu_ms`. Wall duration is now scaled by the declared thread count, and the
certificate says `cpu_measurement: stage_wall_ms_x_configured_threads` -- this
is an estimate from a configured option, not a measurement of process CPU, and
it is labelled as one rather than implied to be more.

**The round-four stage timeout was neither per stage nor a quarantine.** I
introduced `stage_timeout_s` last round, documented it as a cap on one stage,
and then computed a single deadline for the entire wait -- so an extension
dispatched late inherited whatever milliseconds the initial stage had left and
was cancelled before it had run. Worse, a worker that overran and then ignored
`stop` was waited on for one drain timeout and simply abandoned: nothing marked
it failed, so the run finalized while `shadow_available()` still called the
process healthy and the next `position` went to an engine still executing the
previous generation. That is the same defect round three fixed in `quiesce()`,
reintroduced on a path I added. The deadline now restarts per stage, and the
overrun path quarantines exactly as `quiesce()` does.

**The round-four reservation rollback was half done.** I made the extension path
return an undispatched stage's compute and left the initial-dispatch path
ignoring `_dispatch_stage`'s return value entirely, so a stage the anchor raced
still leaked its reservation.

**The horizon was a fraction of an absolute timestamp.** `span_ms` is where a
stage ended on the run clock, not how long it ran, so for an extension running
800 to 1000 ms a 25% horizon asked for 250 ms instead of 50 ms and censored most
later-stage labels. This only became wrong when round four gave later stages
their real start times -- a fix creating the conditions for the next defect.

**Two smaller ones.** `1e309` parses to infinity and passed the positive-number
check for `oracle_timeout_s`, `drain_timeout_s` and `stage_timeout_s`; infinity
reaches `Event.wait()` and `Thread.join()`, which raise `OverflowError`, and the
quiesce path catches that and proceeds without excluding the running worker.
Round two fixed exactly this class for the budget envelope and I did not
generalise it. And `close()` joined the writer thread under a bound and then
closed the file whether or not the thread had exited, so remaining events failed
against a closed handle while `snapshot()` described the stream as whole; it now
waits again and records what was stranded as lost evidence.

### The ninth: derived ids collided across extraction changes

Two derived artifacts with *different* `counterfactual_labels` shared one
`derived_id`. The digest covers sources and extraction parameters but not the
extractor's output, so `EXTRACTOR_VERSION` is the only thing standing in for the
extraction logic itself -- and rounds four and five changed that logic three
times without touching it. A re-derive therefore overwrote an artifact whose
labels differed, which is precisely the collision the content address exists to
prevent and precisely what round one's finding was supposed to have fixed.

I found this by being suspicious of a 0.0006 difference in Brier score between
two fits that should have been identical. It is bumped to `residuals-v3`, which
also invalidates models fitted against the older extraction.

### Re-measured, and the comparison I first drew was wrong

My first attempt to attribute this round's effect compared a sweep against a
figure from a different invocation and concluded the run mix had regressed
(24 completed to 11). Re-running the pre-round-five code on the same command
gave 12 completed, so the disposition mix is run-to-run variance in the
anchor-versus-shadow race, not a regression. Deriving both code versions from
*identical* bundles gives identical row counts, because the horizon fix only
bites for extensions and the shadow-mode sweep dispatches none.

Current measurement: 178 training rows, 10 buckets, 133/45 split, Brier 0.026,
in-domain 0.29. Two buckets clear the support floor, and the inverted direction
holds: never-flipped `lc0|n3|s3|f0` at 0.220 against just-flipped
`lc0|n3|s0|f1` at 0.032. The in-domain rate is not comparable to round four's
0.00 -- that was a different sweep, and the difference is evidence variance
rather than anything this round fixed.

## Residual concerns worth carrying forward

1. **Fast searches collect nothing.** With `on_anchor_complete: drain`, a very
   short fixed-node anchor search can finish before shadow qualification
   completes, producing a `cancelled` run with little or no shadow evidence.
   Honest, recorded, but it means evidence density depends on the time control.
2. **The anchor is charged an estimate, not a measurement.** Shadow
   finalization happens before the anchor completes. Over-counting is the safe
   direction, but it is not a measurement.
3. **Disjoint ownership limits the residual geometry.** The most interesting
   cross-engine questions need an overlap-capable COMPARE/VERIFY phase.
4. **The calibration is small and in-session.** 36 runs on one machine with one
   engine build. It is a working pipeline, not a general model.
5. **`stage_cpu_ms_estimate` is a declared constant.** If it is badly wrong, the
   envelope is respected but the reservations are a poor model of reality.
6. **`stage_gpu_ms_estimate` is the same kind of constant, and it is the only
   GPU accounting there is.** Nothing measures actual accelerator time; the
   envelope is respected against an estimate. A GPU envelope declared without an
   estimate now fails `gpu_accounted` rather than quietly claiming compliance,
   but that is a refusal to claim, not a measurement.
7. **The alpha-beta workers contribute almost no labelled evidence.** They
   finish node-limited stages too fast to produce many checkpoints, so the
   calibration is LC0-shaped by accident of timing rather than by design.
   Collecting usable Stockfish and Reckless evidence needs longer stages, not a
   lower support floor.
8. **The eligibility filter is currently inert.** `contract_validatable` removes
   zero rows on this sweep because every stream translated cleanly. It is
   correct and untested by real data.
9. **The telemetry backlog guard is also inert here.** `observation_backlog` was
   zero on all 153 contract decisions. The writer thread keeps up on this
   hardware at this event rate; the guard exists for when it does not, and no
   run in this repository has exercised it.
10. **The anchor finalization wait has no deadline of its own.** It ends when the
    anchor answers, the anchor dies, or the controller closes. That is the right
    set of conditions, but it means a live anchor that never answers holds the
    worker thread until the controller shuts down. There is deliberately no
    arbitrary timeout, because inventing one is what produced the defect it
    replaced.
11. **The calibration is now out-of-domain on its own held-out split.** One
    bucket clears the support floor and no held-out row lands in it. The
    pipeline is working and the model is honest about knowing nothing; that is
    not the same as the model being useful. Closing this needs more evidence,
    not a lower floor.
12. **`stage_timeout_s` is a new declared constant with no measurement behind
    it.** It replaces a worse constant. A stage that legitimately needs longer
    than it will still be cut off, and nothing here establishes the right value.
13. **Observer failures are recorded but not surfaced in the manifest.** They
    live on the runtime and in shadow health; a run whose anchor stream was
    damaged by an observer exception is not called out anywhere a reader would
    look first.
14. **CPU accounting is an estimate built from a configured option.** Nothing
    measures process CPU. `Threads` scaling is much closer than raw wall time
    and is labelled in the certificate, but a config that lies about its thread
    count, or an engine that uses more, is not detected.
15. **`derived_id` still does not hash the extractor's output.** It hashes
    sources, parameters and `EXTRACTOR_VERSION`. That makes the version bump
    load-bearing: any future change to extraction logic that forgets it
    reintroduces the collision found this round.
16. **Five review rounds have not converged.** Rounds three, four and five each
    found defects introduced or left incomplete by the round before. That is a
    property of the change's size, and it is the strongest argument in this
    document for landing the overlap phase separately rather than growing this
    branch further.
