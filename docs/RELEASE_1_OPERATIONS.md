# RELEASE-1: immutable offline OCI candidate (PR #69)

**This is an experimental, offline-only packaging milestone, not a licensed
public distribution, deployable Lichess bot, certified destination host,
authenticated signature or playing-strength claim.**

## Source boundary

The release retains the exact ONLINE-3A source-sealed Stockfish-anchor /
Stockfish-shadow / Reckless / BLAS-LC0 UCI bundle. It separately checks the
pinned lichess-bot Git commit (df7e730d...) and the source-locked ONLINE-4B
overlay. The upstream checkout remains byte-identical and is **not patched
twice**. The image contains an already-patched, hash-manifested bridge but
does not have a live-client supervisor or permission to contact Lichess.

Release policy lives at qualification/release-image-v1.json and runtime
contract at deploy/release/runtime-contract.json. No moving Docker base
tag, no unhashed model file or implicit fallback bridge is allowed.
Ubuntu 24.04 repository pockets use an authenticated signed snapshot.
The image digest, image config digest and OCI archive hash are separately
recorded; they must not be confused.

The Ubuntu base and package snapshot alone do not prove byte-identical
rebuilds. The OS package inventory, Python runtime wheel lock, pinned wheel
hashes and full application file manifest are collected and verified.
Until exact reviewed OS/wheel inventory hashes are committed, the aggregate
reports offline_candidate_qualified=false even when the image test succeeds.

## Build / qualification sequence

On Ubuntu 24.04 amd64 with Docker Buildx and the original ENGINE-OPT-V2
toolchain, obtain the exact checkout, build the locked networks and engines,
and seal the UCI release:

```sh
make build-online-engine-opt-v2
python3 scripts/qualify-engine-opt-v2.py
python3 scripts/release-manifest.py create
python3 scripts/release-manifest.py verify
python3 scripts/package-online.py
python3 deploy/lichess/fetch_bridge.py
# Use the locked dependency resolution / wheelhouse procedure in
# .github/workflows/release-candidate.yml
python3 scripts/build-release-candidate.py
python3 scripts/verify-release-candidate.py --stage build/release1-stage
```

The candidate is assembled under build/release1-stage. Source manifests and
OCI metadata are independently reconstructed in the release-candidate CI
workflow. It builds with an explicitly pinned Ubuntu image, exports a BuildKit
OCI layout with SBOM/provenance attestations and separately loads a test image
for execution. Docker's local classic image store may not retain the
attestations; the OCI exporter is independently inspected.

**No OCI tarball, Docker image, binary bundle or public container tag is
uploaded to GitHub artifacts** while combined-distribution licensing is
unresolved. CI retains non-binary evidence reports, inventories, SHA-256
digests and a source-bound manifest. CI artifacts and their hashes are
not publisher-authenticated release signatures.

## Runtime contract

The entrypoint at /opt/release-tools/deploy/release/entrypoint.py permits only:

- `verify`: complete byte-seal, pinned source and wheel checks;
- `offline-smoke`: explicit loopback simulator with synthetic token.

There is no `play-online` operation. A real LICHESS_BOT_TOKEN is a fatal
configuration error. The guarded bridge allows only http://127.0.0.1.

The qualified runtime command must include:

```sh
docker run --rm --network none --read-only \
  --pids-limit 128 --cpus 4 --memory 6g \
  --cap-drop ALL --security-opt no-new-privileges \
  --user 10001:10001 \
  --tmpfs /run/allfather:rw,nosuid,size=64m,uid=10001,gid=10001 \
  --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001 \
  --volume "$PWD/test-persistent:/var/lib/allfather:rw" \
  -e ONLINE_RELEASEx_ISOLATED=1 -e ONLINE4B_ISOLATED_CONTAINER=1 \
  allfather/release1:offline offline-smoke \
  --scenario R00 --color white --run-id smoke-white
```

Prepare the host test-persistent directory for UID/GID 10001 before use.
The distinct bound persistent mount contains the SQLite WAL and epoch fence.
A separate persistent replay bind mount is mandatory because the sealed UCI
config writes to build/replays-online-hybrid-v2 relative to /opt/allfather.
That directory is still immutable without the explicitly provided mount. Both /opt/allfather and /opt/bridge remain read-only.
Each simulated game uses a new bounded run identifier; the runner refuses
to overwrite any prior journal. Process cleanup is deliberately confined to
its private PID namespace and is **not** a production daemon.

The image must play complete standard/casual 10+5 games from both colors
and retain a replayable terminal journal; fault R02 exercises a real
bridge restart and R10 proves 60-second account-wide cooldown containment.
The frozen ONLINE-4A and ONLINE-4B source harnesses remain separately
qualified in their own CI workflows.

## Image, host, licensing and governance checks

scripts/verify-release-candidate.py checks all packaged files against an
exact source-bound manifest, including both bridge patch hashes and every
wheel hash. scripts/verify-release-oci.py checks OCI index and image/config
descriptors, layer hashes, attestations and executable identity. The OCI
archive SHA-256 is different from the container manifest digest.

scripts/qualify-release-host.py reuses the canonical LinuxHostProvider and
HostCapabilities. A GitHub runner is only a bounded *test host*. Without a
named and independently measured production destination,
destination_host_qualified must remain false even if resource preflight
passes.

The license inventory in qualification/license-compliance-v1.json includes
Stockfish, LC0, Reckless, lichess-bot and the undecided Allfather-specific
controller license. A reviewed corresponding-source package, permitted
distribution arrangement, obligations and notice text must be approved
before any public image or downloadable binary is published.

The always-present .github/workflows/release-qualification.yml only
validates that **publication remains prohibited**. Its green status is
not a release qualification. An authenticated publishing/deployment
workflow must be designed separately after the legal and operator gates.

No real credentials, account upgrade, bot challenge, public network
session, deployed service, host certification or Elo claim occurs here.

## Next step

After the build artifact and exact OS/Python locks are source-approved,
separately qualify the named destination host, complete compliance review,
design and independently test a guarded official-Lichess transport profile
and a supervised bot process. Only then, with explicit operator approval,
consider one unrated/casual 10+5 approved-bot canary.

## Signed APT trust-root bootstrap

The digest-pinned minimal Ubuntu 24.04 image may not contain a usable CA
certificate bundle. Fetching the fixed HTTPS snapshot *before* installing
`ca-certificates` fails TLS validation. RELEASE-1 now installs only
`ca-certificates` using native HTTP access to Ubuntu's signed APT
`InRelease` metadata, restricted to official noble/noble-updates and
noble-security `main` pockets with the pinned Ubuntu archive GPG keyring.
It immediately deletes those bootstrap sources, installs the trust root,
and then uses the frozen signed **HTTPS** Ubuntu snapshot for all remaining
dependencies. No `trusted=yes`, insecure TLS switch, or unsigned package
installation is permitted.

This bootstrap solves the dependency-order problem, **not** final package
reproducibility: the CA bootstrap package version is included in the
image's dpkg inventory, while `ca_bootstrap_snapshot_locked=false` and
`os_package_lock_complete=false` remain explicit blockers. Public
distribution and production deployment remain prohibited until reviewed
exact versions and associated redistribution/source obligations are cleared.
