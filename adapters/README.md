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


## M14-E cross-feed adapters

M14-E adds a separate pure translation layer under `adapters/crossfeed/`.
These modules do not own engine processes and do not participate in routing,
budget authorization, or outward move selection.

The adapter evidence projection deliberately leaves
`controller.crossfeed.CrossFeedView` unchanged because that object is already
hash-bound into M14-C decision evidence. The projection may additionally expose
M14-D recursive REFINE expansion evidence to adapter consumers without changing
the authority-facing cross-feed digest.

The initial subprocess-safe operation vocabulary is intentionally narrow:

```text
VERIFY_SET
REFINE_PREFIX
```

Both compile to already-qualified ordinary UCI restrictions. `VERIFY_SET`
uses a candidate subset through `searchmoves`; `REFINE_PREFIX` reuses the
existing prefix compiler to advance to the parent position and restrict the
final move.

Engine-specific classes preserve their own native semantic namespaces:

```text
stockfish.*
reckless.*
lc0.*
```

Source ranks remain ordinal only inside one exact engine/search/prefix/candidate
universe. They are never averaged across engines or depths. Tactical alarms in
this milestone are categorical native `mate` observations only; no cp/Q
threshold conversion is introduced.

The real-engine adapter contract proves that all three vendored families accept
the generated UCI restrictions and remain inside the declared move region. It
does not wire the adapters into the live controller.
