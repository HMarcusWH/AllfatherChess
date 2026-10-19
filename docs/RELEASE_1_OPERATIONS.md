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
  --volume "$PWD/test-persistent/replays:/opt/allfather/build/replays-online-hybrid-v2:rw" \
  -e ONLINE_RELEASEx_ISOLATED=1 -e ONLINE4B_ISOLATED_CONTAINER=1 \
  allfather/release1:offline offline-smoke \
  --scenario R00 --color white --run-id smoke-white
```

Prepare `test-persistent/replays` and the parent directory for UID/GID 10001
before use (for example `mkdir -p test-persistent/replays && sudo chown -R 10001:10001 test-persistent`).
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

## SLSA and SPDX assurance contract

The scanner-only compatibility job uses the same explicit reviewed BuildKit
`builder-id` as the full OCI export, with a separate SBOM attestation. The
independent scanner verifier validates all recognized in-toto statement
siblings and SLSA v1 builder fields. The builder ID is a declared build
platform identity, **not** a publisher signature or distribution permission.
Field-specific failures must be reported using fixed classifications, never
by echoing arbitrary provenance strings into public CI logs.

SPDX 2.2 and SPDX 2.3 have different package-field requirements. In the
strict SPDX 2.2 profile, package copyright and concluded/declared license
fields are mandatory, and analyzed packages require a verification code.
SPDX 2.3 permits those package metadata fields and the verification code to
be absent, with NOASSERTION semantics where specified. In both versions,
`filesAnalyzed` defaults to true; a supplied verification code must be
valid, and `filesAnalyzed=false` prohibits verification codes and
file-license arrays. Every SPDX file record must contain a valid SHA-1
checksum, with optional additional supported digests.

Every supplied license field is checked by the source-attested offline SPDX
2.x expression parser against the frozen SPDX License List v3.29.0
(`qualification/spdx-license-identifiers-v3.29.0.json`); syntactically
valid licenses do not establish actual copyright permissions. The OCI verifier
independently checks application manifest subjects, the full layer graph,
archive bytes and provenance, and never authorizes distribution.

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

## Mounted replay data and immutable release verification

The runtime's only writable engine subtree is
`/opt/allfather/build/replays-online-hybrid-v2`, overlaid with a
separately mounted persistent replay directory. After one completed game,
the next process finds new replay manifests on that volume. Those
runtime-generated files are intentionally **not** part of the immutable
release source file manifest.

The installed verifier excludes only that precise replay subtree and
only when its filesystem device is different from the image's read-only
engine root. Every executable, controller source, model weight, patched
bridge file, Python wheel and policy artifact stays under the original
exact-byte verification; unlisted files elsewhere fail closed. During
staged (pre-image) verification, replay-file exemptions are never allowed.

RELEASE-1 also exports only Git-tracked files from the verified
lichess-bot checkout. Untracked files such as a local `.env` may not
enter the image regardless of their contents. The CA trust-root bootstrap
source is bound into the source-attestation inventory.

## Post-merge Codex hardening: source-bound OCI and independent move evidence

PR #71 introduces an explicitly named **local OCI export** (`--tag allfather/release1:offline`).
The tag is an attestation identity input; the workflow uses `type=oci,dest=...`,
not a registry push, and never publishes the image or OCI tarball.
`scripts/inspect-release-oci.py` writes bounded diagnostic-only JSON before
`scripts/verify-release-oci.py` evaluates the archive. The diagnostic report is
not an integrity verdict. Image-level in-toto subjects and Docker descriptor
references must identify the exact selected application manifest, while the
OCI layers must still match the ordered expanded `config.rootfs.diff_ids`.
The final aggregate independently binds the generated OCI archive SHA-256.

Each installed-image smoke run now retains evidence schema 2: the ordered
server transport trace with a canonical SHA-256 digest, request-ID-correlated
HTTP responses, authoritative board commits, durable SQLite journal and
guard submission audit. On the CI host, `scripts/qualify-release-smoke.py`
independently reconstructs move legality, HTTP-attempt uniqueness, accepted
board history and journal/epoch identities. It also checks R02's real
lost-ACK restart and R10's complete post-429 observation window and
account-wide 60-second API cooldown. A producer `ready: true` without those
independent witnesses is insufficient for qualification.

The host-side report `build/test-results/release1/smoke-verdict.json` and
bounded OCI descriptor report `oci-attestation-structure.json` are retained
as non-binary CI diagnostics. No raw OCI archive, executable, private log or
persistent SQLite replay volume is uploaded by the RELEASE-1 workflow.

**No new release permissions are implied.** OS/wheel reproducibility locks,
combined license review, destination-host qualification, real Lichess
transport, public BOT account play and operator approval remain blocked.

### Final PR #71 evidence-integrity controls

BuildKit's OCI metadata identifies the exported root descriptor, usually a nested image index. The outer layout index.json, selected nested image index, runnable application manifest, and attestation manifest must be kept distinct. The exact exported descriptor digest, media type and size must match BuildKit metadata. Every embedded OCI descriptor is checked against its actual archive member length and hash.

The workflow requests minimal SLSA provenance with an explicit builder-id URI. A schema-shaped empty predicate or blank builder identity fails the version-correct validator; actual BuildKit output must still pass the real OCI qualification job. The bounded recursive inspection report is diagnostic-only.

The packaged smoke verifier now opens the real state/move-journal.sqlite3 read-only and checks integrity, owner, games, intents, cooldown, events and WAL-visible state before accepting result.json. It rejects linked or missing state paths and mismatching snapshots. SQLite/JSON consistency is not a signature and does not protect against a writer that rewrites both records coherently.

For R02 the fake server records move request IDs in response_write_completed and connection_close_requested, resetting the identifier at every new HTTP/1.1 request. The independent host verifier rejects a completed response for the targeted move and requires the correlated close, first-epoch unknown ACK and subsequent recovery evidence. A server-side 200 status alone does not establish that the client received the response.

The pure verify_smoke() function remains useful for synthetic trace-unit tests. Only verify_file() and qualify_directory() verify persisted SQLite durability. The release permissions, source/engine configurations, and outward move authority remain unchanged.

## Final PR #71 repair contracts (October 2026)

The independent OCI verifier now checks that the *nested-index runnable-image descriptor* advertises `linux/amd64`, agreeing with the image configuration; a supported flat manifest without a platform descriptor remains permitted, but a declared platform must agree. Any present OCI 1.1 attestation manifest subject is validated against the application's already-hashed manifest descriptor (digest, media type and exact nonboolean integer byte length). The recursive inspector exposes declared and actual subject sizes as **diagnostic-only** data.

R02 lost acknowledgment requires a unique first-epoch `http_unknown_ack` audit with `endpoint="move"`; a non-move or missing endpoint cannot stand in for that evidence. R10 requires a matching completed HTTP 429 response, a matching first-epoch `global_rate_limit_block` guard audit, an on-disk `UNKNOWN_ACK` intent and `http429:move` cooldown, plus the existing account-wide API freeze and 61-second observation. Each is independently verified; a producer `ready=true` is never sufficient.

The Dockerfile's snapshot-based APT update/install uses bounded `Acquire::Retries=2` and HTTPS timeouts of 30 seconds. Persistent failure is not ignored: the workflow preserves a bounded failure-only JSON report at `build/test-results/release1/snapshot-acquisition-failure.json`, prints the tail of the temporary build log, and exits unsuccessfully. Package sources, signatures, pinned base and snapshot, and release restrictions remain unchanged. A successful retry is not proof of pinned OS/wheel inventory or release permission.

## PR #71 further integrity hardening

Stopped packaged smoke containers write the persistent recovery SQLite journal as UID 10001. The host runner may read files but cannot create or update WAL shared-memory sidecars there. The independent verifier now copies only the regular SQLite main file and optional WAL bytes into a private host-owned temporary directory, omits producer SHM, and opens the copy through normal SQLite mode=ro, consistent transaction and integrity check. This retains WAL-visible committed rows while leaving original evidence untouched. immutable=1 is never used. Directory/sidecar symlinks, corrupt bytes and JSON mismatches fail qualification.

For OCI platform selection, every *advertised* platform on a nested-index descriptor leading to the runnable application must match linux/amd64, in addition to the leaf manifest and image configuration checks. Synthetic nested root/intermediate platform falsifiers are covered. Attestation-only index branches are not mistaken for runnable images.

SPDX 2.x documentation predicates must include the real document-creation identity and a structurally identified nonempty package/file inventory: CC0-1.0 data license, SPDXRef-DOCUMENT, document name and URI namespace, UTC creation timestamp and creator records. Such shape validation never establishes publisher authenticity, licensing or redistribution permission.

R10 requires a finite numeric SQLite cooldown deadline consistent with the single same-epoch durable cooldown event's recorded deadline, cause and timestamp. The original 60–86400-second duration is checked with a narrow transaction-timestamp tolerance and against the client's 429 guard audit. A matching but semantically forged JSON+SQLite deadline is not sufficient.

All final-head real-process workflows, standalone OCI attestation verification, additional Codex code/security review, dependency locks and public-release gates remain independently enforceable.

## Final PR #71 SPDX, OCI, observation-window and evidence workflow

SBOM validation: in-toto SPDX predicate document must have supported 2.2/2.3 creation identity, legal namespace without `#`, identified file/package records, unique SPDX IDs, valid package `name`+`downloadLocation`, and file `fileName`+nonempty checksum inventory. The offline release contract additionally requires nonempty combined inventory. The diagnostic-only inspector reports bounded counts and SPDX field names, never arbitrary file names or full predicate contents. A genuine nonempty SPDX file-only SBOM is valid; an absent or malformed inventory cannot be admitted.

OCI runtime selection: declared platforms on every selected runnable index/manifest path must match the qualified linux/amd64 baseline; unqualified nonempty OS/CPU requirements, invalid field types, unsupported variants and additional unknown platform requirements are rejected. Each embedded index/manifest `mediaType`, if present, must match the referencing descriptor; all child descriptors remain digest/size verified.

R10: the `rate_limit_hold_completed` event must be request-correlated and recorded after 61.25 seconds have elapsed since the targeted real HTTP 429 response completion. The independent host verifier rejects a completed hold less than 61 seconds after this event. A server write does not by itself prove client receipt: the same first-epoch `global_rate_limit_block` guard audit and persistent SQLite cooldown evidence are independently required. The producer's fixed 100-second scenario deadline remains a bounded fail-closed timeout.

SBOM generator: RELEASE-1 no longer uses the historical SHA256-only Docker Hub scanner. The scanner-only source lock is `qualification/release-scanner-v1.json`, bound to docker/buildkit-syft-scanner v1.11.0 at commit `d88056b4`, Go 1.25.8, vendored Syft 1.42.3 and the SHA256-approved `deploy/release/scanner/target.go.patch`. The patch adds real SHA1 hashing beside SHA256; it does not modify SPDX evidence after scanning. The scanner is compiled twice with vendored-only dependencies and builds two disposable scratch images to test binary and image reproducibility. Its manifest is resolved in a loopback-bound, runner-local registry and passed to BuildKit by immutable digest, never a mutable tag. CI still computes full BuildKit metadata to verify attestation but uploads only allowlisted sanitized reports, OS inventory and four local smoke logs; full scanner binaries, OCI archives, raw SBOM/provenance and private event payloads are never uploaded.

The green supporting workflows and full image smoke testing remain necessary but insufficient: standalone OCI attestation verification, final host aggregate, dependency qualification and a fresh review must pass. The publication/licensing/live-game prohibitions are unchanged.

## RELEASE-1 snapshot, SBOM and downstream evidence qualification

**Pinned snapshot acquisition:** The image still starts from the digest-pinned Ubuntu base and two native APT sources with GPG Signed-By and the frozen Snapshot field. The minimal base bootstraps CA trust from signed native HTTP indexes as before. Remaining packages are installed with deploy/release/install-pinned-apt.sh: up to four bounded attempts for transient HTTP 429/502/503/504 responses, with old index lists discarded after failure. Hash, signature, certificate or unrecognized download failures are fatal; there is no mirror or unsigned fallback. An availability-only snapshot preflight runs before expensive engine compilation and emits a sanitized JSON diagnostic. It does not replace APT signature validation.

**Scanner preflight:** The scanner-compatibility job builds the v1.11.0
source-locked generator with SHA1 + SHA256, starts a disposable local registry
and builds a minimal test OCI image without chess engines. It requests one
SPDX SBOM and one minimal SLSA v1 provenance statement, then independently
verifies the exact builder ID frozen in `qualification/release-image-v1.json`.
The actual manifest digest and binary SHA256 are recorded as **candidates**.
They are never admitted just because the job produced them: the source lock's
`approved_manifest_digest` and `approved_binary_sha256` must be populated
through a separate review of clean reproducible builds. Until then the
scanner-compatibility job deliberately fails after reporting the bounded
candidate identities; the full OCI integration remains skipped. The completed
image identity still cannot grant publisher signature, licensing, network or
distribution authority.

**SPDX source rules:** Package downloadLocation may be NONE, NOASSERTION, or a supported syntactically valid URL/VCS location without embedded secrets. File and optional package checksums require supported exact algorithm and canonical lowercase fixed-width digest. Variable-width MD6 is explicitly unsupported in the release profile. A structurally valid SPDX document is not evidence of actual package-download provenance. The SPDX schema and release-profile policy are separate claims.

**Image runtime platform:** Both every selected runnable descriptor platform and the OCI image configuration platform fields must match the qualified linux/amd64 baseline. The image config retains its legitimate rootfs, history, and runtime config values; strict descriptor-only requirements are not applied indiscriminately to the entire image JSON. The full graph is hashed and linked through BuildKit metadata and the loaded Docker image inspection.

**Admission and evidence:** scripts/qualify-release-aggregate.py now requires an independent host smoke verdict with persisted SQLite matching actual disk, exact case identity, no publisher/public grant, and release_blocked=True. The aggregate also checks the selected exported OCI root digest against BuildKit's declared digest. Allowed HTTP statuses and paths are rechecked as defense in depth; legal move authorization and no-blind-retry stay exclusively with the strict host verifier. Generated diagnostic artifacts are allowlisted, free of raw BuildKit metadata, image layers, subject names and unreviewed annotations.

**Stop condition:** An exact-head test runner must pass the complete RELEASE-1 fast contracts, scanner compatibility, pinned signed APT, installed Docker image, R00/R02/R10 persisted real-process cases, OCI attestation and SLSA validation, and final aggregate; all other workflow gates plus fresh Codex review must be green before draft PR merge. The OS/wheel locks remain incomplete and public distribution remains prohibited.

## PR #71 October 17: fault-evidence admission and snapshot/diagnostic follow-up

The authoritative RELEASE-1 HTTP request contract is now `scripts/release_http_contract.py` (host-only, source-attested). Exact GET endpoints are `/api/stream/event`, `/api/bot/game/stream/off3b001`, `/api/account`, `/api/account/playing`, `/api/users/status`, and `/game/export/off3b001`, status 200, with no move request ID. No wildcard GET or query variant is admitted. The independent packaged-smoke verifier and downstream aggregate both call this same contract, while the independent verifier additionally validates legal UCI/board, durable SQLite rows, ACK handling and hold windows. This is evidence admission, not authorization for live external services.

ONLINE-4A/4B source evidence now refuses a recorded legal UCI that differs from the actually submitted canonical move URL; percent-encoded alias paths are not accepted as a substitute for the canonical path. Counter matching alone does not establish move/intent identity. Real fake-server request and immutable trace evidence remain the inputs; no chess engine move-selection behavior is changed.

Snapshot availability preflight reads the complete bounded signed-cleartext InRelease document, checks required beginning/end delimiters and suite identity, prohibits unexpected redirects, checks exact Deb822 source stanzas, handles TimeoutError and socket read failures, enforces a global budget, and writes a sanitized failure report. This preflight remains diagnostic only: cryptographic GPG verification is still performed by pinned Ubuntu APT during image building. The APT retry classifier permits retry only if **all** acquisition failures are among transient HTTP 429/502/503/504; unknown and mixed errors fail closed.

The OCI diagnostic inspector runs before strict verification and may be uploaded on failure. It therefore now allowlists statement `_type`, `predicateType`, descriptor/config media types, accepted annotations and subject digests, and does not echo arbitrary error text or source metadata. Validation itself still requires the complete application config/layer/SPDX/SLSA subject graph; sanitized diagnostics do not establish qualification.

Do not mark these changes proven until exact-head CI passes. Retain the frozen no-publication, no-credential, no-host-admission, no-live-transport, no-license-promotion and incomplete OS/wheel-lock requirements. Run original RELEASE-1, ONLINE-4A/4B, baseline/LOCAL-1/LC0 and policy workflows, then request final Codex reviews.

## October 17 — raw HTTP targets, exact move evidence and OCI archive continuity

Retained offline HTTP evidence now represents the raw request-target when explicitly approved by `tests/lichess/http_target_contract.py`, staged alongside the `FakeLichess` server and source-attested. Rejected arbitrary targets are recorded as disapproved plus a SHA-256 fingerprint and a safe path, not as unredacted query values. The pinned upstream Lichess bot sends `GET /api/users/status?ids=allfatherfixture` and `POST /api/bot/game/off3b001/move/{uci}?offeringDraw=false|true`; both documented forms are admitted, but unrelated/duplicated/query-bearing paths are not. `scripts/release_http_contract.py` uses the exact same classification for independent smoke and downstream aggregate. Live tokens remain forbidden.

ONLINE-4A evidence matching is two-way: every actual move HTTP POST (including an accepted, failed or fault-injected request) must be matched to an exactly one-to-one move trace entry by method, path and request ID; the trace still must match a canonical UCI and authoritative board/legal intent. A legal but untraced POST is an evidence failure, not a valid extra submission.

RELEASE-1 OCI verification now reads and hashes its archive through the same opened inode, requires stable inode/size/mtime/ctime and rejects an atomically replaced file before reporting `oci_archive_sha256`. The later aggregate independently rehashes the pathname and will reject a different archive. OCI structure inspection is diagnostic-only, tolerant of malformed JSON container types with a safe, bounded failure report, never an integrity or publisher-signature grant.

The native GPG-signed, fixed Ubuntu snapshot remains authoritative for APT. Retry classification ignores bracketed HTTPS peer address/port metadata (`443` is not an HTTP status); retries remain bounded to explicitly recognized 429/502/503/504 acquisition failures and immediate refusal on mixed or integrity failures.

Full exactly pinned offline OCI/SQLite/engine, complete merge/check matrix and final Codex reviews are **still required** for any new head. No release-policy permissions or dependency-lock qualification are raised.

## October 17 — portable independent verifiers and attested source closure

RELEASE-1 runs `scripts/qualify-release-smoke.py` directly as a host CLI. The original `scripts/release_http_contract.py` top-level package import depended on CWD/`PYTHONPATH` and failed only *after* four real packaged games succeeded. The shared canonical HTTP policy is now imported by an explicit `importlib.util.spec_from_file_location` using `Path(__file__).resolve().parents[1]`, preserving one single policy file but removing accidental Python path dependence. A regression invokes both stand-alone host verifiers with normal `python` from an unrelated directory and `PYTHONPATH` unset. The real CI runner uses user-installed Python dependencies, so isolated `-I` mode is intentionally not imposed. Fast contracts must execute that check **before** expensive engine compilation.

ONLINE-4A/4B source policies both include the new `tests/lichess/http_target_contract.py` in their exhaustive attestation path sets. The fake HTTP server captures the origin-form request target from the raw request line *before* CPython converts leading double or triple slashes to a single slash. Approved canonical upstream requests may retain only the audited allowlisted `ids=allfatherfixture` and `offeringDraw=true|false` parameters; rejected raw queries are not copied into downloadable reports.

ONLINE-4A now replays all HTTP move attempts and authoritative commits in transport sequence. An attempted move must match game, receipt ply, canonical UCI and side/position legality. Coherent unsafe attempts remain observable `UNSAFE_WITNESS` evidence and cannot masquerade as normal safe continuation. A stale replay after prior committed legal move has its own observed disposition only when the correlated original lost/unknown acknowledgment is proven. ONLINE-4B additionally compares per-request IDs on full HTTP move audit versus trace.

OCI attestation statement validation and sanitized diagnostics accept two forms only: valid singleton in-toto statements with `statements` *absent*, and nonempty correctly typed `statements` arrays. Explicit null/object/string/empty wrappers fail closed even when rehashed consistently. The RFC3986 URI input grammar rejects raw Unicode and forbidden delimiter characters, without rejecting valid percent-encoded URIs or legitimate pinned BuildKit/Syft data. No permission, licensing or public release gate is promoted.

The pinned Syft scanner-only integration shares the same in-toto envelope contract as the full OCI verifier and safe diagnostic tool. Valid singleton statements have no `statements` key; present null/nonarray/empty wrappers are rejected. This preflight is a compatibility test only and cannot qualify or distribute any public artifact.

### Source-locked observed unsafe recovery versus evidence corruption

A genuine dropped-before-commit R01 fault can yield a second chess-legal HTTP submission on the unchanged position that the fake server actually commits. ONLINE-4A's independent verifier now preserves this as a *valid authoritative board commit and an unsafe repeat attempt*; it no longer rejects the entire harness merely because the committed request ID belongs to the second attempt. Other fault cases similarly retain observed stale/forbidden retry evidence under bounded, sanitized `UNSAFE_WITNESS` dispositions. A normal R00 cannot be declared `SAFE_CONTINUATION` if an attempted move was illegal, repeated, or had an unapproved raw target.

ONLINE-4B verifies full original GET/POST request targets against the source-attested HTTP policy, requiring raw `request_target` to match the normalized route and admission bit before matching request-ID/move/SQLite evidence. The pinned bridge's genuine `ids=allfatherfixture` query remains permitted, but forged double-slash and secret/extra queries never qualify. A duplicate or mismatched raw target is rejected rather than reinterpreted through parsed pathname alone.

RELEASE-1 URI admission now constrains path/query/fragment components individually under RFC3986 and validates optional recognized SLSA v1/v0.2 object/map/list fields, while preserving real BuildKit valid optional nulls and percent-encoded resource identities. Every new source change requires exact-head scanner, packaged-game, OCI/SLSA and aggregate qualification. No live transport or distribution claims arise.

## Consolidated assurance-proof obligation boundary

The new assurance contract is `qualification/assurance-contract-v1.json` with executable source dependency closure and Git committed-byte identity through `scripts/verify_source_closure.py`. Offline HTTP status meaning is defined separately from canonical request-target syntax. A successful harness is distinct from observed unsafe behavior and does not grant safe continuation; a green OCI image does not grant public distribution or even `offline_candidate_qualified` while other gates remain false. Versioned local validation profiles define the accepted subset of SLSA ResourceDescriptor and BuildMetadata semantics, with intentionally independent OCI/scanner negative tests.

All retained independent reports pass through the bounded safe-output serializer. Tests must inject a secret-like canary into nested producer fields and verify that the final serialized artifact excludes it. Source imports and dynamic execution must be attested exhaustively before interpreting producer or verifier claims.

### 2026-10-17 nested assurance follow-up

Review the current source closure, HTTP, OCI/SLSA and sanitized-report contract jointly, never by a green one-field predicate. BuildKit material identities may use RFC3986 **opaque Package URLs** such as `pkg:docker/ubuntu@...` in SLSA provenance; these are immutable resource identifiers, not network destinations. The independent OCI verifier must accept correctly formed versions without broadening live network authority.

An injected HTTP fault must be reflected in the *targeted request's observed HTTP status*, not just in an independent trace flag. The aggregate must independently link the OCI archive digest/layers and full SPDX/SLSA/subject graph to its positive `image_integrity_qualified` claim. Scanner preflight cannot select one valid SPDX sibling while silently dropping other malformed attestation statements.

The October 18 SPDX review exposed a version-confusion risk: SPDX 2.2 package
copyright/license fields remain mandatory while SPDX 2.3 permits omission;
the file SHA-1 checksum rule applies to both versions. Neither an optional
verification code nor a missing one may bypass validation of supplied license
information. This is now enforced by version-specific negative controls.

The bounded APT acquisition retry remains restricted to individually recognized transient statuses. A valid `apt-get install` unable-to-fetch summary may accompany those statuses, but unknown errors never become retryable by including that summary. All green checks must originate at one exact Git SHA; smoke/OCI data from another head cannot grant release promotion.

## October 18 — verified interpreter inputs before qualification

The RELEASE-1 workflow independently compares its scanner/OCI and aggregate
verifier files against Git's exact committed bytes **before execution**.
During aggregation, `scripts/qualify-release-aggregate.py` additionally
requires the stage's `source_attestation` hashes to match the actual
working-tree bytes and `git show SOURCE_SHA:path`. It then runs the
independent smoke, shared HTTP and claims verifiers from a private snapshot
containing only those verified bytes, protecting against later mutation of
the checkout. The aggregate cannot trust a changed smoke verifier merely
because `git rev-parse HEAD` is unchanged.

These checks bind a qualification to its source bytes; they do **not** confer
operator authority, license clearance, authenticated publisher signatures,
network recovery claims or rights to distribute a binary image.

## October 18 — scanner source-build and approval procedure

The upstream v1.11.0 scanner uses Syft file hashing configured for SHA256 by
default; that conflicted with SPDX 2.x's required SHA1 on file records. The
source-attested `target.go.patch` adds `crypto.SHA1` and `crypto.SHA256`
through the existing Syft `WithFilesConfig(WithHashers(...))` API. A pinned
Go 1.25.8 compiler action builds only vendored dependencies offline.

The CI source scanner lifecycle is:

1. Compare all scanner scripts, patch, Dockerfile and lock bytes with the exact
   Git HEAD. Fetch only upstream commit `d88056b4`, check the Go/Syft version
   and apply exactly one patch to `internal/target.go`.
2. Compile twice and compare executable hashes. Build two `FROM scratch`
   scanner images in the host Docker engine with a frozen source-date epoch.
3. Push **only to the current GitHub runner's** loopback-bound, digest-pinned
   `registry:2.8.3` container; compare both manifest digest responses.
4. Configure a docker-container BuildKit daemon on the same Docker bridge,
   allowing plain HTTP only for `scanner-registry:5000`, and address the
   custom scanner by its manifest SHA256.
5. Run the real scanner against the pinned Ubuntu fixture and require genuine
   SPDX/SLSA validation. Before marking scanner compatibility green, compare
   the measured manifest and binary hashes with separately reviewed values in
   `qualification/release-scanner-v1.json`. All results are offline only.
6. Recreate and verify the scanner independently on the full OCI integration
   job. Destroy temporary registry, builder and network on exit.

The initial scanner lock deliberately left binary and image identities
`null`, so the real scanner could be tested without self-approving its
manifest. Source-locked CI run 39130571069 built two matching Go binaries,
produced identical OCI manifests and validated a real 93-package/2265-file
SPDX document alongside the required SLSA v1 builder. Its measured binary
SHA256 and manifest digest are now frozen in
`qualification/release-scanner-v1.json`. Subsequent CI jobs must reproduce
both exact identities, independently prove a known regular-file hash, and
still satisfy all packaged OCI, safety and governance constraints.
No registry image or scanner binary is published, and no external credentials
are required.

File records with SHA1 `000...000` (used by upstream Syft for entries without
regular-file digest metadata) may be structurally allowed by SPDX, but are
**not** proof of actual file contents. An independent known-regular-file hash
test is required before claiming scanner content-hash qualification.

### Independent real-file content-hash witness

The real scanner probe also extracts `/usr/bin/dpkg` from the exact digest-
pinned Ubuntu 24.04 base image without running the image. The independent host
verifier compares SHA1 and SHA256 over those actual bytes with the corresponding
SPDX `fileName` entry in the scanner's OCI-attested document. A hexadecimal
SHA1 string, an absent file or the nonregular-file zero placeholder does not
qualify. The reference executable is kept only in a temporary runner directory
and is never uploaded. The published witness contains safe Boolean verdicts,
not the raw file or SBOM.

## October 18 — final independent-review repairs

The manual final boarding review exposed a difference between the scanner-only
SLSA identity check and the **real application OCI** gate. Full image provenance
is now bound directly to `qualification/release-image-v1.json`:
exactly one SLSA v1 predicate, pinned workflow builder ID and exact
application-manifest subject, even when a caller supplies a correctly
rehashed but different SLSA archive. A scanner-only success cannot waive it.

Build and qualification now have separate execution source domains.
The offline producer assembles chess evidence and an OCI archive in its
working copy. The independent RELEASE-1 verifiers run *after those steps*
from a new detached worktree at the exact head SHA; they do not import
the potentially modified producer checkout. ONLINE-4A and ONLINE-4B
independent jobs likewise create isolated worktrees and run
`verify_source_closure.py` against committed bytes *before* loading local
recovery code. The v2 verifier fetches its independently pinned bridge
checkout under its own verified root. This prevents a stale green HEAD from
authorizing a mutated executable module.

ONLINE-4B accepts only distinct, phase-indexed `bridge-phase-N.log` evidence
inside its scenario directory. ONLINE-4A refuses symlinked or
path-escaping replay manifests and `route.json` before hashing.
These checks are evidence admission rules, not declarations of successful
live network recovery or host/public-play approval.

## RELEASE-2A — dependency observation after merged PR #71

The next small, reviewable step is `RELEASE-2A_DEPENDENCY_OBSERVATION.md`.
Its source-attested observer reads the **real installed image** OS/Python
inventories and verifies all 12 staged Python wheels and both lock files
before retaining only their bounded hashes and package counts. The source
and application image build continue to be qualified by the original
RELEASE-1 workflow. Candidate-only observation does **not** freeze a
reproducibility lock, grant a signature or authorize distribution or live play.

### RELEASE-2A follow-up: complete installed/wheel name and version closure

The independent observer now checks exactly twelve staged wheel archives
plus `runtime-wheels.lock` and `wheel-attestation.json`—no extra .pth/.py
payload, directory, link or unlisted artifact. Each hash-verified wheel's
canonical filename distribution/version must equal its actual
`.dist-info/METADATA` and the installed `pip freeze --all` version. The
only nonwheel installed distribution admitted is venv-provided `pip`,
whose version remains an unapproved observation. This is dependency
**observation** rather than a frozen release lock, signed image, licensing
approval or public BOT deployment permission.

## RELEASE-2B-1 — Python source-selected wheel candidate

RELEASE-2B-1 replaces the live runtime resolver with the committed
`deploy/lichess/requirements.release.lock`. Its hashes select eleven
upstream wheels and one **chess sdist**, not a locally built chess wheel.
`qualification/release-python-wheels-v1.json` separately selects the
twelve exact CPython 3.12 linux/amd64 wheel filenames and SHA256 bytes.

The digest-pinned builder reconstructs chess twice in independent scratch
directories with frozen Python, tool wheels, environment and no build
network. Both must equal the previously observed chess wheel SHA.
Independently checked package metadata and runtime-wheels.lock are sealed
in the stage. The Ubuntu-provided venv pip==24.0 remains a separate native
origin exception, pending RELEASE-2B-2 provenance.

This is a **candidate** lock. It does not approve native APT/bootstrap
closure, dependency reproducibility, complete OCI byte reproduction,
licensing, host, credentials, public distribution or live Lichess operation.
See `docs/RELEASE_2B_PYTHON_LOCKS.md`.
