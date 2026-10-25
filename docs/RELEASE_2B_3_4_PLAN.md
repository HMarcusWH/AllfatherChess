# RELEASE-2B-3 / 2B-4 — independent dependency comparison and reviewed admission

**Status: proposed implementation work, not a source-level approval.**
RELEASE-2B-1 [PR #74](https://github.com/HMarcusWH/AllfatherChess/pull/74)
merged twelve exact selected Python source/wheel candidates.
RELEASE-2B-2 [PR #75](https://github.com/HMarcusWH/AllfatherChess/pull/75)
merged the signed Ubuntu snapshot and 29 source-selected native package
acquisition/installation candidate. PR #77 later repaired the shared CI-host
signed APT prerequisite. The current policies deliberately retain
`os_package_lock_complete=false`, `wheel_lock_complete=false`,
`ca_bootstrap_snapshot_locked=false` and all release/host/live/public flags
false. These booleans are not implementation TODOs for this documentation PR.

## 1. Evidence inputs and identity contract

- `qualification/release-image-v1.json`: pinned Ubuntu OCI index,
  `linux/amd64`, 20261001T000000Z snapshot, offline runtime limitations.
- `qualification/release-bootstrap-v1.json`:
  Signed-By/trust-root/snapshot metadata candidate.
- `qualification/release-os-packages.lock`:
  source-selected versions/architectures/pool files/native archive SHA256,
  signed Packages/InRelease membership, 92 base / 94 bootstrap / 120 final
  observed inventories (not universally assumed host facts).
- `deploy/lichess/requirements.release.lock` and
  `qualification/release-python-wheels-v1.json`: twelve pinned Python
  dependency source resolutions and exact wheel filenames/bytes.
- `qualification/release-python-builder-v1.json` and
  `qualification/release-native-build-v1.json`: candidate toolchain and
  build-envelope policy.

A retained candidate hash is an integrity predicate; it does not prove two
builders independently reproduced it.

## 2. RELEASE-2B-3 — independent clean-build comparison

Implement a bounded workflow with **two separate clean dependency builds**,
each on the *same immutable source SHA*, with independent workspace/cache
identity, signed Ubuntu acquisition rechecked, networkless post-admission
package installation, pinned wheel builder and source lock, and exact final
package and wheel inventories.

Each builder must upload immutable raw manifests containing Git SHA/tree,
run/job identity, runner architecture/OS, base digest, signed apt
InRelease/Packages source proofs, native archive hashes, installed dpkg
inventory, full wheel file names and SHA256, builder policy and actual
dependency-stage image/bundle identity. No real BOT token.

Add a **third, source-independent comparator** (planned:
`scripts/compare-release-dependencies.py`) that reopens *both* retained
artifacts, independently recomputes their hashes, verifies their source/source
index membership and schema, rejects missing or duplicate files, and
cross-compares native/wheel and image-stage evidence. It must not trust a
producer's own `passed` flag.

Then rerun/recompare with the **same SHA** but new run/attempt identity. A
mismatch, missing signature, wrong architecture, signed index drift, URL
fallback, unpinned dependency, altered wheel metadata or package post-install
download produces a recorded **NOT QUALIFIED**, not an automatically repaired
or retroactively asserted PASS. Include destructive fixture mutations.

### Minimum proposed repository wiring (not yet present)

- `scripts/compare-release-dependencies.py`: independent two-builder reader
  and comparator.
- `tests/release/test_compare_release_dependencies.py`: malicious/negative
  fixtures and exact positive evidence.
- `.github/workflows/release-2b-reproducibility.yml`: two producers,
  independent evidence verifier and same-SHA second attempt.
- Optional source-bound evidence schema under `qualification/schemas/`,
  reviewed with the implementation rather than inventing approval flags.

The exact filenames above are work-package targets; their existence and
successful execution must be verified in the future PR.

## 3. RELEASE-2B-4 — separate dependency-lock approval

A **different PR/review** must consume complete 2B-3 outputs, independently
recheck signatures, source identity, byte equality and external license
obligations, then decide whether a source-controlled lock-admission
record can be approved. A planned record such as
`qualification/release-dependency-lock-v1.json` must never be committed
as `approved=true` without actual human review and portable evidence.

Re-run the complete exact-head offline RELEASE-1 OCI, runtime entrypoint,
engine/bridge source, SPDX/SLSA, SQLite/PGN, image and no-publication gates.
All `public_bot_release`, artifact distribution, host, live transport and
playing-strength authority remain **separate false gates** even after a valid
dependency lock.

## 4. Non-transfer claims and acceptance

RELEASE-2B-3/4 prove independent dependency acquisition/build reproducibility
only for the declared platform and input domain. They do not prove the full
OCI byte stream reproducible unless that was separately measured; nor do they
grant a combined license, signed publisher approval, real Lichess account,
network recovery or engine superiority.

Cross-references: [RELEASE-1](RELEASE_1_OPERATIONS.md),
[2B Python](RELEASE_2B_PYTHON_LOCKS.md),
[2B native](RELEASE_2B_UBUNTU_LOCKS.md),
[PR-B readiness](ONLINE_PLAY_1_PR_B_READINESS.md),
and [CURRENT_STATUS](CURRENT_STATUS.md).
