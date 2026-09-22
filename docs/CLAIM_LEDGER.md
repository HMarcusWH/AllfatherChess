# Claim ledger

Every substantive statement this milestone makes, labelled. The labels are not
decoration: `MEASURED` never becomes `PROVED`, `CALIBRATED` never becomes chess
truth, and `POLICY` never becomes a strength gain.

```text
PROVED       enforced by code, tests, contracts, or the compiler
MEASURED     observed in a replay or experimental artifact on this checkout
DERIVED      deterministic calculation from measured data
CALIBRATED   a relationship learned and validated out of sample from replay data
POLICY       a controller decision rule, chosen not discovered
OPEN         not established
```

## PROVED

Enforced by `tests/controller/*`, `scripts/shadow-execution-contract.py`, and
`scripts/active-routing-contract.py`.

### Identity and authority
- AllfatherChess is the only external UCI identity; no constituent handshake
  leaks.
- Four managed instances exist with distinct process roles and correct solver
  families; exactly one instance may hold the anchor role.
- `engine` (solver family) and `engine_instance` (process role) are preserved
  separately in every telemetry event.
- Exactly one outward `bestmove` is emitted per successful external search.
- No shadow bestmove reaches stdout, even when every shadow prefers a different
  move from the anchor.
- A shadow crash does not change the outward move and promotes nobody.
- An anchor failure still fails closed with `bestmove 0000`.

### Ownership
- The observation partition is exact, pairwise disjoint, and covers the
  qualified legal-root universe.
- The partition is deterministic and ignores every evidence signal.
- Each dispatched worker receives exactly `ledger.active_roots(owner)`, with
  `searchmoves` last.
- Empty owner regions and terminal universes never dispatch.
- On real engines, every shadow candidate, PV head, and bestmove stays inside
  its owned region.
- The anchor runs exactly once, unrestricted, and holds no shard.

### Lifecycle
- The anchor search is dispatched before any shadow work.
- The anchor may not be configured as the `go perft 1` oracle.
- A state mutation cannot proceed until the previous shadow generation drains,
  so no stale generation observes the next position.
- `stop` drains; `quit` leaves no orphan process (asserted by `pgrep` in both
  the fast tests and both real-engine contracts).

### Evidence integrity
- Every generated telemetry stream validates against the **unchanged**
  telemetry v1 contract.
- Replay manifests are complete, JSON round-trippable, sha256-verifiable,
  tamper-detectable, and detached from live controller state.
- Raw manifests and raw streams contain no residual, ranking, overlap, or
  routing vocabulary.
- An incomplete stream is marked `contract_validatable: false` rather than given
  a fabricated completion.
- A missing stream is reported, never imputed.

### Scale firewall
- Combining two differently-tagged engine values raises `ScaleMixingError`;
  Stockfish cp minus Reckless cp is a type error, not a convention.
- Alpha-beta node units and LC0 count units remain distinct in telemetry, in
  derived features, and in the budget ledger, which exposes no scalar total.

### Budget and routing
- Concurrent reservations cannot exceed the envelope (24 threads on a barrier
  against a 10-slot ceiling grants exactly 10).
- Verification and controller-overhead reserves are withheld from solver work.
- Controller overhead is charged to the envelope with a monotonic clock.
- A stop is impossible without calibration present, **validated out of sample**,
  in domain **for that worker's own solver family**, sufficiently supported,
  below the risk threshold, past the minimum observation, reading a live
  observation view that is still current, and reading one with **nothing left in
  flight** from the engine. A model whose deterministic split left zero held-out
  rows may be consulted but may not license suppression.
- Every threshold in that conjunction is range-checked at startup, so a config
  file cannot delete a gate by making it vacuously true.
- The legal-root oracle must be an observational `shadow` instance, so its
  failures stay evidence and can never reach outward authority.
- A telemetry-observer exception cannot withhold the outward answer: the
  authority callback runs whether or not observation succeeded, and the failure
  is recorded as evidence rather than raised.
- A stream that has dropped events or failed adapter translation may not
  authorize suppression for the rest of the run.
- A configuration may not start the engines in Chess960: the variant is the
  GUI's to set, and a startup option would leave every replay recording a
  variant the engines were not searching.
- An envelope claim requires the run to have finished inside `wall_ms`, not
  merely inside the CPU and GPU ceilings.
- A shadow worker that overruns its declared stage budget and then ignores
  `stop` is quarantined, exactly as one that misses its drain deadline is.
- Timeouts must be finite: an infinite one reaches `Event.wait()` and raises,
  and the quiesce path would otherwise proceed without excluding the worker.
- An observational instance that fails its startup handshake is recorded and
  excluded; only an authority role's failure aborts the controller.
- A shadow worker that answers with a move outside its assigned root region is
  recorded as a failed stage, not a completed one.
- The observation floor is declared per engine-native semantics; an alpha-beta
  node count cannot satisfy a floor on an LC0 visit-derived count, and an
  undeclared quantity satisfies none.
- A calibration whose contents no longer address to its own `model_id` is
  refused at load.
- Instance names are restricted to a safe filename alphabet, because they are
  used directly as telemetry filenames.
- Replay setup on the pre-anchor path is bounded by `shadow.prepare_budget_s`;
  past it a search proceeds without a bundle rather than waiting on the
  filesystem.
- A stop requires held-out evidence **in the bucket being served**, not merely a
  non-empty holdout somewhere in the model.
- Every routing threshold, including per-semantics observation floors, is
  range-checked at startup.
- The outward anchor must be a Stockfish-family instance; no configuration can
  move decision authority to another engine.
- A calibration's **evaluation** is content-addressed alongside its buckets,
  because authorization reads held-out evidence out of it.
- Every piece of pre-anchor filesystem work is inside `prepare_budget_s`, not
  just the first, and is inside the wall envelope: the run clock starts before
  preparation, so `prepare_ms`, `qualification_ms`, `started_monotonic` and the
  budget ledger's seed all share one origin. `prepare_budget_s` is ONE deadline
  shared by every step, not a window per step.
- Decision authority never waits on observation: the GUI's `stop` reaches the
  anchor before any shadow is contacted, `stop` writes to a shadow never happen
  under the coordinator lock, and cancellations raised on an authority thread
  are detached and joined by the quiesce barrier.
- Stream creation is bounded everywhere it happens, at dispatch as well as
  before the anchor.
- `drain_timeout_s` bounds the whole quiesce barrier, not each wait within it.
- An owner released by the quiescence timeout is marked failed, so its region
  is never sealed as normally completed.
- A routing threshold must be a number: a JSON boolean is refused rather than
  coerced to a value that silently satisfies its own gate.
- The wall envelope binds the first shadow stage, not only extensions.
- Closing a telemetry stream is bounded by its declared timeout even when the
  queue is full.
- Pre-anchor work abandoned at the budget is released, not leaked: a writer
  that finishes late is closed and its file removed, and every path that can
  leave a bundle directory no run will finalize into -- a late `mkdir`, a
  stream timeout, or a stream constructor that raises -- takes it back.
- A declared `stage_timeout_s` is used exactly as configured.
- The authority stream stays open until the anchor answers, the anchor dies, or
  the controller closes. No shadow-side timeout can close it early.
- A denied stop degrades to continued observation, never to an improvised
  action.
- Routing decisions are byte-identical under fixed evidence and a fixed clock.
- A declared-but-unloadable calibration refuses to construct a router.
- The outward fixed-node decision in shadow and active mode equals the direct
  Stockfish decision under the same configuration.

## MEASURED

Observed on this checkout with real Stockfish, Reckless, and LC0 builds. These
are observations, not guarantees.

- Concurrent shadow execution works on real engines: three restricted workers
  overlapped a 1.5 s anchor search, all three dispatched ~3 ms after run start.
- Controller overhead before anchor dispatch (`prepare_ms`) is 1.5–1.8 ms. It
  was ~700 ms before this PR's fix to per-run binary hashing — that regression
  was found by this milestone's own audit discipline. The figure rose from
  ~0.6 ms when the measurement's origin moved ahead of preparation in round
  nine; the extra ~1 ms was always being spent, and was previously outside both
  `prepare_ms` and the wall envelope.
- A 1200 ms `go movetime` under a 4000 ms declared envelope reports
  `wall_ms_elapsed` of 1204–1206 ms with the clock now starting before
  preparation.
- An empty, manifest-less directory under `replay_root` makes
  `build_derived_artifact` raise rather than skip it, so an abandoned setup
  thread's leftover directory is not inert.
- A 36-bundle sweep over the frozen corpus completed in ~25 s and produced 11
  completed, 19 cancelled (superseded by the next position), and 6 terminal
  runs.
- On a 1.5 s real Stockfish anchor search, the leader stabilized at ~21 % of the
  observation trajectory and ~99.5 % of nodes were spent after that point. This
  is the speculative-waste signal, measured; it is **not** evidence that the
  extra nodes were useless.
- The active-routing contract verifies the FULL `envelope_claim`, not just the
  CPU/GPU reservation bit. Measured on this checkout: **0 of 4 movetime runs
  achieve the full claim**, all 4 short on wall time by up to 7.0 ms, because
  that fixture declares `budget.wall_ms` equal to the anchor's own `movetime`
  and the outward search alone saturates it. 1 fixed-node run correctly claims
  nothing. Earlier revisions of this PR reported "envelope respected in 5 runs"
  on the strength of the reservation bit alone; that sentence was stronger than
  what was asserted and has been withdrawn.
- Routing decisions per contract run rose from 141 to 237 once the checkpoint
  wait stopped scaling with the owner count. Same policy, same thresholds; the
  router now looks as often as it was configured to.
- The active-routing contract collected 10 evidence bundles, fitted a model over
  400 rows and 21 buckets with a 280/120 train/held-out split (Brier 0.026,
  in-domain rate 0.85), then produced 165 routing decisions across 5 active runs
  with 3 authorized stops and 28 denied stops. The envelope was respected in
  every run (peak 5413 of 6000 declared CPU-ms) and measured controller overhead
  was 16-25 ms per run.
- Every active run in that contract ends `cancelled`: the driver synchronizes
  the next position immediately after each bestmove, which quiesces the run.
  That is the lifecycle barrier working, and it is recorded rather than smoothed
  over.

## DERIVED

Deterministic calculations from the measured data, versioned as
`residuals-v1` and content-addressed.

- Per-engine leader trajectories, flips, stabilization fraction, PV persistence,
  and engine-native work-after-stability ratio.
- Structural cross-engine comparisons with explicit shared-support size.
- Counterfactual labels: `later_leader_changed`, `later_pv_changed`,
  `stable_to_end`, `reversal_within_horizon`, `first_discoverer_of_anchor_move`.

## CALIBRATED

- `bucketed_reversal_risk_v3`, extractor `residuals-v3`, fitted from 178 rows
  over a 36-run sweep: 10 buckets, 133 train / 45 held-out rows, Brier 0.026,
  in-domain rate 0.29, prior 0.082.
- **These counts are not stable across sweeps and should not be read as if they
  were.** The sweep's completed/cancelled mix varies run to run with the
  anchor-versus-shadow race, and that variance moves the row count more than any
  fix in rounds four or five did. On a thinner sweep the same pipeline yields
  140 rows with one servable bucket and an in-domain rate of 0.00, and the
  fitter prints that the model is out-of-domain everywhere and can license only
  conservative actions. Deriving two code revisions from *identical* bundles
  gives identical rows, which is the comparison that isolates a code change.
- The label attrition chain on the sweep measured here: 1260 checkpoints ->
  1200 carrying a leader -> 388 surviving right-censoring -> 178 after
  shadow-only, family-scoped, eligibility-filtered selection.
- **The evidence is almost entirely LC0.** Once buckets were scoped by solver
  family and anchor rows excluded, every well-supported bucket turned out to be
  `lc0|…`: the alpha-beta shadow workers finish their node-limited stages too
  quickly to contribute many labelled checkpoints. So there is currently **no
  usable calibration evidence for Stockfish or Reckless workers**, and the
  router correctly authorizes nothing for them. Before the fix, stops for those
  workers were being authorized from LC0-derived buckets.
- **The fitted relationship is not monotone in the assumed direction, and the
  sign is the opposite of the one the routing gate assumes.** Of the two
  buckets with enough support to serve a decision (`stop_min_support = 25`):

  | bucket | meaning | support | fitted risk |
  | --- | --- | ---: | ---: |
  | `lc0\|n3\|s3\|f0` | has never flipped its leader | 39 | **0.220** |
  | `lc0\|n3\|s0\|f1` | flipped recently, current run < 25% of history | 29 | **0.032** |

  A worker that has never flipped is measured as ~8x *more* likely to flip
  within the next horizon than one that just flipped. The v2 entry that stood
  here claimed the opposite ("risk falls from 0.082 in the least-settled bucket
  to 0.011 in the most-settled"); that reading was an artifact of pooling anchor
  and cross-family rows into shared buckets, and it does not survive scoping.
  The ordering survived the round-four span correction unchanged; only the
  magnitudes moved.
- **Consequence: zero authorized stops is structural, not marginal.** Both
  servable buckets fail the conjunction, and each fails a different half of it:
  `s0|f1` clears `stop_max_reversal_risk = 0.05` and fails
  `stop_min_stability_fraction = 0.6`; `s3|f0` clears the stability gate and
  fails the risk gate (0.220 > 0.05). On a thinner sweep `s0|f1` drops below
  `stop_min_support = 25` entirely and only the high-risk bucket is servable, so
  the conjunction cannot be satisfied even in principle. The support floor was
  **not** lowered and neither threshold was moved across any of the five review
  rounds; doing either would manufacture stops out of a model whose own held-out
  evidence does not support them.
- The reliability table agrees on the held-out rows where it has counts, and the
  model under-predicts slightly in its largest buckets; that is left uncorrected.

This is calibrated **about an engine's own leader stability**. It is not a
statement about chess correctness.

## POLICY

Chosen, not discovered. Every value is declared in `config/*.json`.

- `conservative_v1`: stability nominates a stop; only the gate authorizes it.
- `root_index % owner_count` as the observation partition.
- `on_anchor_complete: drain` — an in-flight node-limited stage finishes, but no
  new stage opens once the outward decision is emitted.
- Shadow workers at `MultiPV = 3`; anchor at `MultiPV = 1`.
- All thresholds: `stop_max_reversal_risk`, `stop_min_support`,
  `stop_min_stability_fraction`, `min_observation_nodes`,
  `max_stages_per_owner`, `stage_cpu_ms_estimate`, the envelope, and the
  verification reserve.
- Charging the anchor its declared reservation rather than a measured figure.

## OPEN

Nothing below is established by this milestone.

- **Strength.** No Elo experiment was run. Allfather is not shown to beat
  Stockfish, Reckless, or LC0 at any resource level.
- **Equal-envelope validation.** Active mode accounts compute inside a declared
  envelope, but has not been run against an equal-resource benchmark opponent.
  Shadow mode deliberately overspends.
- **Timed equivalence.** Concurrent execution perturbs wall-clock search through
  CPU/GPU contention. No resource isolation was established, so only fixed-node
  decision equivalence is claimed.
- **Generalization of the calibration** beyond the positions, time controls,
  hardware, and engine builds it was fitted on.
- **Whether reversal risk predicts move quality.** It predicts an engine
  changing its own mind. That is not the same question.
- **Why never-flipped buckets carry the higher measured reversal risk.** Two
  readings fit the data equally well and this milestone separates neither: it
  may be a real property of MCTS leader dynamics, or it may be an artifact of
  *when* each bucket is populated. A worker that has never flipped is
  disproportionately early in its search, and right-censoring keeps only
  checkpoints in the first `1 - horizon_fraction` of a trajectory, so the
  never-flipped population skews toward the part of a search where the leader
  is still moving. The feature vector is deliberately past-only and carries no
  checkpoint position, so the fit cannot tell the two apart. Adding a position
  feature would resolve it and would also reintroduce the train/serve skew that
  was removed in round 1; that trade was not taken here.
- **Direct Stockfish-vs-Reckless disagreement.** Disjoint EXPLORE ownership
  still gives it empty support there. Explicit VERIFY now supplies raw
  common-support evidence on the three EXPLORE nominees, but derived
  COMPARE/RELOCK analysis over those child streams remains OPEN.
- **Whether LC0 disagreement carries information beyond Stockfish-vs-Reckless
  disagreement.** VERIFY now supplies the necessary common support, but the
  incremental-information analysis has not yet been derived or calibrated.
- **LC0 strength qualification.** The validation profile is a backend-light
  random configuration and is explicitly not strength-qualified.
- **Transposition overlap.** Only assigned-prefix non-overlap is guaranteed;
  disjoint prefixes can still transpose.
- **Whether routing overhead is worth its cost.** It is measured, not justified.


## Explicit VERIFY evidence plane

### PROVED by code/contracts

- `RootShardLedger` remains the EXPLORE ownership authority; VERIFY overlap is
  represented separately and never assigns one exploration root to two owners.
- A clean three-owner EXPLORE run nominates exactly the three owner bestmoves in
  owner order. Because EXPLORE regions are pairwise-disjoint and each bestmove
  is containment-checked, those nominees are distinct.
- VERIFY reuses `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow`
  after EXPLORE and gives all three the identical restricted root set. The
  three VERIFY searches are independent observational searches; their results
  do not vote on or delay outward authority.
- Raw VERIFY telemetry remains telemetry v1 with `phase = VERIFY` and
  `decision_authority = false`.
- The VERIFY child artifact hash-binds the finalized parent replay and its own
  streams. Candidate, PV-head and final-bestmove containment is checked against
  the declared common root set.
- VERIFY is rejected in active mode until its compute can be settled through
  the global budget ledger.
- No VERIFY result changes outward decision authority; the unrestricted
  Stockfish anchor remains sole bestmove authority.

## COMPARE / descriptive RELOCK derived layer

### PROVED by code/contracts

- completed VERIFY streams reconstruct into three trajectories with identical
  three-root authorized support;
- the three stable pairings reuse the scale-free residual primitives and require
  shared support exactly 3;
- synchronized checkpoints are defined only over the common controller-clock
  interval in which all three VERIFY searches are observationally live;
- each final VERIFY candidate maps back to the solver that nominated it during
  EXPLORE, enabling deterministic candidate-adoption rows without a vote;
- `terminal-suffix-v1` classifies complete unanimous evidence as
  `RELOCK_OBSERVED`, complete non-unanimous evidence as `RELOCK_FAILED`, and
  incomplete evidence as `RELOCK_UNDEFINED`;
- the content-addressed analysis artifact binds exact parent and VERIFY raw
  hashes and derivation does not mutate raw evidence;
- no cross-engine numeric score subtraction, correctness label, routing action,
  or decision-authority effect is introduced.

### OPEN

- Whether common-support disagreement predicts chess error or useful marginal
  compute.
- Whether three-way convergence or descriptive RELOCK predicts a better move or
  useful authorization condition.
- Whether a real LC0 network contributes complementary decision information.
- Whether VERIFY produces enough value to repay its compute cost.
- Any Elo or equal-resource strength gain.


## Recursive PrefixShardLedger v2

### PROVED by code/contracts

- RootShardLedger v1 remains the unchanged live EXPLORE ownership primitive.
- PrefixShardLedger v2 deterministically identifies shards by generation + full
  canonical move prefix.
- The v2 root partition retains exact coverage, authorized-owner, no-duplicate,
  no-overlap, and atomic-failure semantics.
- A v2 frontier is prefix-free: a dispatchable prefix and one of its descendants
  cannot coexist as frontier regions.
- A sealed frontier leaf may be split atomically into a non-empty declared child
  set; the parent becomes RETIRED and all children are LEASED to the inherited
  owner in the supplied oracle order.
- Leased frontier leaves may be transferred atomically between authorized owners;
  active, sealed, retired, duplicate, or wrongly-owned inputs fail before any
  ownership change.
- Recursive snapshots preserve parent/child round trips, deterministic DFS order,
  monotonic revisions, and JSON-serializable detached state.
- Prefix dispatch compilation preserves existing external history and compiles
  `prefix[:-1]` into the descendant position with `prefix[-1]` as the sole
  restricted root.
- The real-engine contract obtains exact child sets from Stockfish `go perft 1`
  across two recursive levels and has Stockfish, Reckless, and LC0 sequentially
  enforce the same certified depth-3 descendant restriction.
- No live shadow, replay-v1, routing, budget, VERIFY, or outward-authority path
  consumes PrefixShardLedger v2 in this milestone.

### OPEN

- Which COMPARE/RELOCK evidence should nominate a recursive split.
- How recursive REFINE stages should be represented in replay evidence and
  budget accounting.
- Whether recursive localization reduces useful compute waste.
- Position-level overlap after transpositions; prefix-free ownership does not
  imply globally disjoint board-state expansion.
- Any playing-strength or equal-resource benefit from recursive splitting.


## Shadow REFINE execution

### PROVED by code/contracts

- completed raw VERIFY final disagreement deterministically nominates REFINE
  roots in frozen VERIFY candidate order; unanimous finals nominate none;
- initial root EXPLORE continues to use unchanged RootShardLedger v1;
- REFINE mirrors completed root ownership into PrefixShardLedger v2 and records
  both source and v2 snapshots;
- each non-terminal target receives its exact Stockfish shadow perft-1 child
  universe;
- child ownership is an exact pairwise-disjoint `child_index_modulo`
  partition implemented through atomic v2 transfers;
- each owner searches its sibling children from the correct descendant
  `position` with only those child moves in `searchmoves`;
- REFINE telemetry is bound to the descendant position id and the existing
  `REFINE` phase vocabulary;
- candidate, PV-head and final-bestmove escape from an owned child region fails
  the stage;
- temporarily repositioned shadows are restored before release or quarantined;
- no new REFINE stage may commit after the outward anchor completion boundary;
- REFINE evidence is a separate hash-bound sibling artifact and does not mutate
  Replay schema v1 or raw VERIFY;
- active mode still rejects REFINE configuration and the unrestricted Stockfish
  anchor remains sole outward authority.

### OPEN

- Whether VERIFY disagreement predicts chess error.
- Whether one-level REFINE resolves meaningful disagreement.
- Whether repeated recursive refinement is useful.
- Whether REFINE repays its CPU/GPU/wall cost under a competitive envelope.
- Whether REFINE evidence should ever authorize candidate cross-feed to the
  final Stockfish decision path.
- Any playing-strength or equal-resource advantage.


## Active VERIFY / REFINE specialist scheduler

### PROVED by code/contracts

- ordinary solver/anchor work, VERIFY, and REFINE occupy distinct declared
  CPU/GPU partitions inside one global envelope;
- solver work cannot consume specialist reserves, and VERIFY/REFINE cannot
  borrow each other's reserve in scheduler v1;
- active VERIFY requires a successful VERIFY reservation before dispatch;
- active REFINE requires a successful REFINE-oracle reservation before perft-1
  child enumeration and a separate REFINE reservation before each descendant
  engine stage;
- denied or undispatched specialist work is not launched and its reservation is
  released;
- dispatched specialist work settles CPU as stage wall time × configured
  threads; actual spend is not clamped to the estimate;
- per-instance REFINE positioning/restoration overhead is charged as controller
  work;
- `route.json` records specialist authorizations/denials plus per-purpose
  budget totals;
- the envelope claim requires the global CPU/GPU ceiling, specialist partition
  caps, wall limit, bounded anchor request, anchor reservation and declared GPU
  accounting to hold together;
- the active specialist contract preserves Stockfish-anchor sole outward
  authority and leaves no open reservation or orphan process.

### OPEN

- whether VERIFY or REFINE repays its compute in move quality;
- optimal VERIFY/REFINE reserve fractions;
- measured process CPU/GPU occupancy versus the current declared/estimated
  accounting convention;
- any equal-resource playing-strength improvement over Stockfish.
