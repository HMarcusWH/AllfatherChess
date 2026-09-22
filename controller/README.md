# Controller

The controller is the engine. Stockfish, Reckless, and LC0 are solver backends.

## Modules

```text
runtime.py           process roles, authority vs observational health, legal-root oracle
uci_frontend.py      the single external UCI identity and its lifecycle barriers
shards.py            live RootShardLedger v1
prefix_shards.py     recursive PrefixShardLedger v2 ownership substrate
refinement.py        raw shadow REFINE plan/artifact/integrity
shadow.py            EXPLORE/VERIFY/REFINE process lifecycle and decision barrier
replay.py            run-level replay bundles and non-blocking telemetry capture
verification.py      explicit common-support VERIFY plan/artifact/integrity
verification_analysis.py offline COMPARE / descriptive RELOCK analysis
replay_analysis.py   trajectory reconstruction and counterfactual stopping labels
residuals.py         derived residual geometry with explicit shared support
calibration.py       fitted, out-of-sample-validated reversal-risk models
budget.py            one declared resource envelope, reserved before it is spent
routing.py           observe -> propose -> authorize -> dispatch
```

Nothing reaches upward: residuals never enter raw telemetry, routing decisions
never enter a replay manifest, and the ledger holds no budget or evidence.

## Topology

```text
GUI / tournament
      |
      v
AllfatherChess UCI shell
      |
      +--> stockfish-anchor   [unrestricted / sole outward authority]
      +--> stockfish-shadow   [restricted ledger owner / perft oracle]
      +--> reckless-shadow    [restricted ledger owner]
      '--> lc0-shadow         [restricted ledger owner]
```

`engine` is the solver family and carries score/work semantics.
`engine_instance` is the process role and carries authority. They are never
conflated: both Stockfish processes share a family and differ in everything
that matters operationally.

## Modes

| Mode | Config | Behavior |
| --- | --- | --- |
| `anchor` | `config/allfather.validation.json` | the frozen PR #9/#10 profile: three managed backends, Stockfish alone searches. Unchanged. |
| `shadow` | `config/allfather.shadow.validation.json` | four instances; three restricted workers observe pairwise-disjoint regions; no routing. |
| `active` | `config/allfather.active.validation.json` | shadow plus a conservative routing policy that allocates observation compute inside a declared envelope. |

The runtime config loader accepts `schema_version: 1` (the legacy family-keyed
anchor profile) and `schema_version: 2` (role-keyed `instances`). Legacy configs,
contracts, and tests are unaffected.

## Health model

Authority health covers the anchor and, in the legacy profile, the managed
backends. A shadow instance is **observational**: its death is recorded as
evidence, excluded from authority health, and skipped during synchronization. It
never fails an outward search and never promotes another backend.

## Lifecycle invariants

- the anchor search is dispatched before any shadow work;
- root qualification runs on `stockfish-shadow`, never on the anchor;
- every state mutation passes a quiesce barrier that cancels and joins the
  previous generation, so no stale generation observes the next position;
- `stop` drains, `quit` leaves no orphan;
- exactly one outward `bestmove` per successful external search.

## Concurrency model

One permanent stdout reader per process, inherited unchanged from PR #9.
Telemetry capture enqueues on that reader thread and translates on a dedicated
writer thread, so capture can never stall or deadlock the reader. Every active
search carries an explicit generation token and every callback checks it.

## Documents

`docs/UCI_SHELL.md`, `docs/SHARD_LEDGER.md`, `docs/PREFIX_SHARDS.md`, `docs/SHADOW_EXECUTION.md`,
`docs/REPLAY_FORMAT.md`, `docs/RESIDUAL_CALIBRATION.md`,
`docs/BUDGET_ROUTING.md`, `docs/ACTIVE_SPECIALIST_SCHEDULER.md`, `docs/COMPARE_RELOCK.md`, `docs/REFINEMENT.md`, `docs/CLAIM_LEDGER.md`,
`docs/THEORY_IMPLEMENTATION_MAP.md`, `docs/ADVERSARIAL_AUDIT.md`.

## What the controller still does not do

No voting, no cross-engine score conversion, no RELOCK authorization, no
VERIFY-based decision influence, no cross-feed, no native integration, and no
strength claim. PrefixShardLedger v2 has both a shadow REFINE consumer and an
active consumer whose specialist work is reservation-gated; neither can alter
the outward decision. A descriptive
terminal-suffix RELOCK is now derived offline, but it is not a controller
certificate. Explicit VERIFY overlap remains observational only; the outward
move is the unrestricted anchor's in every mode.


## Explicit VERIFY evidence

`config/allfather.verify.validation.json` remains `mode: shadow`. After a
clean three-owner EXPLORE run, the three owner bestmoves become one ordered
three-root candidate set and the same three shadow processes re-search exactly
that set under telemetry phase `VERIFY`. `RootShardLedger` is not mutated:
the overlap is represented by a separate `VerificationPlan` and raw artifact.

In active mode, VERIFY is accepted only with a positive declared VERIFY reserve
and every participant must obtain a router reservation before dispatch. It has
no outward decision authority.


## COMPARE / descriptive RELOCK

`controller/verification_analysis.py` is an offline consumer of the raw VERIFY
child artifact. It reuses the existing `SearchTrajectory` reconstruction and
scale-free `compare_at()` primitive to derive pairwise shared-support metrics,
three-way convergence state, EXPLORE→VERIFY candidate adoption, and a frozen
`terminal-suffix-v1` RELOCK descriptor.

No live controller module imports or consumes the derived artifact.


## Recursive prefix-shard substrate

`controller/prefix_shards.py` adds PrefixShardLedger v2 without modifying the
live RootShardLedger v1. It supports deterministic full-prefix identities,
per-shard activation/sealing, atomic split of sealed leaves into owner-inherited
children, atomic transfer of leased frontier leaves, and a prefix-free frontier
invariant.

`common/prefix_dispatch.py` compiles one qualified prefix into the descendant
`position` plus one final `searchmoves` root. The live shadow coordinator
does not import either module yet.


## Shadow REFINE

`controller/refinement.py` turns completed raw VERIFY disagreement into a
deterministic one-level target set. `shadow.py` obtains each target's exact
Stockfish perft-1 child universe, applies the frozen child-index partition
through PrefixShardLedger v2, temporarily positions idle shadow workers at the
descendant board, and records separate REFINE telemetry.

Per-instance position divergence is tracked by the coordinator and must be
restored or quarantined before a generation is released. In active mode the
child oracle and each descendant stage are independently reservation-backed;
REFINE still has no outward decision authority.
