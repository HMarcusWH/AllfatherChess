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
