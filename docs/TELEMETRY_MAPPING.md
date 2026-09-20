# Telemetry v1 backend mapping

This document maps evidence already exposed by the vendored engines into the common telemetry contract. It is a specification for PR #7; PR #6 does not modify engine source or emit live telemetry.

## Shared UCI mapping

A backend process adapter creates `search.started` before dispatching `go`, assigns `sequence`, measures adapter `observed_ms`, and translates each relevant UCI line into one raw event.

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

PR #7 must qualify score perspective before using anything stronger than `unknown`.

## Reckless

Current Reckless `print_uci_info()` emits depth, seldepth, multipv, score cp/mate, upperbound/lowerbound, nodes, time, nps, hashfull, tbhits, and PV.

Portable mapping mirrors Stockfish but keeps independent semantics: `reckless.uci_cp`, `reckless.uci_mate`, `reckless.uci_nodes`, `reckless.uci_time`, and `reckless.uci.v1`.

Reckless normalizes its internal score to displayed centipawns before UCI output. That displayed value remains Reckless-native; it is not interchangeable with Stockfish centipawns without later calibration.

An explicitly empty restricted-root set can terminate with no move on a nonterminal board. The adapter must therefore never infer `terminal.fact` from `bestmove (none)`.

## LC0 standard UCI output

LC0 `ThinkingInfo` can expose depth, seldepth, time, nodes, score cp/mate, WDL, moves-left, hashfull, nps, eps, tbhits, multipv, and PV.

Important semantic caveats:

- LC0 UCI `nodes` is produced from LC0 playout/visit accounting, not alpha-beta node accounting. Map it as unit `count` with semantics `lc0.uci_nodes` until a stronger common unit is qualified.
- LC0 score output depends on configured `ScoreType` and can represent several transformations (centipawn variants, Q, W-L, win percentage, WDL_mu). PR #7 must tag the active transformation rather than assuming every printed `score cp` is the same semantic quantity.
- LC0 omits the `multipv` token for the default single-PV case. A PV-bearing primary line without `multipv` is normalized by the adapter to `candidate.multipv_index = 1`; no atomic ranking frame is inferred.
- WDL, when emitted, is a separate evaluation channel with semantics `lc0.uci_wdl`; it does not replace the scalar score channel.
- eps, moves-left, depth/seldepth/hashfull/tbhits/nps and similar fields remain under `lc0.uci.v1` unless/until promoted by a later contract.

## LC0 defect instrumentation

The vendored LC0 tree already has opt-in `DefectTelemetry` / `DefectTelemetryIterations` instrumentation. It emits JSON payloads in UCI comments:

```text
DEFECT_TELEMETRY_ITER {...}
DEFECT_TELEMETRY_SUMMARY {...}
```

PR #7 maps these payloads losslessly to `lc0.defect.iter.v1` and `lc0.defect.summary.v1` native events.

Do not promote `leader_move_raw` or `runner_up_move_raw` into common UCI moves. They are internal raw move encodings.

## Terminal facts

Ordinary UCI parsing does not manufacture terminal facts. A `terminal.fact` requires an independent rules or tablebase source with explicit provenance and perspective. This preserves the distinction between "search returned no move" and "position is terminal."

## PR #7 implementation rule

The adapter layer should be lossless before it is clever. If a field cannot be mapped without semantic invention, preserve it under a native schema and defer promotion until a testable mapping exists.
