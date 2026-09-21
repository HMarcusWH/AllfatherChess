# Explicit VERIFY evidence plane

## Status

Implemented as shadow-mode research instrumentation.

- EXPLORE remains pairwise-disjoint under `RootShardLedger`.
- COMPARE v1 is deterministic nomination only: the three completed EXPLORE
  bestmoves, in owner order, become the common candidate set.
- VERIFY lets all three shadow processes re-search that same set.
- Raw VERIFY execution remains unchanged; offline COMPARE / descriptive RELOCK
  analysis is implemented in `controller/verification_analysis.py`.
- VERIFY cannot run in `mode: active` until its compute is charged through
  `BudgetLedger`.
- Stockfish anchor remains the sole outward bestmove authority.

## Runtime and ownership contract

For a clean three-owner EXPLORE run, pairwise-disjoint ownership makes the three
valid owner bestmoves distinct. VERIFY therefore uses exactly
`V=(m_stockfish,m_reckless,m_lc0)` and sends that same `searchmoves` set to
all three shadow processes.

`controller.shadow.ShadowRunCoordinator` remains the sole process-lifecycle
coordinator: it owns the generation barrier, telemetry observer, exit handler,
cancellation/quiescence and the lock shared with anchor completion.
`controller/verification.py` owns the VERIFY plan, stage/artifact model,
deterministic nomination and integrity checks.

No new VERIFY stage begins after the outward decision. An already-running stage
follows `shadow.on_anchor_complete` (`drain` or `cancel`).

## Raw artifact

Replay v1 remains EXPLORE evidence. Deliberate overlap is stored separately:

```text
<replay_root>/<run_id>/
    manifest.json
    stockfish-anchor.jsonl
    stockfish-shadow.jsonl
    reckless-shadow.jsonl
    lc0-shadow.jsonl
    verification/
        manifest.json
        stockfish-shadow.jsonl
        reckless-shadow.jsonl
        lc0-shadow.jsonl
```

The verification manifest contains only orchestration facts and hash-binds the
finalized parent replay manifest. VERIFY streams remain telemetry v1 with
`phase = VERIFY` and `decision_authority = false`.

`verify_verification_integrity()` checks parent provenance, stream hashes and
sizes, the exact common request, and candidate/PV-head/bestmove containment.

## Claim boundary

The raw VERIFY milestone does not establish that agreement is correct, that
disagreement predicts error, that VERIFY is worth its compute, that
random/backend-light LC0 is strength-qualified, or that Allfather is stronger
than Stockfish. The derived definitions and artifact format are specified in
`docs/COMPARE_RELOCK.md`.
