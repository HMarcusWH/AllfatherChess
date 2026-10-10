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
SHA256. The packager refuses overwriting an existing archive/stage; remove them
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

CI extracts the archive to a new directory and repeats the real-engine UCI
handshake; this is not a public bot game or an Elo test.

## Host-capacity observation

```bash
python3 scripts/online-host-preflight.py --output build/online-release/host.json
python3 scripts/online-host-preflight.py --require-capacity
```

Strict mode requires observed cgroup-v2 CPU quota and memory limit, usable
quota >= 4 CPUs, memory limit >= 4096 MiB and Linux x86-64. It records
`j12_authority_qualified=false` even on a passing preflight, because the
full J12 qualifier, not this script, decides authority. Missing host facts
are never treated as unlimited capacity or zero usage.

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
