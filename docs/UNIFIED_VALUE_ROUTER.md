# M14-G2 — Unified value-of-compute decision router

## Status and claim boundary

M14-G2 wires the M14-G1 same-process staged VERIFY calibration and the M14-F
structural regime support model into one live route choice:

> after a clean base VERIFY round, should the controller buy the configured
> staged VERIFY extension or stop spending specialist compute at the base round?

This is a **routing** decision only. It does not itself reserve resources and it
never authorizes an outward chess move.

The authority chain remains:

```text
base VERIFY evidence
        |
        v
unified_value_v1 route
        |
        +--> SKIP_STAGED_VERIFY
        |
        +--> BUY_STAGED_VERIFY
                  |
                  v
        existing authorize_specialist()
                  |
             reserve / deny
                  |
                  v
             dispatch
```

If a completed staged extension exists, its terminal bestmoves become the
terminal source for the frozen counterfactual `unanimous_verify_v1` decision.
That still does not grant move authority. M14-C `DecisionAuthorization`
remains a separate gate, and the current G2 validation profile does not enable
`hybrid_authority`.

No Elo, move-quality, correctness, optimal-routing, or equal-resource strength
claim is introduced.

## Why the shortcut is the thing that needs evidence

M14-G1 established a conservative staged model:

`bucketed_staged_verify_decision_change_v1`

It estimates:

```text
P(the declared same-process staged VERIFY extension changes
  the frozen unanimous-VERIFY decision | base-round past-only evidence)
```

Buying the extension is the conservative branch. Skipping it asserts that the
extra compute is unlikely to change the decision. Therefore missing evidence
must never be interpreted as permission to skip.

The G2 policy consequently fails closed:

```text
missing model
OR unseen / under-supported staged bucket
OR staged bucket not observed on holdout
OR change probability above threshold
OR missing regime support
OR regime out-of-domain
        |
        v
BUY_STAGED_VERIFY
```

The existing resource authority may still deny the requested extension when the
verification reserve or wall envelope is exhausted.

## Live inputs

The live router consumes only evidence available after the clean base VERIFY
barrier.

For the staged decision-change model it reconstructs the exact serving schema:

```text
transition
base_decision_disposition
min_observation_count
max_leader_flips
min_stable_run_fraction
```

The transition is the declared same-process node step, for example:

```text
same-process:n64->n128
```

The terminal base decision is evaluated by the same
`evaluate_unanimous_verify_policy()` primitive used by the counterfactual and
G1 dataset paths.

For the regime layer the live router reuses the M14-F vocabulary and support
model. It derives:

- VERIFY terminal pattern;
- terminal RELOCK state;
- verifier observation / flip / stability / PV-persistence features;
- source-native mate alarms;
- request mode.

The G2 staged route occurs before REFINE, so its live regime observation records
REFINE as absent rather than pretending future refinement evidence already
exists.

## Skip gates

`SKIP_STAGED_VERIFY` is licensed only when all of these pass:

1. a staged decision-change model is loaded;
2. the staged model says the current serving bucket is in-domain;
3. the exact serving bucket has at least one held-out, in-domain observation in
   the model's evaluation record;
4. predicted decision-change probability is at or below
   `routing.staged_skip_max_change_probability`;
5. a regime support model is loaded;
6. the current regime support bucket is in-domain;
7. the M14-F `OUT_OF_DOMAIN` status is inactive.

Any failed gate produces `BUY_STAGED_VERIFY`.

The held-out-bucket gate is deliberately stricter than merely requiring a
global holdout score. A model that has held-out rows somewhere else has not
validated the exact shortcut it is serving here.

## Route artifact

The existing `route.json` remains the controller resource/routing audit. G2
adds a separate `value_decisions` array to that artifact. Each record contains:

- action and reason;
- base-to-extension transition;
- exact serving features;
- staged estimate;
- structural regime classification;
- all skip gates;
- staged/regime model identities;
- explicit authority declaration.

The declaration is always:

```json
{
  "routing": true,
  "resource": false,
  "outward_move": false
}
```

The existing `specialist_actions` records remain the authoritative reserve /
settle audit for actual VERIFY dispatches.

## Counterfactual terminal source

Before G2 the frozen counterfactual decision always read terminal bestmoves from
`verification/manifest.json`.

G2 preserves that behavior when the extension is skipped or unavailable. When a
staged extension completes cleanly, the decision uses:

`staged_verification/manifest.json`

as its terminal source while retaining the same base VERIFY candidate universe
and verification identity.

The sealed `decision/counterfactual.json` records
`decision_terminal_source` and hash-binds the staged manifest when applicable.
Deterministic replay verifies that source before rebuilding the decision.

This is evidence updating, not authority transfer.

## Configuration

The validation profile is:

`config/allfather.unified-value.validation.json`

Its routing block uses:

```json
{
  "policy": "unified_value_v1",
  "staged_decision_calibration": null,
  "regime_support_calibration": null,
  "staged_skip_max_change_probability": 0.10
}
```

The shipped validation profile intentionally has null model paths. Therefore it
must fail closed to `BUY_STAGED_VERIFY`; it exists to qualify the wiring and
authority boundaries, not to smuggle a toy fitted model into production logic.

A research/qualification operator may point the two calibration fields at
content-addressed model artifacts produced by the existing M14-G1 and M14-F
tooling.

## Tests and real-engine contract

Fast unit tests:

```bash
make unified-value-router-tests
```

Real-engine mechanism contract:

```bash
make unified-value-router-contract
```

Run the validation profile:

```bash
make run-allfather-unified-value
```

The real-engine contract requires:

- exactly one live unified staged route decision;
- the null-model profile chooses `BUY_STAGED_VERIFY`;
- the route decision has no resource or outward-move authority;
- all three extension searches receive separate existing resource
  authorizations;
- base and extension physical resource phases remain distinct;
- the extension completes over the G1 same-process/same-candidate contract;
- the completed extension becomes the counterfactual terminal source;
- the outward move remains the Stockfish anchor.

## Promotion boundary

M14-G2 establishes the controller seam required to use validated value-of-compute
evidence in live routing.

It does **not** establish that the current staged model should be deployed on a
strength platform, that the threshold is optimal, that decision change equals
decision improvement, or that Allfather is stronger than a constituent engine.

Those claims remain downstream of the governed policy-evolution and strength
campaign milestones.
