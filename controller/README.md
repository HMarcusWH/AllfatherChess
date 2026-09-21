# Controller

The controller is the engine. Stockfish, Reckless, and LC0 are solver backends.

## Modules

```text
runtime.py           process roles, authority vs observational health, legal-root oracle
uci_frontend.py      the single external UCI identity and its lifecycle barriers
shards.py            RootShardLedger v1 (unchanged by this milestone)
shadow.py            concurrent restricted dispatch, run lifecycle, replay binding
replay.py            run-level replay bundles and non-blocking telemetry capture
verification.py      explicit common-support VERIFY plan/artifact/integrity
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

`docs/UCI_SHELL.md`, `docs/SHARD_LEDGER.md`, `docs/SHADOW_EXECUTION.md`,
`docs/REPLAY_FORMAT.md`, `docs/RESIDUAL_CALIBRATION.md`,
`docs/BUDGET_ROUTING.md`, `docs/CLAIM_LEDGER.md`,
`docs/THEORY_IMPLEMENTATION_MAP.md`, `docs/ADVERSARIAL_AUDIT.md`.

## What the controller still does not do

No voting, no cross-engine score conversion, no recursive shard split or
transfer, no VERIFY/RELOCK overlap, no cross-feed, no native integration, and no
strength claim. The outward move is the unrestricted anchor's in every mode.


## Explicit VERIFY evidence

`config/allfather.verify.validation.json` remains `mode: shadow`. After a
clean three-owner EXPLORE run, the three owner bestmoves become one ordered
three-root candidate set and the same three shadow processes re-search exactly
that set under telemetry phase `VERIFY`. `RootShardLedger` is not mutated:
the overlap is represented by a separate `VerificationPlan` and raw artifact.

VERIFY is rejected in `mode: active` until its compute is integrated with the
global budget ledger. It has no outward decision authority.
