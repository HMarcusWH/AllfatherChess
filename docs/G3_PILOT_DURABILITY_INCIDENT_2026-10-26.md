# G3 pilot: retained durable-I/O wall-envelope incident (26 October 2026)

**Status:** Negative evidence preserved. This document and `tools/strength_lab/g3_io_forensics.py` confer **no** chess, resource, deployment, distribution or live Bot API authority.

## Source and result boundary

The successful exploratory run [40216238892](https://github.com/HMarcusWH/AllfatherChess/actions/runs/40216238892), artifact `12609439308`, ran 16 games, G3 0W/4D/12L against the single Stockfish anchor. It was not a matched-compute or Elo experiment. The source tree had 993 measured G3 replays. Resource persistence median/p95/p99/max: **0.799 / 1.000 / 2.221 / 4.978 ms**, with no wall denials.

The independent S0 rerun [40361350837](https://github.com/HMarcusWH/AllfatherChess/actions/runs/40361350837), artifact `12672732421`, retained eight completed Fastchess blocks but **failed** replay qualification; it has **no qualified match result**. Of 889 measured G3 replays, one failed the strict full-run wall claim. Median/p95/p99/max resource persistence: **0.715 / 27.424 / 133.384 / 539.635 ms**, including 18 writes above 100 ms. These are different observed attempt populations, not matched game outcomes.

The failing replay is `20261026T171358480563Z-g000131-10a3472e` in `g3-pilot-02`. From the original sealed files:

| Observed phase | Milliseconds from external `go` |
| --- | ---: |
| Outward `bestmove` publication | 1162.977327 |
| Process endpoints frozen | 1164.155 |
| Resource interval frozen | 1164.400 |
| Serialization | 0.409 additional |
| Durable resource write | **539.635 additional** |
| Resource persistence complete | 1704.444 |
| Terminal route budget snapshot | **1704.588** |
| Declared wall envelope | **1512.000** |
| Denied overrun | **192.588** |

`output_within_deadline=true`, resource CPU was complete and qualified, but the independent v1 claim is `wall_within_envelope=false`, `claimed=false`. In `controller/routing.py`, the terminal wall snapshot comes **after** `controller/resource_measurement.py` has durably persisted `resource.json`. The observed 539.635 ms write is therefore counted in the full-run v1 envelope. The denial is correct; a timely `bestmove` does not override it.

This is a **durable-storage tail observation**, not proof of a particular operating-system cause. The older [23 October incident note](LOCAL1_RESOURCE_INCIDENT_2026-10-23.md) independently retains a 677.229 ms write tail. File-sync/atomic replace must not be disabled, and the original wall threshold must not be widened to turn this attempt green.

## Reproduce the historical evidence (diagnostic only)

Download each raw artifact from the GitHub run, then execute from repo root:

```bash
python3 -m tools.strength_lab.g3_io_forensics \
  --input /path/to/g3-pilot-raw-40361350837-1.zip \
  --expected-sha256 30987beea5deb737ceef2859720a91a5fcd29864abca71b8d7b7d94b5276ce7b \
  --expected-source-commit afc7cb40243c985f91c9f9638cabe7edc261a28b \
  --output /tmp/g3-io-incident.json

python3 -m tools.strength_lab.g3_io_forensics \
  --input /path/to/g3-pilot-raw-40216238892-1.zip \
  --expected-sha256 9be9eb650689916847f1fa61f7835f3c7c3a6d4b399070cf03d454435fee68f6 \
  --expected-source-commit 796228796c098df88ad406d595dda4c6db3d5848 \
  --output /tmp/g3-io-historical.json
```

The inspector checks ZIP/member bounds, source SHA, duplicate keys, replay IDs, triplet integrity, wall-flag arithmetic and seal phases. It returns only `diagnostic_only=true`, `qualifies=false`, and `qualified_games=null`. It does not independently requalify PGNs, clock authority, release or resource certificates. Each new failed campaign additionally retains its own attempt-specific forensic index when possible; failure to write an index does not improve qualification.

## Required next actions

1. Run one predeclared *unchanged-source* retry of the original failed `exploratory-games` job with its own GitHub attempt identity; keep both artifacts even if the new attempt passes. Do not retry in a loop.
2. Review exact-source game, UCI, integrity and time results; obtain fresh complete green checks on the eventual S0 PR head before merging.
3. Design timing-contract separation (`bestmove` clock / measurement freeze / persistence) as a *separate, opt-in versioned protocol*, not a reinterpretation of v1. Define the durable completion of **all** artifacts, including `route.json` and replay manifests.
4. Keep PR #84 S1's output-only ablation and all public/live/deployment denials unchanged until S0 ancestry is resolved and S1b matched-resource experiments are completed.

`qualification/strength/negative-g3-durable-tail-20261026-v1.json` pins the immutable source, archive and failure identities. The negative event remains negative after any later green CI attempt.
