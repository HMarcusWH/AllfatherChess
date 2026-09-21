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
- Controller overhead before anchor dispatch (`prepare_ms`) is ~0.6 ms. It was
  ~700 ms before this PR's fix to per-run binary hashing — that regression was
  found by this milestone's own audit discipline.
- A 36-bundle sweep over the frozen corpus completed in ~25 s and produced 11
  completed, 19 cancelled (superseded by the next position), and 6 terminal
  runs.
- On a 1.5 s real Stockfish anchor search, the leader stabilized at ~21 % of the
  observation trajectory and ~99.5 % of nodes were spent after that point. This
  is the speculative-waste signal, measured; it is **not** evidence that the
  extra nodes were useless.
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

- `bucketed_reversal_risk_v3` fitted from 150 rows over a 36-run sweep:
  8 buckets, 111 train / 39 held-out rows, Brier 0.030, in-domain rate 0.26.
  The routing contract independently fits its own model over 20 runs: 121 rows,
  11 buckets, 85 train / 36 held-out rows, Brier 0.060, in-domain rate 0.36.
  Base rate 0.097.
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
  | `lc0\|n3\|s3\|f0` | has never flipped its leader | 30 | **0.219** |
  | `lc0\|n3\|s0\|f1` | flipped recently, current run < 25% of history | 24 | **0.038** |

  A worker that has never flipped is measured as ~8x *more* likely to flip
  within the next horizon than one that just flipped. The v2 entry that stood
  here claimed the opposite ("risk falls from 0.082 in the least-settled bucket
  to 0.011 in the most-settled"); that reading was an artifact of pooling anchor
  and cross-family rows into shared buckets, and it does not survive scoping.
  The ordering survived the round-four span correction unchanged; only the
  magnitudes moved.
- **Consequence: zero authorized stops is structural here, and round four made
  it more so.** `s0|f1` now has support 24 and no longer clears
  `stop_min_support = 25` at all, so exactly one bucket is servable: `s3|f0`,
  which clears the stability gate and fails the risk gate (0.219 > 0.05). There
  is no longer a well-supported bucket that could satisfy the conjunction even
  in principle. The support floor was **not** lowered and neither threshold was
  moved; doing either would manufacture stops out of a model that is
  out-of-domain on its entire held-out split.
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
- **Direct Stockfish-vs-Reckless disagreement.** Disjoint ownership gives it
  empty support within a run; an overlap-capable COMPARE/VERIFY phase is needed.
- **Whether LC0 disagreement carries information beyond Stockfish-vs-Reckless
  disagreement.** Cannot be answered without that overlap phase.
- **LC0 strength qualification.** The validation profile is a backend-light
  random configuration and is explicitly not strength-qualified.
- **Transposition overlap.** Only assigned-prefix non-overlap is guaranteed;
  disjoint prefixes can still transpose.
- **Whether routing overhead is worth its cost.** It is measured, not justified.
