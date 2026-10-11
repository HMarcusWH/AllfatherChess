# ONLINE-3A: sealed offline AllfatherChess UCI package

**Scope:** offline controller/package validation. No lichess-bot bridge, credentials,
BOT upgrade, network API, J12 host qualification or online deployment authority.

## Offline reference identity

- Four-process Stockfish/Reckless/real BLAS-LC0 runtime:
  `config/allfather.online-hybrid-v2.validation.json`.
- Engine bundle: `build/online-engine-opt-v2`. Its original build manifest
  records the Stockfish-anchor reference policy. The ONLINE-3A release manifest
  additionally binds the separate outward HYBRID config by SHA256.
- Never substitute the unqualified adaptive `orchestrated-v1` host profile.
- Never modify controller move/resource authority merely to pass this gate.

## Clean checkout and reproducible artifact

On x86-64 Linux with the existing ENGINE-OPT toolchain installed, run:

```bash
make build-online-engine-opt-v2
python3 scripts/qualify-engine-opt-v2.py
python3 scripts/release-manifest.py create
python3 scripts/release-manifest.py verify
python3 scripts/package-online.py
```

The output archive is `dist/allfather-online3a-offline.tar.gz` and staged
runtime tree is `dist/online3a-stage/`. The release manifest hashes the
source-controlled controller/config/qualification source files, the actual
three engine binaries and three neural networks, the existing engine build
manifest and the separately authorized HYBRID runtime. It binds the exact Git
source commit and tree, rejecting dirty tracked source or stale build identity.
A copy can verify all bytes on another host even without Git.

Repeated archive generation from the same sealed bytes gives identical archive
SHA256. CI retains the archive digest; external recipients must compare it to
a separately trusted workflow/commit record. An adjacent hash is not a publisher
signature or proof of authenticated distribution. The packager refuses overwriting an existing archive/stage; remove them
deliberately before regenerating. Release files are read-only; the application
is allowed to write only into `build/replays-online-hybrid-v2/`.

## Offline UCI launch

```bash
python3 deploy/bin/allfather-online --check
printf 'uci\nisready\nquit\n' | python3 deploy/bin/allfather-online
```

The launcher checks the release manifest and all included artifacts before
executing `python3 -m controller --config <sealed HYBRID runtime>`. Seal errors
go to stderr and refuse launch, never producing partial UCI stdout. The
`--check` flag is human-facing and must not be passed by a UCI GUI.

ONLINE mode requires a POSIX pipe/FIFO on stdout. Direct redirect to a regular
file is rejected by the controller's atomic UCI write contract. For manual
capture use a pipe and tee (with shell pipefail enabled); never weaken the
controller to accommodate a test harness.

CI extracts the archive and drives the actual four-process controller through
a pipe-backed subprocess, waits for handshake and readiness, makes one clocked
startpos search, validates a non-null legal bestmove using the pinned independent
chess-rules oracle, checks subsequent readiness and clean shutdown, and verifies
regular-file stdout rejection. Evidence includes raw stdout, stderr and JSON
smoke results even on failure. A separate validator recomputes the legal move,
protocol counts, manifest and archive membership/mode checks, and stores the
archive SHA256. No producer-only passed flag can substitute for validation. This is not an Elo or public bot test.

## Host-capacity observation

```bash
python3 scripts/online-host-preflight.py --output build/online-release/host.json
python3 scripts/online-host-preflight.py --require-capacity
```

Strict mode reuses the M14-J LinuxHostProvider and HostCapabilities to examine
every cgroup-v2 ancestor's limits, effective cpuset and process affinity.
It requires complete bounded evidence of at least 4 usable CPUs and 4096 MiB
of memory on Linux x86-64. Unknown ancestor data fails closed and explicit
unlimited limits are not confused with a finite quota. Even a successful
observation retains `j12_authority_qualified=false`; the separate J12
qualifier alone can authorize that runtime.

## Optional container integration scaffold

`deploy/Dockerfile` consumes only `dist/online3a-stage/` and runs as UID
10001. It has no public server listener or API token. Supply a verified
immutable amd64 base image rather than a floating tag:

```bash
docker build -f deploy/Dockerfile \
  --build-arg BASE_IMAGE='ubuntu:24.04@sha256:<reviewed-digest>' \
  -t allfather/online3a:offline .
```

Its APT runtime dependency versions and final OCI image digest are *not yet
release-locked*. This is deliberately a **container integration scaffold**,
not a fully qualified production image. The next release gate must lock
its software bill of materials and qualify the image on the destination
hardware. Do not publish the image as a bot release at this stage.

## Remaining gates

1. ONLINE-3B: pin and test an upstream lichess-bot revision plus resolved
   Python dependencies; use standard/casual/10+5/one-game/bot-only and
   preapproved opponent allow-list; disable books, cloud moves, bridge tables,
   pondering, draw/resign heuristics, unsupported UCI options.
2. ONLINE-4: fake API disconnect/restart/reconciliation/move-ack uncertainty,
   duplicate state events, cancellation and rollback; durable game→move→replay
   links and bounded retention.
3. Actual Linux deployment host qualification, remaining META-1/ONLINE-PLAY-1
   research/acceptance requirements and combined GPL/AGPL compliance review.
4. Explicit operator approval before irreversible Lichess BOT account upgrade,
   live credentials or first unrated public game.

**No claim of a released bot, generic-host portability, Elo advantage,
production adaptive STOP/SKIP or comparative strength follows from ONLINE-3A.**


## ONLINE-3B: offline Lichess bridge (separate release boundary)

The pinned upstream bridge, fake API qualification and restrictions are
documented in [ONLINE_3B_BRIDGE_QUALIFICATION.md](ONLINE_3B_BRIDGE_QUALIFICATION.md).
The bridge's first game search and normal clock searches are tested against the
unchanged ONLINE-3A controller. No real credentials, BOT account conversion,
public opponent or network endpoint are authorized.
