# G3-v2 versus Stockfish anchor — first offline chess-result pilot

## October 25 repair and evidence boundary

The original [PR #81 attempt 40200192045](https://github.com/HMarcusWH/AllfatherChess/actions/runs/40200192045)
ran all eight planned opening pairs and retained 16 normal draw PGNs. The
source-bound verifier **rejected** the campaign because the source checkout
acquired an untracked root-level `config.json`. No qualified score or
strength improvement follows from that attempt. The raw manifest additionally
records LC0 startup failure on every G3 replay, including exit 127 and broken
UCI pipes; accordingly, all 1,194 observed G3 decisions used
`ANCHOR_FALLBACK`, with no non-anchor move changes. These are **negative
engineering observations**, not evidence that hybrid selection has no value.

The repair keeps every frozen choice in
`qualification/g3-pilot-v1.json` unchanged.

- The game job installs the same native runtime prerequisite family as the
  successful LOCAL-1 runner rather than assuming the separate build job's
  `libprotobuf`/`libopenblas` libraries were transported.
- `tools/g3_pilot_preflight.py` checks real LC0 ELF dependencies, starts
  the source-bound b4 binary/weights, verifies `uciok`, `readyok`,
  observes the actual BLAS backend, and requires one legal terminal
  `go nodes 32` result. Failed preflight cannot start a tournament.
- Fastchess now executes with its current working directory set to the
  *individual absolute-path match directory*. The predeclared opening,
  proxy `--spec`, PGN, log and session paths remain absolute; temporary
  files such as Fastchess's default `config.json` stay under `build/`,
  not in the tracked source checkout.
- Source hygiene is checked **before and after each match block** using
  the repository's existing fail-closed `source_identity()`. Bounded
  diagnostics retain unexpected path, size and SHA256, but do not upload
  arbitrary untracked source bytes (which might contain secrets).
- Every source-bound G3 replay now contributes a backend-health, completed
  LC0 EXPLORE, three-family EXPLORE, VERIFY, pre-deadline proposal,
  authorization and actual anchor-change observation. Missing or failed
  backend startup makes the pilot **NOT QUALIFIED**. Zero proposals or
  overrides with otherwise healthy constituents remains an allowed
  *observed experimental outcome*.

This remains an **exploratory**, same-tournament-clock comparison against
a one-process Stockfish anchor, not the separate frozen
100-game matched-J12 `META-1` control. The original failed run is not
relabelled by a fresh successful campaign. No license, public Bot API,
deployment, Elo, equal-compute or strength authorization is granted.


**Pre-registered source experiment, 25 October 2026. Not META-1. No Lichess
token, account, network Bot API, J12 authority promotion, Elo, equal-compute
or public release authorization.**

This is a small *exploratory* direct Fastchess head-to-head, rather than another
release-assurance dependency test. It uses the already qualified
`config/allfather.online-hybrid-v2.validation.json` and source-bound
real-network BLAS-LC0 b4 constituent bundle. Both sides play the same
Fastchess tournament clock `0:30+1`, in color-reversed opening pairs.

## What is frozen in the repository

- `qualification/g3-pilot-v1.json`: exactly **8** opening blocks by zero-based
  index `[0,6,12,18,24,30,36,42]` from the source-hash-bound 50-opening
  `tests/fixtures/meta1/openings-v1.pgn`. The subset was selected before
  outcomes were observed, not in response to games.
- **16 natural-rule games**, two per opening, colors reversed. Serial,
  one Fastchess job at a time, 30+1 clock, no adjudication by engine score.
- `allfather-g3`: the already-qualified clocked G3 hybrid via the existing
  `tools.local_game.proxy` and pinned engine/network bundle.
- `allfather-anchor`: one Stockfish process in the legacy anchor wrapper.
  **This arm does not incur the J12 matched orchestration cost.**
- Each complete game has PGN, session UCI logs, resource observations, and G3
  decision/replay evidence. A post-run source-bound verifier reopens those
  materials, checks legally parsed PGN against actual played UCI moves and
  every sealed G3 decision/resource claim, and refuses missed clocks,
  abnormal termination or missing evidence.

## Execution and retained outcomes

`.github/workflows/g3-pilot.yml` runs on pull requests changing the frozen
pilot files (and can be manually launched later). The source job freezes
policy/denials; the Ubuntu 22.04 job runs pinned Fastchess source tests;
the Ubuntu 24.04 build job compiles the actual b4 engine bundle; the final
job plays eight paired openings through real engines.

The output is at
`build/test-results/g3-pilot/<exact-SHA-prefix>-<workflow-id>-<attempt>/`
with raw games/trace/replay, `manifest.json` and `report.json`. A job failure
retains evidence rather than restarting/replacing a loss. The report counts
W/D/L, paired scores, observed CPU/response times, and genuine G3
anchor-changing HYBRID interventions where sealed replay supports them. A
zero-override campaign remains *observed zero*, not a fabricated success.

The final match verifier runs in the **same CI job** against source-bound
artifacts. It does not claim independent third-party attestation. The four
workflow jobs qualify build/source process behavior but do not turn the
16-game descriptive outcomes into a significant Elo/Superiority claim.

## Interpretation and next experiments

The comparison has intentional confounding: G3 coordinates four solver
processes and is measured against the single-process anchor under the same
tournament clock, not equal resource usage. Performance can be affected by
search scheduling, CPU contention, controller overhead and actual move
selection. Sixteen games is too small for a useful superiority inference.

**J13 META-1 remains the next causal control:** same J12 orchestration,
`HYBRID` versus `ANCHOR_CONTROL`, 50 opening pairs / 100 games, but only
on a real J12-authority-qualified host with a separately independently
verified experiment. M15-B/C remains the formal equal-resource match program.

See [META-1 runbook](META_1_EXECUTION_RUNBOOK.md),
[full-game qualification](FULL_GAME_QUALIFICATION.md),
[claims](CLAIM_LEDGER.md), and
[roadmap](ROADMAP.md).
