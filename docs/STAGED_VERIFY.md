# M14-G1 — Same-process staged VERIFY and serve-compatible value-of-compute

## Status and claim boundary

M14-G1 adds the intervention substrate needed before the unified M14-G router may
make live value-of-compute decisions.

It does **not** route compute from the new calibration, does **not** change the
outward chess move, and does **not** establish correctness, Elo, playing
strength, or strategic utility.

The central causal distinction is:

- the older PR #23 family compares **separately executed complete VERIFY runs**
  at different budgets;
- M14-G1 measures the action a future live router can actually buy after seeing
  base evidence: **run one fresh second VERIFY search on the same managed
  process, over the same candidate universe, at the declared extension budget**.

Those are different interventions and therefore use different artifacts,
extractors, and model identities.

## Intervention

The only M14-G1 extension intervention is:

`same_process_staged_verify_v1`

For every eligible generation:

1. EXPLORE finishes cleanly for Stockfish, Reckless, and LC0.
2. Existing VERIFY v1 nominates the owner-ordered three-candidate union.
3. All three base VERIFY searches complete cleanly.
4. If the anchor decision boundary is still open, each same managed solver
   process receives a new restricted `go` over the **identical** three
   candidates.
5. The extension uses a strictly larger node limit than the base round.
6. Every extension dispatch requires a fresh specialist resource reservation.
7. Base and extension process consumption are measured as separate resource
   stages.
8. The extension is written to a separate
   `staged_verification/manifest.json` artifact and never changes existing
   VERIFY v1 semantics.

A 64-node base followed by a 128-node extension means:

```
go nodes 64 searchmoves <same-three-candidates>
# clean completion barrier
go nodes 128 searchmoves <same-three-candidates>
```

It does **not** mean that 64 extra nodes are treated as mathematically
equivalent to a separate 128-node run. Same-process cache / TT state may be
inherited. That inheritance is part of the declared intervention and is why
this model family remains separate from PR #23.

## Authority firewalls

M14-G1 preserves three distinct authorities:

- **resource authority** — the active router may reserve enough budget for a
  configured staged research dispatch;
- **routing-value evidence** — the offline calibration may estimate whether the
  declared extension changes the frozen decision policy;
- **move authority** — unchanged. Staged VERIFY carries none.

Runtime configuration rejects combining a staged extension with
`hybrid_authority`. The M14-C active move-authority path therefore cannot
silently consume M14-G1 experimental work.

## Artifacts

Existing base VERIFY remains:

`verification/manifest.json`

The extension is:

`staged_verification/manifest.json`

The staged manifest binds:

- parent run id and parent manifest hash;
- base VERIFY id and manifest hash;
- position and generation;
- identical nominees and candidate universe;
- identical solver-process identities;
- base and extension dispatch limits;
- extension stage commands and terminal bestmoves;
- extension telemetry streams;
- explicit `routing: false` and `outward_move: false` authority declarations.

Integrity verification additionally requires the entire base round to complete
before the first extension dispatch timestamp.

## Decision policy reuse

The staged label does not implement a second chess policy.

`controller.decision.evaluate_unanimous_verify_policy()` is the shared policy
primitive used by both:

- the existing counterfactual decision path; and
- M14-G1 base/extension label construction.

That prevents a future policy change from creating two superficially similar
but semantically different decision labels.

## Past-only feature surface

`controller/staged_value_of_compute.py` builds
`staged-verify-decision-change-v1` rows.

The feature digest contains only facts available after the base round:

- intervention and budget transition identity;
- base candidate universe and owner nominations;
- base terminal decision state;
- per-verifier observation count;
- leader flips;
- stable-run fraction;
- PV persistence;
- self-retention;
- elapsed stage time;
- native work value with native semantics tag.

The extension terminal vector, extension decision, and all
`decision_changed` labels are excluded from the feature payload.

Changing future extension evidence must leave the base feature digest unchanged.

## Labels

The extension labels are descriptive:

- `decision_changed`;
- `proposal_emerged`;
- `proposal_disappeared`;
- `proposal_move_changed`;
- `terminal_vector_changed`.

A changed decision is not called better, correct, stronger, or an Elo gain.

## Calibration

`controller/staged_decision_calibration.py` defines:

`bucketed_staged_verify_decision_change_v1`

The model estimates only:

> P(the declared same-process staged VERIFY extension changes the shared frozen
> unanimous-VERIFY policy result | base-round past-only evidence)

Position groups, not individual repeated runs, are the independence unit for
train/calibration/holdout splitting and support.

Serve-time evaluation fails closed when:

- the bucket is unseen;
- row support is below the configured minimum; or
- independent position-group support is below the configured minimum.

The model artifact is content-addressed and separately hash-binds its held-out
evaluation.

## Runtime configuration

The validation profile is:

`config/allfather.staged-verify.validation.json`

The relevant block is:

```json
{
  "verification": {
    "enabled": true,
    "nomination_method": "owner_bestmove_union_v1",
    "dispatch_limit": {"nodes": 64},
    "staged_extension": {
      "enabled": true,
      "intervention": "same_process_staged_verify_v1",
      "dispatch_limit": {"nodes": 128}
    }
  }
}
```

The extension node limit must be strictly greater than the base node limit.

## Tooling

Fast regressions:

```bash
make staged-verification-tests
make staged-value-of-compute-tests
make staged-decision-calibration-tests
```

Real-engine mechanism contract:

```bash
make staged-verify-contract
```

Run the research profile:

```bash
make run-allfather-staged-verify
```

Build an offline dataset from already-sealed runs:

```bash
make staged-value-of-compute-sweep RUNS='run-a run-b run-c'
```

Fit the staged model:

```bash
make staged-decision-calibration DATASET=build/staged-value-of-compute/<id>/dataset.json
```

The sweep and fitting tools never start an engine.

## Promotion boundary

M14-G1 is complete when the repo can prove:

- existing VERIFY v1 behavior remains unchanged;
- the extension cannot start before clean base completion;
- candidate roots and solver identities cannot change between rounds;
- anchor completion blocks undispatched extension work;
- each extension dispatch receives fresh resource authorization;
- base and extension physical measurements remain distinct;
- future extension evidence cannot leak into base features;
- repeated copies of one position do not create independent support;
- unsupported calibration buckets fail closed;
- no staged evidence can acquire move authority.

M14-G2 now consumes this qualified substrate through `unified_value_v1`.
The staged model may license skipping only when its exact serving bucket has
held-out support and the M14-F regime-support bucket is in-domain; otherwise the
router buys the extension fail-closed, subject to the existing resource gate.
