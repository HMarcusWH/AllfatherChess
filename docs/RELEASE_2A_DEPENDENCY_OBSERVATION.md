# RELEASE-2A: Independent dependency-inventory observations

> **Status synchronization (25 October 2026):** The original RELEASE-2A observation work was merged as [PR #72](https://github.com/HMarcusWH/AllfatherChess/pull/72). Wheel and native provenance **candidate selection** followed in merged #74 and #75. The underlying 120-row installed dependency inventory is observational, not an approved lock. The next separate steps are independent two-clean-builder comparison (RELEASE-2B-3) and reviewed lock admission (RELEASE-2B-4); see [remaining release work](RELEASE_2B_3_4_PLAN.md). No public release or distribution authorization.


**Status: evidence candidate, not a frozen lock or distribution permission.**

## Baseline and intent

[PR #71](https://github.com/HMarcusWH/AllfatherChess/pull/71) was
merged as `952a2c5cddff842efc5418f21e5987f51bcad6bf`. Its final
source-verified RELEASE-1 CI qualified the offline image, patched scanner,
SLSA v1/SPDX, four packaged chess/restart/SQLite cases, and host aggregate.
The release policy intentionally retains all OS and Python wheel lock flags
as **false** and all reviewed dependency hashes as **null**.

In the successful PR #71 RELEASE-1 evidence artifact
([run 39150491718](https://github.com/HMarcusWH/AllfatherChess/actions/runs/39150491718))
the installed Ubuntu image contained **120** sorted `dpkg-query` records and
`pip freeze --all` produced **13** Python distribution records. These are
real historical observations. They are not hard-coded thresholds and do not
establish byte-identical repeated builds.

## What this PR measures

`scripts/observe-release-dependencies.py` executes in RELEASE-1's isolated
exact-source Git worktree, after the installed image, packaged smoke, OCI
attestations and final independent aggregate checks pass.

It independently checks:

1. Source SHA/tree, stage manifest, executable's own source attestation and
   the exact immutable release-image policy SHA-256.
2. Bounded ASCII, LF-only, sorted, unique `name=version` OS package records;
   the required native packages must all appear, and foreign architectures
   or invalid versions are refused.
3. Bounded, sorted `name==version` installed Python distributions, with no
   unpinned/direct URL/VCS entries, duplicate names or unexpected control
   characters; pinned core bridge packages must be present.
4. Every one of the **12** staged wheel files against the staged wheel
   attestation, runtime installation lock, original source requirement lock
   and the candidate manifest. The complete wheelhouse directory must contain
   **only** those 12 wheel files, `runtime-wheels.lock` and
   `wheel-attestation.json`: extra .whl, .pth, .py, directories or symlinks
   are rejected. The installed `pip freeze --all` inventory must match the
   12 wheel distribution names and exact versions read from both wheel
   filenames and their actual ZIP `.dist-info/METADATA`. The venv-provided
   `pip` distribution is the **sole named exception**, still version-recorded
   and subject to future reproducibility review.

A successful observation explicitly records
`installed_wheel_versions_verified=true` and `wheelhouse_closed=true`
after byte-level and semantic package/version reconciliation. It does not
authorize those versions or create approved lock hashes.

The runner retains only a bounded structured report at
`build/test-results/release1/dependency-observation.json`, beside the
existing allowlisted offline diagnostic artifacts. No raw OCI archives,
wheel binaries, SBOM, provenance envelope or credentials are newly exposed.

The observer can be reproduced after all RELEASE-1 build inputs exist:

```sh
python3 scripts/observe-release-dependencies.py \
  --stage build/release1-stage \
  --os-inventory build/test-results/release1/os-packages.txt \
  --python-inventory build/test-results/release1/python-packages.txt \
  --source-lock deploy/lichess/requirements.lock \
  --source-sha "$(git rev-parse HEAD)" \
  --output build/test-results/release1/dependency-observation.json
```

In CI the executable comes from a *fresh Git-committed worktree* instead of
the mutable producer checkout. The output contains source policy/base/snapshot
identities, hashes and counts, but no authority-bearing release permissions.

## Acceptance and next step

This first PR qualifies that a single real build's installed dependencies
have been observed and byte-checked. It **never sets** any of these claims:

```text
dependency_lock_approved=false
cross_run_reproducibility_qualified=false
offline_candidate_qualified=false
artifact_distribution_authorized=false
public_bot_release=false
release_blocked=true
```

The next lock-freeze review requires at least two independently run,
exact-source and exact-toolchain builds, matching full OS + Python records,
runtime and source lock bytes, wheel attestations and wheel binaries.
Investigate bootstrap CA version and package-source availability separately.
Disagreements are failures or unresolved findings, never instructions to
update a baseline blindly.

Legal/controller licensing, corresponding source, host qualification, live
transport, operator consent and publisher signature remain wholly separate
gates, including after any future OS/wheel lock approval.
