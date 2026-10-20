# RELEASE-2B-2 — Ubuntu signed snapshot and native dependency candidate

**Candidate only. No public bot, live transport, host or dependency-lock approval.**

## Starting point

RELEASE-2B-1 merged at commit 1c54dfac5d8ae1d9d25b93a8d72b9efbc4ec0b50.
The release base is the exact Ubuntu OCI index digest recorded in
qualification/release-image-v1.json, platform linux/amd64.
Both runtime Ubuntu source stanzas already use
Snapshot: 20261001T000000Z with a system Ubuntu archive keyring.

The historical CA bootstrap used the two signed official native HTTP
repositories without Snapshot. Those indexes were moving.

## First test: signed HTTP snapshot CA acquisition

Both bootstrap stanzas now require the identical timestamp, preserving
the Ubuntu Signed-By trust anchor and refusing floating fallback.
deploy/release/Dockerfile.native-probe is disposable and intentionally
not claimed to be the production release dependency stage. It checks
real APT acquisition, signed index selection, exact candidate CA version,
the SHA256 in the authenticated package index against the downloaded
deb, installed CA version and base/bootstrap dpkg inventories.

An independent RELEASE-2B-2 CI run must show the Ubuntu pinned base can
actually acquire and install a certificate package through signed HTTP
snapshot semantics. Source syntax alone never proves this.

The observed 120-row final native package inventory from RELEASE-2A
is preserved as an unapproved candidate in
qualification/release-os-packages.lock. This is not the base inventory
and does not prove package acquisition origin. All actual acquired and
upgraded deb filename/version/hash/index/suite records remain to be
observed before review.

## Further work for RELEASE-2B-2

Reconcile and review the exact pinned base package inventory, acquired
APT package hashes and signed InRelease/Packages linkage, and final
120-row installed inventory. Refactor the actual release Dockerfile to
use a single release-dependencies stage inherited by release-application,
without any second subtly different installer. Run the full existing
RELEASE-1 OCI/SPDX/SLSA, real offline smoke and negative tests.

Keep ca_bootstrap_snapshot_locked, os_package_lock_complete and
wheel_lock_complete false until separately reviewed approval in 2B-4.
All public/licensing/live/host/credentials authorization remains false.
A signed HTTP snapshot failure is a bounded blocker; do not work around
it using floating repositories, trusted=yes or disabled GPG/TLS checks.

## First probe failure and alternative transport experiment

The initial real signed-HTTP snapshot Docker build (GitHub Actions run
39346907615) failed with CA trust errors: APT converts the requested
Snapshot field into HTTPS URLs under snapshot.ubuntu.com, which the
minimal pinned Ubuntu base cannot verify before ca-certificates exists.

This is a genuine bootstrap dependency cycle, not a permission to use
live repos or disable TLS. The next disposable probe borrows only the
certificate bundle from the already digest-pinned RELEASE-2B-1 Python
wheel builder image. That additional trust root is explicitly
**unreviewed**, used only in the diagnostic Dockerfile.native-probe,
and not permitted inside the production release image. The experiment
tests feasibility of the signed Ubuntu snapshot *after* transport CA
trust is supplied.

Even a green experiment cannot promote native locks until a separate
exact signed-index-bound CA .deb bootstrap and complete provenance
are reviewed, and the actual release-dependencies stage is qualified.

## Reproducible snapshot bootstrap acquisition evidence

The disposable source-bound build at GitHub run 39347616409 (job
118384046507; artifact 12231726879) passed after removal of APT's
discovery-only live indexes from the selection cache. APT authenticated
the final pinned snapshots, and the downloaded CA .deb SHA agreed with
the signed-index APT package metadata.

Observed candidate identities:
- base Ubuntu dpkg inventory: 92 records; SHA256
  c9ec22da6aac2bed9dff11162a66e5bb4bf4c15f3485528ac4358901cb1156f5
- after CA bootstrap: 94 records; SHA256
  43165ad782a6d9f161cf3e60f4846cfe051a35d8fa9f0170ab0046ae4acb33a9
- ca-certificates=20260601~24.04.1 .deb SHA256
  6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50
- pinned Ubuntu archive keyring SHA256
  80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31

The exact frozen InRelease SHA256 values for noble, noble-updates,
and noble-security are recorded in the candidate bootstrap policy and
must remain identical. The next revision adds direct gpgv checking and
captures the ephemeral transport trust-bundle hash. This evidence is
candidate observation, not independent release attestation or public authority.

## Exact base and post-bootstrap inventories from the authenticated probe

The CI job 118385068752 retained and printed the **complete 92-row
base image dpkg inventory** and **94-row post-bootstrap inventory** from
the same digest-pinned Ubuntu image. Both are now committed as measured,
unreviewed evidence in qualification/release-os-packages.lock. The distinct
historical full release inventory remains 120 rows.

The CA bootstrap installs ca-certificates and openssl and upgrades
libssl3t64. These are observed package-name/version transitions only;
only the CA .deb currently has measured SHA. The openssl and libssl3t64
.deb SHA256/index provenance remains an explicit blocker.

The temporary transport trust bundle has the measured SHA256
714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5.
This remains an **experimental, unreviewed trust input**, not a
production authority. Direct gpgv calls in the successful CI verified
all three frozen InRelease signatures under Ubuntu archive key
F6ECB3762474EDA9D21B7022871920D1991BC93C.

No lock approval flags have been raised. The actual release Dockerfile now
uses one shared native/Python dependency stage, but neither this candidate
nor its unreviewed transport CA is approved for public distribution.

## Completed production dependency repair — RELEASE-2B-2 candidate

PR #75 now contains one actual `release-dependencies` Docker stage
inherited by `release-application`, instead of two different native or
Python installers. Both source-declared `Snapshot:` stanzas remain fixed at
20261001T000000Z. During runtime package acquisition the build derives
direct dated snapshot URLs to eliminate APT's moving discovery indexes
from package selection, retains the Ubuntu Signed-By keyring requirement,
and verifies every retained InRelease with gpgv.

The bootstrap's provisional transport CA bundle is copied from the exact
digest-pinned Python wheel-builder image and checked against the frozen
714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5
SHA256. Ubuntu's exact, signed native ca-certificates package then replaces
the bootstrap transport trust through update-ca-certificates --fresh.
The trust source remains marked unreviewed and candidate-only.

The source-controlled direct native selections have ten pinned name/version
records. The real stage performs a download-only acquisition against the
frozen Ubuntu snapshot, retains downloaded .deb SHA256 and APT package-index
records, and installs the full package set with the existing bounded
retry/fail-closed classifier.

The independently checked complete package inventories remain **92 base**,
**94 after bootstrap**, and **120 final**. The native lock now contains
three actual bootstrap .deb hashes and 26 runtime added/upgraded .deb hashes.
The seven exact snapshot InRelease hashes (three bootstrap and four runtime)
and their successful gpgv source identity are frozen as candidate evidence.

The installation includes exactly twelve previously selected Python wheels,
installed with --no-index and --require-hashes. There is no engine, move,
resource router or chess-authority code change in this PR.

### Exact CI evidence and claim limits

At GitHub Actions run 39354219892, all of the following completed green:

- source-bound native candidate/negative contracts;
- actual signed Ubuntu CA bootstrap;
- the real production `release-dependencies` build, with offline Python
  wheels and independent exact-commit verification of the whole native
  package and downloaded wheelhouse closure.

This is a **candidate package acquisition and installation integrity result**,
not external independent two-job reproducibility attestation. That comparison
belongs to RELEASE-2B-3. Separate reviewed dependency-lock admission belongs
to RELEASE-2B-4. No entire OCI byte-reproduction or licensing/public/host/
live-network/playing-strength claims follow from 2B-2's success.

The full RELEASE-1 OCI/SPDX/SLSA and genuine packaged-smoke workflow must be
rechecked at the final exact head before PR #75 can be considered merge-ready.
The repository's external authority and lock-approval booleans remain FALSE.
