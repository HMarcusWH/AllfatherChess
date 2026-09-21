# Theory → controller → code implementation map

## Purpose

This document is the audit trail required before the immediate controller stack
was written. It maps each borrowed architectural concept to a concrete Allfather
controller concept and then to a code object, test, or artifact.

It exists to make one rule enforceable: **architecture transfers, theorems do
not**. None of the source documents proves a chess-search property. Anything
this repository asserts about chess must be established by Allfather's own
tests, contracts, or experiments.

```text
defect motif            != chess theorem
disagreement            != certificate
stability               != proof of bestmove correctness
replay success          != Elo gain
routing implementation  != strength gain
```

## Source authorities

| Document | Authority scope in this repository |
| --- | --- |
| `Chess_engine_optimization.pdf` | chess domain; unresolved decision mass `D(x)`; speculative waste; telemetry-before-intervention; reversals; certificate-gated suppression; equal-compute validation |
| `racr_proofread.pdf` | routing/control; cheap observation; proposal vs authorization; trust regions; stop/zoom/refine/branch/switch/abstain; re-lock; audit memory; budget-aware computation |
| `ICW_NSG_Master_Single_Source_of_Truth_v1.0` | metacontrol/governance; Intuition/Creativity/Wisdom separation; Xi flight record; warnings are not authorization; fail-closed; evidence discipline |
| `Defect_Routed_Projection_Geometry_for_Event_Based_Vision_v0_2` | staged compute; cheap defect measurement before expensive specialists; mathematical residual vs downstream utility; adaptive feature acquisition; stop/refine/specialist/fallback |

## Map

### Phase A — measurement before intervention

| Theory concept | Source | Allfather concept | Code / test / artifact |
| --- | --- | --- | --- |
| "Freeze baseline + instrumentation" phase P0 | chess §9 | shadow observatory precedes any routing | `controller/shadow.py`, `config/allfather.shadow.validation.json` |
| Cheap observation nominates, it does not decide | RACR §4.1 | raw telemetry is evidence, never authority | `docs/SHADOW_EXECUTION.md` decision firewall; `tests/controller/test_shadow_runtime.py` |
| Full-compute anchor remains available as fallback | defect §12 (`fallback` = full-compute anchor, not refusal) | `stockfish-anchor` unrestricted, sole outward authority | `BackendManager.anchor`, `UciFrontend` |
| Staged route records what was executed | defect §10 | replay manifest records stages, ordering, dispositions | `controller/replay.py`, `docs/REPLAY_FORMAT.md` |
| Provenance / configuration hash / failure ledger | RACR §7.2, chess §10 | manifest carries binary, config, ledger, stream hashes | `ReplayManifest`, `tests/controller/test_replay.py` |
| Warnings are not authorization | ICW | a shadow failure is recorded, never an election | `BackendManager` authority vs observational health |

### Phase B — residual geometry, derived not raw

| Theory concept | Source | Allfather concept | Code / test / artifact |
| --- | --- | --- | --- |
| Base defect `d(X→Z)` vs refinement gain `g(X⇝Y;Z)` | defect §9 | absolute disagreement vs marginal value of more compute | `common/residuals.py`, `controller/replay_analysis.py` |
| "No argmin theorem" (Remark 9.1) | defect §9 | lowest disagreement is *not* the correct move | label names are descriptive: `later_leader_changed`, never `wrong` |
| Residuals are domain-specific and not interchangeable | RACR §8.2 | Stockfish cp, Reckless cp, LC0 ScoreType are separate scales | `ScaleMixingError`; `tests/controller/test_residuals.py` |
| Projection residual: update-then-project vs project-then-update | RACR §5.1 | decision at checkpoint vs decision at full budget | `counterfactual_labels()` |
| "Do not audit what algebra already certifies" | defect Remark 12.4 | ledger invariants are enforced structurally, not re-measured per event | `RootShardLedger` unchanged |
| Unresolved decision mass `D(x)` | chess §4 | within-engine unresolved candidate set at a checkpoint | `unresolved_set()` (rank/overlap based, scale-free) |
| Speculative waste | chess §3 | `work_after_stability_ratio` | `controller/residuals.py` |
| Derived artifacts must be traceable to raw input | RACR §7.4 | derived/calibration artifacts carry source run ids + hashes | `docs/RESIDUAL_CALIBRATION.md`, `controller/calibration.py` |
| Independent verification before re-lock | RACR §4-5 | three raw common-support VERIFY searches are compared offline; descriptive `terminal-suffix-v1` RELOCK requires complete three-way evidence and final unanimity | `controller/verification_analysis.py`, `docs/COMPARE_RELOCK.md` |

### Phase C — authorization, budget, fallback

| Theory concept | Source | Allfather concept | Code / test / artifact |
| --- | --- | --- | --- |
| Cheap observation → route proposal → acceptance control → audit memory | RACR §4 | `observe → propose → authorize → dispatch` | `controller/routing.py` four-stage pipeline |
| "Instability is not enough" | RACR §4.3 | a disagreement spike may NOMINATE compute; only a gate AUTHORIZES suppression | `RouteProposal` vs `RouteAuthorization` |
| `BranchAllowed = Rupture ∧ ReLock ∧ BudgetOK ∧ SafeSetOK` | RACR eq. (6) | every authorization conjoins calibration, support, budget and safety gates | `AdmissibilityGate` |
| Stop / zoom / branch / abstain | RACR §4.5 | `STOP_WORKER / EXTEND / HOLD / CONTINUE / FALLBACK_ANCHOR / ABSTAIN_BUY_COMPUTE` | `RouteAction` |
| Trust region shrinks under stress | RACR §5.2 | out-of-domain calibration → conservative action only | `CalibrationModel.evaluate().in_domain` |
| Audit certificate `H = (Γ, cost, residuals, certificates, thresholds, provenance, disposition)` | RACR §5.5 | `RouteDecision` audit record written to `route.json` | `controller/routing.py`, `docs/BUDGET_ROUTING.md` |
| Audit memory / Ξ unresolved stress | RACR §3.4, ICW | `RouteAudit` accumulates authorizations, denials, and stress across a run | `RouteAudit` |
| Conditional-compute law `E[C] = Σ_r q_r C_r` | defect Thm 10.1 | every stage — including diagnostics and controller overhead — is charged | `controller/budget.py` |
| Break-even against the anchor charges *all* route costs | defect Cor. 10.3 | controller overhead and verification reserve are inside `B` | `BudgetLedger.charge_controller_overhead()` |
| "Compute is not latency or memory" | defect Remark 10.4 | wall, CPU-ms and GPU-ms are tracked as separate dimensions | `ResourceEnvelope` |
| Abstention must be operationally meaningful | RACR §8.7 | abstain = buy more compute or fall back to anchor-heavy mode, never a no-op | `RouteAction.ABSTAIN_BUY_COMPUTE`, `FALLBACK_ANCHOR` |
| Controller overhead must be measured | RACR §8.6, chess §14 | overhead is charged with a monotonic clock, never free | `BudgetLedger.overhead` |
| Do not transfer numeric thresholds across domains | chess §14 | every threshold is declared in config, not inherited from ICW/NeRD | `config/allfather.active.validation.json` |

## Explicit non-transfers

The following were deliberately **not** implemented, because the source
mathematics does not apply to chess search without independent proof:

- no Weil/CCM defect theorem is used as a search bound;
- no hard certificate suppresses a chess candidate; the only suppression this
  PR can authorize is *stopping a shadow observation worker*, which cannot
  change the outward move;
- no cross-engine score calibration is applied to move selection;
- no re-lock condition is claimed to prove chess correctness or authorize a
  branch. The offline `terminal-suffix-v1` RELOCK descriptor records only
  complete three-way terminal convergence; the older `stable_to_end` label
  remains an engine-self-reversal label used by the separate calibration path.
