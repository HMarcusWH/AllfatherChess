# Active specialist scheduler

## Status

PR #18 moves explicit VERIFY and one-level REFINE from the deliberately
over-budget shadow observatory into active mode under the same declared
CPU/GPU/wall envelope as anchor and ordinary shadow work.

The control path is now:

```text
EXPLORE nomination
    ↓
active reservation
    ↓
dispatch / settle

VERIFY nomination
    ↓
VERIFY reserve
    ↓
dispatch / settle

REFINE disagreement target
    ↓
REFINE oracle reserve
    ↓
exact child shell
    ↓
REFINE stage reserve
    ↓
dispatch / settle
```

No specialist reservation grants chess decision authority. The unrestricted
Stockfish anchor remains the sole outward bestmove source.

## Resource partitions

`ResourceEnvelope` declares:

- total wall budget;
- total CPU budget;
- total GPU budget;
- VERIFY reserve fraction;
- REFINE reserve fraction;
- controller-overhead reserve.

The same specialist fractions partition CPU and GPU. Solver/anchor work cannot
consume the specialist partitions; VERIFY cannot consume the REFINE partition;
REFINE cannot consume the VERIFY partition.

Actual stage spend is never clamped to a reservation. M14-B settles from physical process CPU when coverage exists; if measurement is unavailable, the conservative wall×threads/declaration fallback remains explicit. An overrun is charged in full regardless of source. A run may therefore remain
inside the global envelope while failing a declared partition cap; such a run
cannot claim specialist-envelope compliance.

## Reservation-before-dispatch

Active specialist work follows a strict sequence:

```text
nominate
→ authorize
→ reserve
→ dispatch
→ settle
```

A denied or missing reservation means no dispatch.

The router records every specialist authorization with:

- phase;
- owner;
- target id where applicable;
- requested CPU/GPU;
- available CPU/GPU before reservation;
- reservation token;
- granted/denied result and reason.

Undispatched reservations are released. On the Linux qualification platform, dispatched stages settle from procfs process CPU; wall duration multiplied by configured engine threads remains a labelled fallback only. GPU device-time has no qualified provider in M14-B, so any profile that requires it fails closed rather than promoting a utilization estimate to measurement.

## VERIFY

Every active VERIFY participant must hold a `verify` reservation before its
restricted common-support search starts.

Partial authorization is valid evidence but produces an incomplete VERIFY
artifact. There is no fallback that silently dispatches an unreserved verifier.

## REFINE oracle

The descendant Stockfish `go perft 1` oracle is no longer free in active mode.
Before the temporary descendant-position request begins, the router reserves
`refine_oracle` work from the REFINE partition.

The settlement interval includes temporary positioning, the oracle request, and
restoration performed by the runtime helper.

## REFINE stages

Each owner/target descendant search requires a separate REFINE reservation.
The existing PrefixShardLedger ownership, exact child partition, descendant
positioning, telemetry containment, cancellation, restore, and authority
barriers remain unchanged.

Per-instance descendant positioning and restoration outside the oracle helper
are charged to controller-overhead lanes.

## Wall boundary

Specialist authorization fails closed once the declared wall envelope is
exhausted. The existing coordinator lock still orders the outward anchor
completion boundary against every VERIFY and REFINE dispatch commit, so no new
specialist stage starts after the outward decision.

## GPU claim discipline

When no GPU envelope is declared, GPU accounting is trivially satisfied.

When `gpu_ms > 0`, a run may claim compliance only if ordinary active stages
and every enabled specialist phase carry non-zero declared GPU estimates.
This is still estimate-based accounting; no claim of measured accelerator
occupancy is made.

## route.json

The active audit adds `specialist_actions` and purpose-partition budget totals.
M14-B also writes content-addressed `resource.json` and binds its SHA/report id
into `route.json`. The strongest envelope claim now requires:

- bounded outward request;
- anchor reservation;
- declared GPU accounting;
- global CPU/GPU envelope compliance;
- solver/VERIFY/REFINE partition compliance;
- wall-time compliance;
- required physical measurement coverage;
- measured physical CPU within the declared CPU envelope.

A final certificate with any open reservation is invalid.

## Validation profile

`config/allfather.active.specialist.validation.json` enables active VERIFY and
REFINE with explicit specialist reserves and conservative finite estimates. The
LC0 backend remains the validation random/backend-light profile, so the real
contract qualifies the control plane only.

`scripts/active-specialist-contract.py` requires:

- one external Allfather UCI endpoint;
- Stockfish anchor sole outward authority;
- valid parent/VERIFY/REFINE evidence;
- reservation-backed VERIFY dispatch;
- reservation-backed REFINE oracle/stages whenever REFINE is applicable;
- zero open reservations at finalization;
- global and partition envelope compliance;
- a positive `envelope_claim.claimed`;
- no surviving engine processes.

## Claim boundary

This milestone proves that specialist computation can be admitted, denied,
charged and audited inside one declared envelope.

It does not prove:

- VERIFY improves chess strength;
- REFINE improves chess strength;
- disagreement is worth its specialist cost;
- the current reserve fractions are optimal;
- the CPU/GPU estimates equal measured process occupancy;
- Allfather beats Stockfish at equal resources.

Those become empirical questions now that specialist work has to pay for itself.
