# LOCAL-1 / Engine Opt v2 — strict resource incident diagnosis (2026-10-23)

> **Synchronized historical-scope banner, 25 October 2026.** **Retained negative evidence:** The three failures below remain unqualified at their original 23 October SHAs. PR #79 stop-ordering and PR #78 post-publication/headroom repairs subsequently merged and #76's exact-head LOCAL-1 passed; **later passes do not retroactively upgrade these failing resource certificates**. The original 677.229ms persistence tail and missing terminal procfs examples must remain visible as motivation for fail-closed physical accounting. See [publication disposition](ONLINE_PUBLICATION_DISPOSITION.md).


This source-only note retains no private UCI trace or runner attachment. It
describes three failed exact-head GitHub qualification runs as NEGATIVE evidence.

| PR/run | Failed replay | Physical/wall result |
| --- | --- | --- |
| #76 / 39834921805 | 20261023T010311784756Z-g000111-2780f9b1 | LC0 missing terminal /proc snapshot, wall 1501.014 vs 1495.000 ms |
| #77 / 39835222318 | 20261023T022158319276Z-g000051-a5eb379c | LC0 missing terminal /proc snapshot, wall 1493.779 vs 1480.000 ms |
| #78 / 39835767981 | 20261023T030149374394Z-g000087-7b11a6f8 | CPU measurement complete, wall 2233.482 vs 1903.000 ms; 677.229 ms durable write |

The two missing LC0 measurements followed hard-expiry shutdown while stopping.
The controller's prior soft-expiry ordering could wait on anchor STOP before it
scheduled shadow STOP; this candidate moves *non-invalidating* shadow STOP
earlier, still asynchronously and token-fenced. Explicit user-stop remains
anchor-first. The exact wall and CPU limits are NOT changed.

The local forensic script `scripts/diagnose-local1-resource-failures.py` reads
unaltered `manifest.json`, `route.json` and `resource.json`, binds their
hashes and emits separate negative categories. It can never certify success.

A slow fsync is outside engine CPU measurement but remains inside the existing
schema-1 FULL-RUN wall envelope. A valid timely outward move is not equivalent
to qualified terminal resource evidence. The historical failure remains denied.
Any future execution-only wall-clock semantics require a separate versioned,
reviewed policy and independent verifier, not a silent threshold change.

No merge/release or Lichess external authority is inferred from these changes.

## Implemented pre-reap observation boundary (candidate)

The runtime installs a pre-reap callback on unexpected UCI stdout EOF, ahead
of its own Popen.poll()/wait(). The Linux provider accepts only a waitable
zombie's PID/start-time-checked terminal snapshot. This may retain CPU usage as
`observed_terminal_cpu_ms` **diagnostic only**. Such an unexpected process
exit still leaves `complete=false` and `cpu_ms=null` so independently
replayed physical resource claims remain denied. Missing or already-reaped
terminal state, PID reuse and reentrant callbacks never manufacture success.
The observer has bounded access to the coordinator and resource locks; failure
to capture is negative evidence, not permission to reuse a dead process.

The test suite also injects a 700 ms real resource-file persistence delay and
asserts that the original schema-1 full-run wall envelope rejects the run.
No fsync is disabled, no wall threshold is widened, and this does not solve
arbitrary hosted-runner storage latency.
