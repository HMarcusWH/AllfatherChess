# G3-v2 versus Stockfish anchor — first offline chess-result pilot

**Pre-registered source experiment, 25 October 2026. Not META-1. No Lichess
token, account, network Bot API, J12 authority promotion, Elo, equal-compute
or public release authorization.**

This is a small *exploratory* direct Fastchess head-to-head, rather than another
release-assurance dependency test. It uses the already qualified
\`config/allfather.online-hybrid-v2.validation.json\` and source-bound
real-network BLAS-LC0 b4 constituent bundle. Both sides play the same
Fastchess tournament clock \`0:30+1\`, in color-reversed opening pairs.

## What is frozen in the repository

- \`qualification/g3-pilot-v1.json\`: exactly **8** opening blocks by zero-based
  index \`[0,6,12,18,24,30,36,42]\` from the source-hash-bound 50-opening
  \`tests/fixtures/meta1/openings-v1.pgn\`. The subset was selected before
  outcomes were observed, not in response to games.
- **16 natural-rule games**, two per opening, colors reversed. Serial,
  one Fastchess job at a time, 30+1 clock, no adjudication by engine score.
- \`allfather-g3\`: the already-qualified clocked G3 hybrid via the existing
  \`tools.local_game.proxy\` and pinned engine/network bundle.
- \`allfather-anchor\`: one Stockfish process in the legacy anchor wrapper.
  **This arm does not incur the J12 matched orchestration cost.**
- Each complete game has PGN, session UCI logs, resource observations, and G3
  decision/replay evidence. A post-run source-bound verifier reopens those
  materials, checks legally parsed PGN against actual played UCI moves and
  every sealed G3 decision/resource claim, and refuses missed clocks,
  abnormal termination or missing evidence.

## Execution and retained outcomes

\`.github/workflows/g3-pilot.yml\` runs on pull requests changing the frozen
pilot files (and can be manually launched later). The source job freezes
policy/denials; the Ubuntu 22.04 job runs pinned Fastchess source tests;
the Ubuntu 24.04 build job compiles the actual b4 engine bundle; the final
job plays eight paired openings through real engines.

The output is at
\`build/test-results/g3-pilot/<exact-SHA-prefix>-<workflow-id>-<attempt>/\`
with raw games/trace/replay, \`manifest.json\` and \`report.json\`. A job failure
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
\`HYBRID\` versus \`ANCHOR_CONTROL\`, 50 opening pairs / 100 games, but only
on a real J12-authority-qualified host with a separately independently
verified experiment. M15-B/C remains the formal equal-resource match program.

See [META-1 runbook](META_1_EXECUTION_RUNBOOK.md),
[full-game qualification](FULL_GAME_QUALIFICATION.md),
[claims](CLAIM_LEDGER.md), and
[roadmap](ROADMAP.md).
