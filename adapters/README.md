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

LC0 defect records are consumed losslessly as `lc0.defect.iter.v1` and `lc0.defect.summary.v1`. Raw internal move identifiers remain raw. The derived LC0 tree now emits defect telemetry once on the normal search-completion path before `bestmove`, so the adapter no longer needs a deferred-completion workaround.

PR #9 adds a separate production process layer under `adapters/process/`. `UciProcess` owns exactly one stdout reader per backend and demultiplexes handshake waiters, `isready` barriers, search info, and `bestmove` callbacks without competing queue consumers. It also isolates stderr, serializes stdin writes, tracks process health, and performs bounded shutdown.

The telemetry adapters remain read-only semantic normalizers and are not coupled to subprocess ownership. Shadow execution subscribes them to the live process stream without any adapter change: `controller/replay.py` enqueues raw lines on the engine's stdout reader thread with an observation timestamp, and a dedicated writer thread performs adapter translation and file IO. Capture therefore cannot stall or deadlock the single reader, and a capture-queue overflow is recorded as explicit truncation evidence rather than applying back-pressure.

Future capability surface:

```text
set_position(...)
search(region, budget)
snapshot()
stop()
verify(candidate, budget)
```
