# Telemetry v1 backend mapping

This document maps evidence already exposed by the vendored engines into the common telemetry contract. PR #7 implements this mapping in `adapters/telemetry/` without modifying engine source.

## Shared UCI mapping

`common.telemetry.TelemetryStream` creates `search.started`, owns sequence numbers, and enforces monotonic adapter observation time. Backend-specific modules under `adapters/telemetry/` translate relevant UCI lines into raw events.

`info` lines with a PV become `candidate.update`. `bestmove` becomes `search.complete`. Free-form or backend-specific structured data that has no honest common mapping remains `native.event`.

The adapter must preserve the distinction between adapter observation time and any backend `time` field.

## Stockfish

Current Stockfish UCI output exposes fields including depth, seldepth, multipv, score cp/mate, lowerbound/upperbound, nodes, time, nps, hashfull, tbhits, PV, bestmove, and ponder.

Portable mapping:

- `multipv` -> `candidate.multipv_index`
- first PV move -> `candidate.move`
- full PV -> `candidate.pv`
- `score cp` -> `stockfish.uci_cp`
- `score mate` -> `stockfish.uci_mate`
- optional UCI WDL -> a second evaluation channel with `stockfish.uci_wdl`
- `lowerbound` / `upperbound` -> evaluation bound; absent qualifier -> `none`
- `nodes` -> `stockfish.uci_nodes`, unit `nodes`
- `time` -> `stockfish.uci_time`
- depth/seldepth/hashfull/tbhits/nps -> `stockfish.uci.v1` native data
- `bestmove` / `ponder` -> `search.complete`

PR #7 deliberately keeps Stockfish score perspective `unknown`; later qualification requires a dedicated source/test proof rather than inference.

## Reckless

Current Reckless `print_uci_info()` emits depth, seldepth, multipv, score cp/mate, upperbound/lowerbound, nodes, time, nps, hashfull, tbhits, and PV.

Portable mapping mirrors Stockfish but keeps independent semantics: `reckless.uci_cp`, `reckless.uci_mate`, `reckless.uci_nodes`, `reckless.uci_time`, and `reckless.uci.v1`.

Reckless normalizes its internal score to displayed centipawns before UCI output. That displayed value remains Reckless-native; it is not interchangeable with Stockfish centipawns without later calibration.

An explicitly empty restricted-root set can terminate with no move on a nonterminal board. The adapter must therefore never infer `terminal.fact` from `bestmove (none)`.

## LC0 standard UCI output

LC0 `ThinkingInfo` can expose depth, seldepth, time, nodes, score cp/mate, WDL, moves-left, hashfull, nps, eps, tbhits, multipv, and PV.

Important semantic caveats:

- LC0 UCI `nodes` is produced from LC0 playout/visit accounting, not alpha-beta node accounting. Map it as unit `count` with semantics `lc0.uci_nodes` until a stronger common unit is qualified.
- LC0 score output depends on configured `ScoreType` and can represent several transformations (centipawn variants, Q, W-L, win percentage, WDL_mu). `Lc0TelemetryAdapter` is constructed with the active ScoreType and emits semantics such as `lc0.uci_score.centipawn` or `lc0.uci_score.Q` rather than guessing from `score cp` text.
- LC0 omits the `multipv` token for the default single-PV case. A PV-bearing primary line without `multipv` is normalized by the adapter to `candidate.multipv_index = 1`; no atomic ranking frame is inferred.
- WDL, when emitted, is a separate evaluation channel with semantics `lc0.uci_wdl`; it does not replace the scalar score channel.
- eps, moves-left, depth/seldepth/hashfull/tbhits/nps and similar fields remain under `lc0.uci.v1` unless/until promoted by a later contract.

## LC0 defect instrumentation

The vendored LC0 tree already has opt-in `DefectTelemetry` / `DefectTelemetryIterations` instrumentation. It emits JSON payloads in UCI comments:

```text
DEFECT_TELEMETRY_ITER {...}
DEFECT_TELEMETRY_SUMMARY {...}
```

The LC0 adapter maps these payloads losslessly to `lc0.defect.iter.v1` and `lc0.defect.summary.v1` native events. The integration harness launches LC0 with `--show-hidden` because both defect options are `kProOnly`, then enables `DefectTelemetry` / `DefectTelemetryIterations` through advertised UCI options.

Do not promote `leader_move_raw` or `runner_up_move_raw` into common UCI moves. They are internal raw move encodings.

## Shadow instance mapping

Shadow and active mode reuse the same solver-family adapters for distinct managed process roles, with no adapter change. The common `engine` field continues to identify semantic provenance (`stockfish`, `reckless`, `lc0`), while `engine_instance` distinguishes concrete roles such as `stockfish-anchor`, `stockfish-shadow`, `reckless-shadow`, and `lc0-shadow`.

No new score conversion is introduced for shadow mode. In particular, Stockfish and Reckless centipawns remain independently tagged, LC0 score semantics remain `ScoreType`-qualified, and alpha-beta nodes remain incomparable to LC0 visit/playout-derived counts without later calibration.

The run-level relationship between these independent telemetry streams belongs in the replay manifest rather than in fabricated aggregate ranking events.

The derived layer enforces the same separation in code: `common/residuals.py` raises `ScaleMixingError` when two differently-tagged engine values would be combined, so a Stockfish-minus-Reckless centipawn difference is a runtime error rather than a convention someone could forget.

Shadow workers run at `MultiPV = 3` in the shipped shadow/active configurations so within-engine margins are observable. The anchor stays at `MultiPV = 1` so it remains the unmodified baseline search and the fixed-node decision-firewall regression stays meaningful.

Live capture never runs on an engine's stdout reader thread: the reader enqueues raw lines with an observation timestamp, and a dedicated writer thread performs adapter translation and file IO. A capture-queue overflow is recorded as explicit `dropped_events` truncation evidence rather than applying back-pressure to the reader.

## Terminal facts

Ordinary UCI parsing does not manufacture terminal facts. A `terminal.fact` requires an independent rules or tablebase source with explicit provenance and perspective. This preserves the distinction between "search returned no move" and "position is terminal."

## PR #7 implementation rule

The adapter layer should be lossless before it is clever. If a field cannot be mapped without semantic invention, preserve it under a native schema and defer promotion until a testable mapping exists.


## Adapter implementation details

A physical PV-bearing UCI line produces one `candidate.update`. Parsed-but-unpromoted fields and the raw line are retained under that event's engine-native schema; the line is not duplicated as a second `native.event`.

A non-PV `info` line becomes `native.event`. This includes Reckless's no-move score-only line, so a null best move is never promoted into a terminal fact.

The shared PV parser consumes only syntactically valid UCI moves and stops at the first non-move token. This prevents trailing LC0 `string` comments from being interpreted as PV moves.

LC0 may append `player`, `gameid`, and `side` to `bestmove`; those fields remain in `lc0.uci.bestmove.v1` native data while bestmove/ponder are promoted into common completion fields.

Known null-move sentinels `(none)`, `none`, `0000`, and `a1a1` normalize to `bestmove = null` without creating `terminal.fact`.

The production adapters consume lines supplied by a caller and do not launch processes. PR #8 will own backend subprocess lifecycle and feed stdout into these already-qualified adapters.


## LC0 defect lifecycle ordering

The derived LC0 search now emits defect telemetry exactly once on the normal completion path before its physical `bestmove` response. The destructor retains a guarded fallback for aborted/destroyed searches, but normal completed searches no longer defer telemetry until `ucinewgame` or the next search.

This ordering matches telemetry v1 directly: defect ITER/SUMMARY native events arrive before `search.complete`. The live integration requires `lc0.defect.summary.v1` to be observed before bestmove, so a regression back to destructor-only emission fails CI.
