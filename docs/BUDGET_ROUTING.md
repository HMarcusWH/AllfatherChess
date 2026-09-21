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

**Controller overhead is charged, never free.** `controller_overhead()` is a
context manager that measures with a monotonic clock and charges the
`controller` lane inside `B`. Metareasoning that costs more than it saves shows
up in the ledger.

**Reserves are withheld.** `verification_reserve_fraction` and
`controller_overhead_reserve_ms` are subtracted from the ceiling available to
solver work, so exploration cannot eat the verification budget.

**Engine-native counters are never summed across semantics.** The ledger keeps
`stockfish.uci_nodes`, `reckless.uci_nodes`, and `lc0.uci_nodes` separately and
exposes no scalar total. Alpha-beta nodes and LC0 visit-derived counts are not
the same quantity, and the API refuses to imply otherwise.

**Wall time is a deadline, not an additive quantity.** `wall_ms` is tracked
separately from `cpu_ms`; exhausting it triggers anchor-only fallback.

### What is charged how

| Lane | Charge |
| --- | --- |
| `anchor` | its full declared reservation. Shadow finalization happens before the anchor completes, so the controller cannot measure the real figure at settle time; charging the reservation errs toward over-counting, the safe direction for an envelope claim. |
| `shadow:<owner>` | the measured duration of each dispatched stage |
| `controller` | measured metareasoning time |
| `verify` | reserved, unused in this milestone |

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
calibration_present  AND calibration_in_domain
                     AND support       >= stop_min_support
                     AND reversal_risk <= stop_max_reversal_risk
                     AND observed_work >= min_observation_nodes
```

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
