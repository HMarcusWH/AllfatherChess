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

## Ten more findings from a sixth review, four of them mine

**The worst one could deny outward service entirely.** Shadow process startup
ran inside the same `try` as everything else, so a shadow that died during
`uci`, timed out, or rejected a configured option raised into a handler that
closed the already-healthy anchor and aborted the controller. An observational
outage therefore prevented any outward search -- the exact inverse of what every
post-startup path does with the same failure, and a straight violation of the
authority/observation split this milestone is built on. Startup failures are now
caught per instance: authority roles still fail closed, observational ones are
recorded and excluded.

**Four are round-four and round-five debt.**

- The wall-envelope conjunct added in round five was evaluated at the wrong
  lifecycle point. `on_run_end` runs from `_execute`, before the worker waits
  for `anchor_done`, so the remaining authority-search time was absent from the
  claim and a run could write `claimed: true` and then outlast `wall_ms`. The
  router's run is now closed after the anchor has answered. Round four
  deliberately left it early to avoid changing snapshot timing; that caution
  produced a false claim, so the timing changes.
- Thread scaling reached `_settle_owner` and not the early-stop path, so an
  authorized stop still charged a four-thread worker 400 CPU-ms for 400 ms.
- The per-stage deadline was per *run*, not per *owner*: any owner dispatching
  an extension reprieved every other pending worker for another full budget,
  while the eventual expiry cut loose stages that had not used their own.
- The final `bestmove` was not counted as a horizon reversal. Round four made
  the span reach the completion timestamp; the reversal predicate still looked
  only at candidate updates, so a search whose updates agreed but whose answer
  differed was labelled a safe negative.

**Three more correctness gaps.** `min_observation_nodes` was compared against
any engine's counter, so 4,000 LC0 visit-derived counts satisfied a floor
written for alpha-beta nodes -- the same mixing that raises `ScaleMixingError`
everywhere else in this codebase. Floors are now declared per semantics and an
undeclared quantity cannot satisfy one at all. A shadow answering with a move
outside its assigned root region was recorded as normally completed and its
owner sealed, so an unauthorized leader could reach calibration; that is now a
stage failure. And under `on_anchor_complete: drain`, routing continued after
the outward decision, so a later checkpoint could authorize a `stop_worker` that
killed the very stage the policy said would drain.

**Two identity gaps.** A `model.json` edited while keeping its `model_id` was
served as if it were the artifact that had been evaluated; the address is now
recomputed on load and a mismatch is refused. And instance names were checked
only for non-emptiness while being interpolated into
`run_dir / f"{instance}.jsonl"`, so a `/`, `..` or absolute path wrote telemetry
outside the run directory -- they are now restricted to a safe filename
alphabet.

### The bestmove-reversal fix is inert on this evidence

Measured across this repository's own bundles: **0 of 133** trajectories have a
`bestmove` that differs from their final candidate leader. The label change is
correct and closes a real hole, but it moved nothing here. That is now the third
guard in this document that is correct and untested by real data, alongside the
telemetry-backlog gate and the stream-eligibility filter.

## Five more findings from a seventh review

**One is the round-zero finding, in a different disguise.** The self-audit that
opened this milestone caught engine binaries being sha256-hashed inside
`prepare_run`, which the frontend calls before `start_anchor_search` -- about
700 ms of observational work in front of the outward decision. `prepare_run`
still does a `mkdir`, opens a JSONL file and starts a writer thread on that same
pre-anchor path, and its docstring claimed it "performs no engine IO, so it
cannot delay the outward search". Filesystem IO delays it just as effectively on
a slow or blocked `replay_root`. The directory work now runs off the calling
thread under a declared `shadow.prepare_budget_s`, and past that bound the
search proceeds with no bundle rather than waiting. The docstring was wrong and
is corrected rather than softened.

**One is a gate I added that never meant what it said.** `calibration_validated`
required `test_rows > 0` -- that the model had seen *some* held-out data. A
bucket can be well supported in training while every held-out row landed in
unrelated buckets, so the risk estimate actually being served had never been
evaluated out of sample at all. The gate now requires held-out evidence in the
bucket being served, read from the model's own reliability table.

**Two are round-five and round-six debt.** Thread scaling reached the shadow
workers in rounds five and six and never reached the anchor: `anchor_cost` falls
back to `wall_ms`, which is a *duration*, so a four-thread `go movetime 1000`
reserved 1000 CPU-ms for roughly 4000 spent. And the `observation_floors`
mapping introduced in round six was never range-checked -- a floor of `-1` makes
`minimum_observation` pass for any tagged observation. Round three range-checked
every routing threshold precisely because an out-of-range value deletes a gate
rather than misconfiguring it; adding a new threshold in round six without that
check is the same defect re-entering through a new field.

**One accounting gap.** Native work was recorded only inside routing
checkpoints, and `_await_completion` returns without a final one once a run is
cancelled. Everything the engines reported after the last checkpoint -- including
the final update before a stopped worker's `bestmove` -- never reached
`route.json`, and a cancellation before the first checkpoint reported no native
work at all. It is now reconstructed at run end.

## Nine more findings from an eighth review, six of them mine

This round is the clearest illustration of the pattern this document has been
tracking: **six of the nine were defects my own previous fixes created or left
half-finished**, and three of those were created by the round-seven fixes
specifically.

**Two rounds of fixes combined into a new hole.** Round six content-addressed a
calibration's buckets, parameters and sources. Round seven then made
`calibration_validated` read `test_rows` and the reliability bucket list out of
`evaluation` -- a field that address does not cover. So a fabricated reliability
entry could license a stop for a bucket with no held-out evidence at all, while
`model_id` still verified and `route.json` still reported the original identity.
Neither fix was wrong on its own; together they opened something neither
touched. The evaluation is now addressed too.

**Round seven's pre-anchor bound covered one call and not the next.** It bounded
the run directory's `mkdir` and left `TelemetryStreamWriter`'s own `mkdir` and
`open` outside it -- the same unbounded delay to the outward search, one line
later. The budget now wraps every piece of pre-anchor filesystem work rather
than the first piece.

**Round seven's finalization accounting was outside the accounting.**
`_record_final_native_work` reconstructs every owner's trajectory during
`on_run_end`, before the snapshot the claim is computed from, and was not inside
any `controller_overhead` block.

**Round six's per-owner deadline widened a hole it was fixing.** Cutting loose
an overrunning owner cancels the run, which sends `stop` to *every* pending
owner -- but only the overrunning ones were waited for and quarantined. A
non-overrunning worker slow to answer `stop` was left running while the run
finalized and cleared `_run`.

**Round five's writer shutdown still wrote through a closed handle.** Marking
queued items dropped does not stop the consumer thread; when it resumed it could
write into a file this method had already closed, and the snapshot taken
immediately after could hash a partial stream. The writer now refuses to write
once the stream is abandoned.

**Round five clamped a setting it had just declared.** `max(1.0, stage_timeout_s)`
silently replaced a declared 50 ms cap with one second, so a stuck worker could
run twenty times its configured budget before cancellation began.

**Three were not mine.** Orchestration raising after dispatch left already-running
stages neither stopped nor drained, so finalization could clear `_run` while
workers searched on. A valid MultiPV frame reporting the primary line as mate
and the runner-up in centipawns made `within_engine_margin` raise
`ScaleMixingError` out of `extract_features`, aborting derivation for the entire
corpus -- an incomparable pair is an unavailable margin, not a crash. And a
schema-v2 config could name a Reckless or LC0 anchor and pass every check,
silently changing which engine holds the outward decision that every claim in
this milestone attributes to Stockfish.

### A test that proved nothing, again

The first version of the mixed-score test used an evaluation kind that
`Observation.primary_evaluation` filters out, so the margin short-circuited to
`None` before any comparison was attempted and the test passed with the fix
reverted. That is the third time in three rounds a regression test had to be
rewritten because it could not fail. The discipline of reverting each fix and
re-running is the only reason any of them were caught.

## Four more findings from a ninth review, all four mine

Every finding this round is a consequence of the pre-anchor preparation budget
I introduced in round seven and extended in round eight. Two were reported;
the other two I found while checking the blast radius of the second.

**The envelope clock skipped the preparation it is supposed to bound.**
`started_monotonic` was captured *after* `_make_run_dir_within_budget()`
returned, so every millisecond of pre-anchor filesystem work sat outside the
wall envelope and outside the controller-overhead charge. A slow `mkdir` ahead
of a search that nearly fills the envelope could still report
`wall_within_envelope: true` and `claimed: true`. The run clock now starts
before any preparation, and `prepare_ms`, `qualification_ms`,
`started_monotonic` and the ledger's seed all share that one origin.

MEASURED on this machine: `prepare_ms` is 1.5-1.8 ms, so the interval that was
invisible is small in practice. It is bounded by `shadow.prepare_budget_s`
(0.25 s as shipped), not by what a healthy filesystem happens to do -- the
directory `mkdir` may take up to the full budget and still succeed, and all of
it was previously uncounted.

**An abandoned writer kept a thread and a descriptor.** When the timed work was
`TelemetryStreamWriter(...)` and its file open finished after the budget, the
timeout path dropped the constructor's result. That result was not inert: the
constructor had already opened the file and started a writer thread, which then
blocked on its queue forever with nothing to drain it. A repeatedly slow
`replay_root` leaked one thread and one descriptor per search until the
controller ran out. The budget helper now takes a `discard` callback and the
worker thread closes its own late result.

**An abandoned `mkdir` left a directory that is not a bundle.** The same
timeout path can leave an empty, manifest-less directory under `replay_root`
after the late `mkdir` completes. Round seven's docstring called abandoned
results "inert leftover"; this one is not. Offline derivation walks every
directory under `replay_root`, and
`build_derived_artifact` raises `ReplayError: cannot load replay manifest` on a
directory with no manifest rather than skipping it -- so one timeout during a
game would break the entire derivation pass afterwards. MEASURED directly. The
late `mkdir` is now taken back with `rmdir`, which removes only an empty
directory and raises rather than deleting anything else.

**And the ordinary stream-failure path left the same directory.** Found while
writing the test for the one above. `prepare_run` creates the bundle directory
first and the stream second, so *every* way the stream can fail -- a timeout, or
a constructor that simply raises `OSError` on a full disk -- returns with the
directory already created and no run that will ever finalize into it.
`ReplayRun.finalize` writes a manifest for every disposition, `aborted` and
`cancelled` included, so a manifest-less directory is never a run that lost its
evidence: it is a run that never started. Fixing only the abandoned-thread case
would have been round eight's mistake again -- bounding the first call and not
the next. Every path that can leave one now takes it back.

### A test that proved nothing, twice in one round

The writer-leak test had to be rewritten twice, for two different reasons, and
both times it passed with the fix reverted.

The first version checked for leaked threads immediately after `prepare_run`
returned -- which is 0.25 s in, while the constructor is still sleeping in
`open` and no thread exists yet. Nothing had leaked because nothing had been
built.

The second version waited, but stalled the constructor *before* its `open`. By
the time that open ran, the directory cleanup added for the finding above had
already removed the directory, so the constructor raised, no writer was ever
built, and again there was nothing to leak. The fix for one finding had made
the test for another unfalsifiable. The test now opens the handle first and
stalls afterwards, which is what a slow filesystem does to a constructor that
succeeds -- the only shape in which the leak exists at all.

That is five regression tests in four rounds that could not fail as first
written. Every one was caught by reverting the fix and re-running, and by
nothing else. The two leak tests now also assert that the abandoned work
really happened, so a test that silently stops exercising its path fails
instead of passing.

### A test that was never run at all

The three new tests were appended to the end of `test_shadow_runtime.py`,
below its `if __name__ == "__main__": unittest.main()` block. `make
controller-tests` runs each file as a script, so the class was defined after
the run had already finished: the suite reported 39 tests and "OK" while three
brand-new tests sat in the file untouched. Caught only because the count did
not go up. Green CI would have said nothing.

## Ten more findings from a tenth review, and a claim this PR had been overstating

The most important thing this round produced is not a fix. It is a correction:
**every earlier report of this branch, including the PR description and four of
my own PR comments, said the active contract showed "envelope respected in N
runs". It did not.** The contract asserted `budget.within_envelope`, which is
the CPU/GPU *reservation* bit alone, while the sentence it printed described
`envelope_claim.claimed`, which additionally requires a bounded outward
request, a reserved anchor cost, GPU accounting and wall-time compliance. That
is exactly the silent upgrade -- MEASURED reported as something stronger -- that
`docs/CLAIM_LEDGER.md` exists to prevent, and it survived nine rounds.

With the full claim asserted, the measured truth is:

```
envelope components verified in 4 movetime run(s) (0 full claim, 4 short on
wall by up to 7.0ms against a wall_ms declared equal to the movetime),
1 wall-unbounded run(s) correctly claimed nothing
```

**Zero runs achieve the full claim.** The contract's active fixture declares
`budget.wall_ms` EQUAL to the anchor's own `movetime`, so the outward search
alone saturates the wall envelope and the ~5-7 ms of controller work can never
fit. Raising that figure would make the claim true, so it was left exactly as
declared and the shortfall is measured and printed instead. The contract now
asserts each component that must hold, and fails if the claim is false for any
reason *other* than that wall shortfall.

The other nine findings:

**The GUI's `stop` could be swallowed by a blocked shadow (P1).** The `stop`
handler cancelled shadow observation *before* calling `stop_anchor()`, and
cancellation writes `stop` to every dispatched shadow process. One blocked
shadow stdin meant the anchor was never asked for its already-computed
`bestmove`. Two more paths had the same shape: `_cancel_locked` performed those
writes while three of its four callers held the coordinator lock -- which also
blocks `note_anchor_complete`, the path that records the anchor's completion --
and `on_anchor_complete: cancel` ran them on the anchor's own stdout reader
thread. Authority now goes first, the writes happen outside the lock, and
authority-path cancellations detach them onto a short-lived thread that
`quiesce()` joins, so a detached `stop` can never land on a later generation.

**Closing a telemetry stream could block forever.** `close()` enqueued its
sentinel with a blocking `put()`. On a full queue with the writer stalled that
waited without bound *before* either timed `join()` was reached, so the
advertised timeout bounded nothing. The sentinel is now offered without
waiting and a flag ends the writer loop when there was no room for it.

**`prepare_budget_s` was two budgets, not one.** Round eight bounded the run
`mkdir` and the anchor stream open separately, so each got a full window and
the outward anchor could be delayed by nearly twice the declared hard cap.
One deadline is now computed in `prepare_run` and every step gets only what
remains of it.

**A JSON boolean could delete a routing gate.** `int()` and `float()` accept
`bool`, so `min_observation_nodes: false` became 0 -- which is IN range, so
`__post_init__` passed it -- and the alpha-beta minimum-work gate then
succeeded for any counter. `stop_max_reversal_risk: true` became 1.0 and
admitted every bucket. Types are checked before coercion now.

**Routing checkpoints ran at N times the configured interval.** The wait loop
waited `interval` on each pending owner in turn, so with three owners every
routing decision and stage-deadline check happened roughly every
`3 * checkpoint_interval_ms`. One slice now bounds the whole iteration. This
is why the contract's decision count rose from 141 to 237 with no policy
change: the router is now looking as often as it was configured to.

**The wall envelope did not bind the first stage.** `authorize_extension`
checked `wall_exhausted()`; `authorize_initial` never did, so preparation and
qualification could spend the envelope and every initial stage was still
reserved and dispatched past the declared deadline.

**One unbounded filesystem open was left.** Creating a later owner's telemetry
stream at dispatch time blocked the coordinator after earlier owners were
already searching, so no stage deadline and no wall check ran while those
engines kept spending envelope. It is bounded like the pre-anchor path now;
past the bound that owner contributes no evidence rather than freezing the ones
that do.

**`drain_timeout_s` was two timeouts, not one.** `quiesce()` passed the full
timeout to the worker join and then again to the finished-event wait, so a
setting documented as the hard bound for draining could hold state-changing UCI
commands for nearly twice itself.

**A stuck owner's region was sealed as complete.** The quiescence-timeout path
released the waiter without setting `state.failed`, and `_execute` seals an
owner that is `dispatched and not failed` -- so the manifest carried a failed
stage whose region was nevertheless represented as normally completed.

### A test that proved nothing, for the sixth time

The shared-deadline test could not fail as first written. `Path.mkdir(parents=True)`
retries itself after creating a missing parent, so the patched sleep fired
twice and consumed the entire budget inside the FIRST step -- the second step
was never reached, and both behaviours produced an identical 0.301 s. Caught by
measuring both configurations directly and finding them equal, then
instrumenting the fixture to see why. The test now pre-creates the replay root
and only slows a `mkdir` that really creates, so each step fits the budget and
only their sum does not.

That is six regression tests in five rounds that could not fail as first
written. Every one was caught by reverting the fix and re-running, and by
nothing else.

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
16. **Ten review rounds have not converged.** Rounds three through six each
    found defects introduced or left incomplete by the round before -- four in
    round five, four again in round six, two in round seven, plus a round-zero
    finding that reappeared in a different disguise, then **six of nine in
    round eight**, **four of four in round nine**, and **ten in round ten**.
    Round eight's worst case came from two individually correct fixes
    combining; all of round nine came from one mechanism I added in round seven
    and extended in round eight; round ten found that mechanism split into two
    budgets, three more places where observation could block authority, and a
    claim this PR had been overstating since before the first review. The count
    went 9 -> 4 -> 10. It is not converging. No threshold, support floor or
    declared budget has been moved in any round and the claim firewall held in
    the sense that it caught the overstatement -- late, but it caught it. This
    remains the strongest argument in this document for landing the overlap
    phase separately rather than growing this branch further.
17. **The bestmove-reversal label is untested by real data.** 0 of 133
    trajectories exercise it here. So are the telemetry-backlog gate and the
    stream-eligibility filter. Three correctness guards in this milestone are
    reasoned rather than observed.
18. **`stage_timeout_s` is now per owner but still one declared constant.**
    Nothing measures what a legitimate stage needs, so a worker that genuinely
    wants longer than the configured budget is still cut off.
19. **`prepare_budget_s` is another declared constant.** Nothing measures what
    a healthy `replay_root` needs (`prepare_ms` is 1.5-1.8 ms on this machine,
    against a 0.25 s budget). Abandoning a bundle is the safe direction when
    the filesystem is slow, but a busy disk will now cost observations. The
    claim this entry used to make -- that whatever an abandoned setup thread
    finishes is "inert leftover" -- was wrong twice over, and round nine
    measured both: a completed writer holds a thread and a descriptor, and a
    completed `mkdir` leaves a manifest-less directory that breaks offline
    derivation. Both are released now -- as is the same directory on the
    ordinary stream-failure path, which needed no abandoned thread at all --
    but the general shape of the risk stands: anything added to this path has to say what happens when it
    finishes late, and "nothing" needs proving rather than assuming.
20. **Anchor CPU is still an estimate.** Scaling `wall_ms` by the configured
    thread count is much closer than not scaling it, but a config that
    misstates `Threads`, or an anchor that finishes early, is not detected.
21. **The wall envelope now includes preparation, but not the quiesce barrier.**
    The run clock starts at the top of `prepare_run`'s own work. Time spent in
    `quiesce()` draining the *previous* generation still sits outside this
    run's envelope. That is a deliberate attribution choice -- the previous
    generation's compute was charged to the previous generation's ledger, and
    charging it twice would inflate B -- but it does mean a search that waits
    on a slow drain took longer in wall-clock terms than its own
    `wall_ms_elapsed` reports. POLICY, not a measurement gap.
23. **No real-engine run has ever demonstrated a full envelope claim.** The
    active contract's fixture declares `budget.wall_ms` equal to the anchor's
    own `movetime`, so the outward search alone saturates the wall envelope and
    `claimed` is structurally false for every run it produces (measured: short
    by up to 7.0 ms). The TRUE branch of that conjunction is covered by a unit
    test with a headroom envelope, and the contract asserts every other
    component plus that the wall shortfall is the ONLY failing one. Raising the
    declared figure would make the claim true, which is why it was not raised.
    Adding a second contract run under a headroom envelope would close this
    honestly; it was not done here because this branch is already too large.
24. **Routing now checkpoints roughly three times as often as it did.** Fixing
    the per-owner wait raised the contract's decision count from 141 to 237
    with no policy change. That is the configured cadence finally being
    honoured, not new behaviour -- but every cadence-sensitive figure measured
    before round ten was measured against a router that looked a third as
    often, and none of those earlier numbers were re-measured.
22. **Bundle discovery still treats every directory as a bundle, and this was
    not fixed.** Five places enumerate `replay_root` with
    `path for path in replay_root.iterdir() if path.is_dir()`, and
    `build_derived_artifact` raises on a directory with no `manifest.json`
    rather than skipping it (MEASURED). Round nine removed every orphan the
    controller itself can create, but a SIGKILL or a full disk between the
    `mkdir` and `finalize` still leaves one, and no cleanup path runs then.
    The robust fix -- select directories that contain a manifest, and report
    what was skipped rather than ignoring it -- is sound and provably hides
    nothing, because `finalize` writes a manifest for every disposition. It
    touches five scripts including two contract harnesses, and this branch's
    own defect history is the argument against making that change here. Left
    OPEN deliberately, not overlooked.
