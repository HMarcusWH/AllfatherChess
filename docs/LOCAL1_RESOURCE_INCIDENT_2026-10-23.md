# LOCAL-1 / Engine Opt v2 — strict resource incident diagnosis (2026-10-23)

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
