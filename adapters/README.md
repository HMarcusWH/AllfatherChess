# Adapters

Generation 1 adapters are process-isolated and UCI-facing. PR #7 implements the read-only telemetry side of that boundary without adding process ownership or routing.

Current telemetry modules:

```text
adapters/telemetry/uci.py
adapters/telemetry/stockfish.py
adapters/telemetry/reckless.py
adapters/telemetry/lc0.py
```

The shared parser recognizes stable UCI syntax but does not assign cross-engine meaning. Backend modules attach engine-specific semantics such as `stockfish.uci_cp`, `reckless.uci_cp`, and configured LC0 ScoreType semantics.

A PV-bearing line becomes one `candidate.update`; unpromoted information from that same physical line is retained inside its `native` payload rather than duplicated into a second event. Non-PV search information becomes `native.event`. `bestmove` becomes `search.complete`.

LC0 defect records are consumed losslessly as `lc0.defect.iter.v1` and `lc0.defect.summary.v1`. Raw internal move identifiers remain raw. Because LC0 emits these records when its search object is destroyed, the LC0 adapter also supports an opt-in deferred-completion path that keeps normalized `search.complete` last while preserving the physical bestmove receipt time in native data.

The adapters do not launch engines. Process lifecycle, command dispatch, stop handling, and the externally visible UCI shell remain PR #8 work.

Future capability surface:

```text
set_position(...)
search(region, budget)
snapshot()
stop()
verify(candidate, budget)
```
