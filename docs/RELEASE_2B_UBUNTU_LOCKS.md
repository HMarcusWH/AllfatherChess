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
