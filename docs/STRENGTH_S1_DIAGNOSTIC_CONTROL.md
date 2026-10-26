# S1 — G3 output-only diagnostic control

**Code status:** An opt-in output-only mechanism with source-locked derivation,
not yet a completed/qualified G3 control match. Source: original G3 v2 profile
under the exact Git blob recorded in
qualification/strength/g3-output-control-v1.json.

## Why this exists

The 16-game pilot showed 140 move-changing hybrid interventions and a
0W/4D/12L result against a single-process Stockfish anchor under equal chess
clocks, not equal total compute. To attribute the source of the losses, we
must compare G3 against an otherwise identical G3 that **computes every
proposal, applies the same decision authorization and accounts resources,
but publishes the Stockfish anchor move**.

It is not correct to substitute the already existing J12
anchor_control_v1: that mode is restricted to the J12 orchestrated policy.

## Runtime implementation

- New explicit outward mode: g3_anchor_control_v1, valid only for
  clocked_staged_preanchor_v1.
- controller/runtime.py rejects this mode for J12 and older M14-C.
  The original anchor_control_v1 J12-only condition is unchanged.
- controller/decision.py uses the existing output-only FinalDecision selection
  and records ANCHOR_CONTROL; the proposal, its evidence digest, safety
  DecisionAuthorization, and authorization snapshot are not recalculated or
  weakened. Only the outward bestmove differs from active G3. Existing
  legacy objects and digest encodings are unchanged.
- tools/local_game/common.py derives the diagnostic configuration from the
  exact original source with ONLY root, replay-root and outward-mode changes.
  Every other field is compared by the existing source-derived verifier.
- tools/local_game/validate.py accepts the distinct diagnostic participant
  name and verifies published output against sealed G3 replay and clock
  evidence, without hiding an engine timeout or returning fabricated moves.
- tests/controller/test_g3_diagnostic_control.py checks proposal/authorization
  equality, same authority snapshots, G3/J12 policy isolation, exact
  derived config and mutation rejection.

## Scientific acceptance still open

This commit qualifies **only the source-level control mechanism**. The
following are needed before S1 as a scientific experiment is complete:

1. Whole-participant cgroup v2 CPU and RSS accounting: all engine descendants,
   Python/controller, startup/warmup, between moves, logging and cleanup.
   Unavailable metrics are unavailable, never zero.
2. Source-locked diagnostic Fastchess campaign with G3_FULL_HYBRID and
   G3_FULL_ANCHOR arms, identical fixed inputs/clock/compute admission.
   Reversed colors and new retained PGNs, UCI sessions, replay joins.
3. Distinct native Stockfish and G3 clock-only controls to attribute timing
   translation, observation work and move replacement separately.
4. An independent verifier of both input signatures and full participants;
   no positive strength claims from a four-game smoke or unit tests.
5. Later: S2–S4 native-clock incumbent tracking and controlled challenger
   comparison; S5 real value-of-computation; S8 untouched matches.

This candidate does not allow Lichess credentials, public distribution,
playing-strength qualification, model-based move replacement or non-G3 mode
promotion.
