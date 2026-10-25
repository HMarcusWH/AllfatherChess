# META-1 J13 — real-host matched-authority campaign runbook

**Program state (25 October 2026):** Experiment *infrastructure* is merged
in [PR #60](https://github.com/HMarcusWH/AllfatherChess/pull/60) and
[PR #61](https://github.com/HMarcusWH/AllfatherChess/pull/61), including
the ANCHOR_CONTROL arm, immutable attempts, paired opening report,
pre/post domain binding and independent requalification. A real 100-game
confirmatory `workflow_dispatch` attempt has **not** been run/qualified.

This runbook documents a future manual qualified-host experiment. It does not
make the host qualified, dispatch a run, grant Lichess credentials or
change the frozen policy in `qualification/meta-1-v1.json`.

## 1. Frozen experimental question and design

**Question:** holding J12 orchestration/resource policy matched, what changes
when permitted outward HYBRID move authority is allowed versus deliberately
publishing the Stockfish anchor?

| Frozen field | Value |
| --- | --- |
| Policy | `qualification/meta-1-v1.json` |
| Source runtime | `config/allfather.orchestrated-v1.validation.json` |
| Engine bundle | `build/online-engine-opt-v2` |
| Openings | `tests/fixtures/meta1/openings-v1.pgn`, exact committed SHA256 |
| Pairing | 50 opening blocks, reversed colors |
| Games | 100, serial (`concurrency=1`) |
| Clock | `0:30+1` |
| Arms | `allfather-orchestrated` vs `allfather-anchor-control` |
| Recording | One source SHA and GitHub run/attempt, immutable raw artifact first |
| Analysis | Descriptive matched opening blocks, interventions/suppressions, WorkGrant CPU and response resource metrics |
| Forbidden claims | `same_compute_claim=false`, no result/Elo/SPRT gate, no superiority or deployment claim |

`ANCHOR_CONTROL` is an **experimental output-mode ablation** of the
same J12 execution path, not a second/cheaper allocator. Different subsequent
board trajectories can still lead to different realized CPU costs; matching
resource governance does not mean identical realized compute.

## 2. Before running on a qualified physical host

1. Confirm the campaign source is the intended merged `main` commit,
   with an immutable candidate bundle and pinned Fastchess source binary.
2. Run J12 real-orchestration authority qualification:
   `scripts/qualify-resource-orchestration.py --record-disposition`
   with source-bound execution domain.
3. Inspect `build/test-results/engine-opt-v2-profile-domain/j12/report.json`:
   `authority_qualified=true` and
   `synthetic_capacity_observation=false` are mandatory for a real attempt.
   Do not relabel `NOT_QUALIFIED_HOST_CAPACITY` as a pass.
4. Confirm complete real HostCapabilities and no unknown domain,
   insufficient CPU/RSS envelope, synthetic capacity or changed engine options.
5. Freeze the 50-opening fixture, two arms, source runtime, CPU topology,
   clock/seed and primary paired descriptive reporting before game 1.

The runner and independent verifier reject a changed campaign ID,
incomplete schedule, repeated attempts, new runtime, missing host preflight
or altered J12 qualification digest.

## 3. Controlled manual GitHub workflow

Use [.github/workflows/meta-1.yml](../.github/workflows/meta-1.yml).
The `campaign` job is only admitted on **workflow_dispatch**; the
source requirement is `GITHUB_REF=refs/heads/main`. Supply the label of an
actually J12-authority-qualified runner host (not merely the default
GitHub-hosted `ubuntu-24.04`). A main push or PR does not silently launch
the 100-game campaign.

The workflow builds ENGINE-OPT-V2 and pinned Fastchess at the exact merged
commit, validates the host domain, captures `meta1-preflight/preflight.json`,
and executes:

```text
python3 -m tools.meta1.runner \
  --j12-report build/test-results/engine-opt-v2-profile-domain/j12/report.json \
  --preflight build/test-results/meta1-preflight/preflight.json \
  --output "build/test-results/meta1/<source-prefix>-<run-id>-<attempt>"
```

That final directory must not already exist. A new GitHub run attempt creates
a distinct immutable identity; never append to an incomplete campaign.

## 4. Independent acceptance and negative dispositions

The producer uploads `meta1-raw-<run-id>-<attempt>` with source, Fastchess,
bundle, J12, host preflight and per-game raw records, **before** any
independent analysis. The separate `independent-verify` job restores this
artifact and runs:

```text
python3 scripts/qualify-meta-1.py \
  --campaign "build/test-results/meta1/<campaign-id>" \
  --output-report "build/test-results/meta1/<campaign-id>/report-independent.json"
```

Require complete 50 paired blocks, exactly 100 games, reversed colors,
verified game/ply/replay/resource identities, stable postflight qualification
domain, no producer-authored qualification shortcuts, and the frozen
intervention/suppression analysis. Missing games, host drift or failed
retained move evidence must remain NOT QUALIFIED, with raw diagnostic
artifacts preserved. The four-game mini-e2e is a wiring test only.

## 5. How to interpret the report

Interpret effect by opening pair and by eligible HYBRID intervention
opportunities; report W/D/L, response and total resource telemetry, fallback,
suppression and incomplete evidence, including confidence/uncertainty where
supported. A strong or weak descriptive result is **not** a trigger to
change the frozen hypotheses after looking at outcomes.

If the real campaign is independently qualified, freeze its exact raw
artifact and report as J13 evidence. J14 still requires a separate
production-profile comparison. [M15-B/C](ROADMAP.md) requires a different
predeclared *equal-total-resource* experiment against each direct constituent.
This runbook supplies no Elo, SPRT, victory or superiority promotion.

References: [J12 plan](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md),
[claim ledger](CLAIM_LEDGER.md), [current status](CURRENT_STATUS.md),
`tools/meta1/preflight.py`, `tools/meta1/runner.py`,
`tools/meta1/paired.py`, and `qualification/meta-1-v1.json`.
