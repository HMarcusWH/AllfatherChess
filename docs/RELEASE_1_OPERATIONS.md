# RELEASE-1 — combined offline OCI candidate and qualification

> **No live Lichess access or downloadable public image in this milestone.**
> PR #68 / ONLINE-4B establishes exact-source offline recovery; RELEASE-1
> packages the same sealed UCI engine plus byte-verified patched bridge into
> a production-shaped, nonroot **offline test artifact**. The candidate is
> NOT a registered production bot, an approved combined distribution, a
> destination-host certification, or a playing-strength claim.

## Source identity and frozen inputs

- Main baseline: PR #68 squash commit `f5eab35cf1682d79d939d4b1f5e63f8e96833a1b`.
- All runtime binaries, neural networks, controller settings and ON-3A seal
  are sourced from the exact Git HEAD's `dist/online3a-stage`.
- The bridge is the pinned, verified `df7e730d` upstream and the exact
  ONLINE-4B source-locked adaptation to a **copied, temporary** tree.
- `qualification/release-image-v1.json` binds the immutable Ubuntu 24.04
  base digest and `20261001T000000Z` signed Ubuntu snapshot.
- Python wheel files use `runtime-wheels.lock` and the recorded wheel SHA256
  attestation. Chess is built from the locked sdist.
- Every staged engine/bridge/wheel byte is independently enumerated and hashed
  in `build/release1-stage/combined-manifest.json`. That manifest itself is
  bound to the ONLINE-3A release seal, bridge overlay manifest and source tree.

The APT snapshot makes resolution repeatable, but **a reviewed exact package
version lock and byte-identical rebuild have not been approved**. No automatic
upgrade, dependency substitution, or opportunistic source refresh is allowed
under a claim of frozen/reproducible binary identity.

## Build, but do not publish

On a supported Linux/amd64 CI host with the existing engine dependencies:

```bash
make build-online-engine-opt-v2
python3 scripts/qualify-engine-opt-v2.py
python3 scripts/release-manifest.py create
python3 scripts/release-manifest.py verify
python3 scripts/package-online.py
python3 deploy/lichess/fetch_bridge.py
# The ONLINE-3B workflow resolves, downloads and verifies runtime wheel hashes.
python3 scripts/build-release-candidate.py
python3 scripts/verify-release-candidate.py --stage build/release1-stage
```

The full pinned wheel preparation commands are in
`.github/workflows/release-candidate.yml`. Never bypass its source verification
or install third-party wheel files without checking their hashes.

The dedicated release Dockerfile is `deploy/release/Dockerfile` with a
Dockerfile-specific strict build-context ignore file. Use its exact
`BASE_IMAGE` from `qualification/release-image-v1.json`, not a moving tag.
OCI files are **local, ephemeral CI evidence only**. RELEASE-1 explicitly
forbids pushing to Docker Hub/GHCR, uploading the OCI archive, tagging a
public release or declaring legal compliance completed.

The OCI verifier distinguishes:
- SHA256 of the **exported local OCI tar archive**;
- SHA256 of its `index.json`;
- digest of the linux/amd64 **OCI image manifest**;
- digest of the OCI **image configuration**;
- content-hashed SPDX/SBOM and SLSA/provenance attestations.

These are not interchangeable. BuildKit's default Docker image store may
drop attestations: use an OCI exporter retaining them, and validate both
attestation types before trusting the offline candidate. None of these
local hashes is an authenticated publisher signature.

## Actual-image runtime layout

| Path | Authority |
| --- | --- |
| `/opt/allfather` | sealed UCI binaries, networks and controller; read-only |
| `/opt/bridge` | immutable, prepatched lichess-bot; read-only |
| `/opt/release` | verifier, manifest and hashed wheels; read-only |
| `/var/lib/allfather` | persistent SQLite/WAL journal, owner lock and game traces |
| `/opt/allfather/build/replays-online-hybrid-v2` | **separate durable bind mount** for existing sealed replay-root configuration |
| `/tmp`, `/run/allfather` | bounded ephemeral tmpfs |
| `/work` | read-only CI test-code mount; never part of image bytes |

The replay bind mount is essential: the sealed UCI config uses this specific
relative replay-root path. Do not silently rewrite that path or put all
recovery data into disposable `/tmp`.

Runtime confinement: UID/GID 10001, no published ports, `--network none`,
`--read-only`, `--cap-drop ALL`, `no-new-privileges`, 128 PIDs, 4 bounded
CPUs and 6 GiB container memory. These are **test envelope constraints**,
not qualified minimum hardware for a future online service.

The image entrypoint has only these operations: `verify`, `uci-check`,
`inventory`, and `offline-case`. The latter requires isolation flags
and launches the source-checked existing ONLINE-4B experiment against the
**installed image**. There is no shell, API host override, live mode,
credential loader, BOT upgrade or operator-facing online service.
The guarded transport remains restricted to loopback even if an operator
accidentally runs the image with a network connection.

## Real behavior and independent qualification

The always-present, read-only `release-qualification.yml` invokes the
reusable `release-candidate.yml` for the exact PR commit and tree.
It executes hermetic negative tests, builds and independently verifies the
combined stage and local OCI archive, then runs the real three-engine UCI
and patched bridge inside the **actual loaded image**.

Ten ONLINE-4B scenarios (including both colors, uncertain acknowledgment,
truncated NDJSON, HTTP 429 cooldown and process-kill restart) must be
independently requalified. The Docker inspect evidence for each required
case is checked against the security contract. The actual image's source,
replay and SQLite evidence is then reconstructed without trusting a
producer-generated `passed: true` flag.

The aggregate independently requires source-matched:
- stage manifest and installed image byte verification;
- OCI config/manifest digests and both SBOM/provenance attestations;
- real-image guarded full-game/recovery evidence;
- runtime image confinement records and actual package inventory;
- CI-host capacity observation (but never destination-host certification).

Failing jobs, missing artifacts or skipped required dependencies cannot be
translated into a successful candidate verdict. A genuine offline green
result can set `offline_candidate_verified: true`, while keeping
`promotion_authorized: false`.

## Open gates and next milestone

`qualification/license-compliance-v1.json` inventories GPLv3/AGPLv3
source, notices, model inputs and the undecided Allfather controller
license. It is **not legal approval**. Do not make compiled images or
binary archives publicly downloadable until corresponding source
obligations and combined-distribution treatment are reviewed.

The actual destination host, host cgroup/storage performance, a production
process supervisor, rollback/kill switch, real Lichess authentication and
official API endpoint, BOT account and named opponents are also **not
qualified**. `scripts/qualify-release-host.py` observes only the CI runner.

After squash merge, the final main commit differs from the PR head.
The eventual release candidate must be rebuilt and requalified from
the new `main` source commit before any future promotion. RELEASE-1 may
merge as an **offline candidate capability**, not as permission to publish.

ONLINE-PLAY-1 / the controlled canary phase may then add separately
reviewed live transport and operator authorization, *only* after licensing
and destination-host requirements are satisfied.
