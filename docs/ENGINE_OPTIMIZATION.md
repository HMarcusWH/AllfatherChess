# ENGINE-OPT-V2

ENGINE-OPT-V2 is the controlled optimization programme inserted between qualified LOCAL-1 v1 and ONLINE-3.

The v1 composition remains the control. This programme does **not** rewrite the already-qualified v1 runtime or claim that LOCAL-1 evidence transfers to a new binary/profile.

## Initial defect exposed by LOCAL-1

The frozen v1 LC0 CPU reference uses BLAS with one BLAS thread, `NNCacheSize=0`, `MinibatchSize=32`, and implicit fixed prefetch. LOCAL-1 showed that LC0 was far more expensive than the alpha-beta specialist stages and frequently failed to complete before the authority boundary. The standalone five-arm matrix was lifecycle/descriptive evidence, not an equal-resource strength campaign.

## Repair strategy

1. Freeze source and benchmark corpus before changing behavior.
2. Compare current derived engines with their declared upstream controls where practical.
3. Repair conventional LC0 CPU configuration before enabling adaptive logic.
4. Benchmark portable build optimizations for Stockfish and Reckless without changing their search algorithms.
5. Preserve the qualified n16 EXPLORE, n16 VERIFY, n32 staged-VERIFY intervention identity in v2.
6. Specialize UCI options by phase only where the controller can prove idle reconfiguration.
7. Use owner-specific resource estimates instead of treating native node counters as a common compute currency.
8. Requalify the selected v2 composition from constituent inference through full-game lifecycle before ONLINE-3.

## Claim boundary

No benchmark row is an Elo or superiority result. GPU promotion, hard NN-evaluation suppression, upstream vendor refreshes, and new Stockfish/Reckless pruning algorithms are outside this PR.

## LC0 research-option visibility

The Allfather LC0 additions are intentionally marked LC0 `kProOnly`. The optimized v2 profile therefore launches LC0 with `--show-hidden` so `AdaptivePrefetch` and `DefectTelemetry` are explicitly advertised at the UCI boundary. This changes option visibility only; the selected profile still keeps both features disabled until measurement supports promotion.


## Frozen measurement decision

Workflow run `36363702414` produced the first complete optimization evidence set at
source `72fe32567591804061b0489b1bb73743dc0f3c9d`. Artifact identities, per-report
SHA-256 hashes, extracted measurements and the selected profile are frozen in
`qualification/engine-opt-v2-evidence.json`.

The LC0 diagnosis was decisive, but the first selection was not repeatable enough. At n16
on the real 791556 BLAS network, the v1-style
`NNCacheSize=0 / MinibatchSize=32 / MaxPrefetch=32` profile measured **1250.602 ms**
median wall time. The first choice,
`NNCacheSize=262144 / MinibatchSize=0 / MaxPrefetch=0`, measured **272.053 ms**
median and **301.016 ms** maximum and initially matched the v1 bestmove vector.

The exact-head rerun on workflow `36366449343` exposed a host-sensitive batch-boundary
failure in that choice: the rook-endgame case changed from `f2f3` to `h2h3` while the
v1 control remained `f2f3`. The aggregate correctly rejected promotion.

The revised candidate is the cross-run-stable
`NNCacheSize=262144 / MinibatchSize=7 / MaxPrefetch=8` profile with a 64-node startup
warmup. It preserved the complete 8/8 v1 bestmove vector on both the original measurement
run and the failed exact-head run, with median wall times **337.053 ms** and **329.912 ms**
and maxima **448.091 ms** and **440.476 ms** respectively. The workflow now repeats both
the v1 baseline and the selected profile three times on the exact candidate head before
promotion. Additional cold minibatch candidates remain in the matrix so a future no-warmup
profile can replace this one only with measured repeatability evidence.

The 2M-entry cache remained slightly faster in one warm lane but used materially more
memory, so the 256k cache remains on the CPU specialist efficiency frontier.

Adaptive prefetch is **not** promoted. The measured adaptive 2M-cache lane remained around
1074 ms median at n16. This does not reject the research idea; it establishes that the
conventional profile must be fixed before judging any adaptive search policy.

The constituent A/B answered the PR #32 regression question: the derived Allfather LC0 and
the pristine pre-Allfather LC0 base had **100% bestmove agreement** at n64 and essentially
identical runtime (766.932 versus 764.217 ms median wall). No material disabled-feature
regression was detected. Reckless likewise showed 100% bestmove agreement against its
pristine source control.

Portable Stockfish PGO measured 584.329 ms versus 595.832 ms for the same-source non-PGO
build and is selected. Stockfish hash differences were sub-1% and therefore not promoted
without strength evidence; both Stockfish and Reckless retain 16 MiB hash.

The revised candidate uses owner-specific provisional reservations of **600 ms for LC0
EXPLORE** and **800 ms for LC0 VERIFY/staged VERIFY**. These are deliberately conservative
until the revised profile is exercised by the fresh exact-head G3-v2 run. The aggregate
now reads the retained physical resource reports and refuses promotion if measured LC0
stage CPU exceeds either selected reservation.

The frozen measurement run is the **selection basis**, not the final exact-head qualification:
subsequent commits apply the chosen profile and harden phase/resource dispatch ordering.
The ENGINE-OPT workflow therefore re-runs the LC0 matrix, constituent A/B, real G3-v2
authority gate and LOCAL-1-v2 on the exact candidate head before the aggregate gate can pass.

Reckless PGO is deliberately not claimed as tested in this milestone. The vendored upstream
PGO target does not preserve the selected portable x86-64/no-default-features build contract,
so a reproducible portable Reckless PGO path remains a future build experiment.

The pre-selection G3-v2 run also produced a genuine non-anchor authority event:
`c2c4 -> g1f3` in the frozen queen-pawn case. That event remains diagnostic until the
final selected exact head reruns G3-v2 and LOCAL-1-v2.
