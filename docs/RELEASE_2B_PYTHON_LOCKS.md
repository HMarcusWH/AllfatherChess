# RELEASE-2B-1 — Frozen Python source and selected wheel candidate

> **Current state (25 October 2026):** Candidate **implementation merged** in [PR #74](https://github.com/HMarcusWH/AllfatherChess/pull/74). The twelve source-pinned distribution/wheel choices and source-locked builder provide testable acquisition/repeatability evidence, not independent multi-build lock approval. RELEASE-2B-3 must compare separate clean builds and repeat on the same source SHA; RELEASE-2B-4 must separately review/admit a lock. `wheel_lock_complete=false` and distribution permission remain unchanged. See [the remaining plan](RELEASE_2B_3_4_PLAN.md).


**Status: candidate only; no dependency-lock approval or public deployment.**

## Evidence inputs and strict selection

The declared top-level dependencies are in deploy/lichess/requirements.in.
Their twelve fully pinned transitive versions and download hashes are in
deploy/lichess/requirements.release.lock. Eleven of those hashes identify
upstream published wheels. The chess==1.11.2 entry identifies **only** the
upstream sdist; the locally built wheel has a separate, historical observed
SHA-256 in qualification/release-python-wheels-v1.json.

The selected-wheel policy binds exactly twelve wheel filenames and hashes
for CPython 3.12 on linux/amd64. A different ABI, platform, or byte stream
is not admitted even when pip would otherwise accept it.

The toolchain policy, qualification/release-python-builder-v1.json, binds a
digest-pinned Python 3.12.14 image and the original GitHub CI-observed pip 24.0 / setuptools 68.1.2 / wheel 0.42.0 build toolchain, digest-checked downloads, fixed
locale, timezone, hash seed, SOURCE_DATE_EPOCH and the build command.
The separate Dockerfile.wheel-builder is **not** the application image.
The Ubuntu 24.04 venv-provided pip==24.0 is tracked as a nonwheel
installer exception. Native origin closure is pending RELEASE-2B-2.

## Local preparation on a supported host

Use a Linux amd64 Docker host with CPython 3.12:

    python3 scripts/verify-release-python-lock.py --root .
    python3 scripts/prepare-online3b-wheels.py \
      --source-lock deploy/lichess/requirements.release.lock \
      --wheelhouse build/online3b-wheels
    python3 scripts/verify-release-python-lock.py \
      --wheelhouse build/online3b-wheels

The preparer uses pip download --require-hashes for source acquisition.
It checks the exact filenames and bytes *before* local construction.
It then creates a pinned builder and runs **two distinct scratch-directory
builds** of the identical chess sdist with no build-stage network.
Both outputs must have the exact historical wheel SHA and identical bytes.
It emits a runtime-wheels.lock differing from the committed source lock
only in the chess sdist-to-wheel SHA, plus source-bound attestation.
No mismatch can rewrite the approved candidate input.

Only twelve wheels, runtime-wheels.lock and wheel-attestation.json are
allowed in a completed wheelhouse. The independent source verifier
checks hashes, wheel ZIP metadata, ABI tags and closed directory content
before stage admission. It can optionally reconcile the installed
Python inventory, including the exact pip exception.

## Evaluation, limits and non-grants

PY-01 through PY-09 reject top-level requirement changes, alternate
platforms, changed bytes, chess-source and builder drift, unexpected
installed packages, mismatched wheel metadata and extra files/links.
Actual pinned-builder/OCI/bridge integration jobs must also pass at the
exact head before merging; unit tests do not establish build equivalence.

The twelve candidate byte hashes came from earlier retained CI
evidence. Source and build-tool archive hashes were checked against
published PyPI records. This PR must independently download the bytes
and verify them; the observation alone is not proof of reproduction.

Two scratch builds inside one builder do not constitute independent
GitHub-hosted executions or third-party attestation. RELEASE-2B-3
will add independent builds, artifact provenance comparison and an
exact-SHA repeat. RELEASE-2B-2 will freeze Ubuntu signed CA/runtime
APT inputs. Whole OCI byte reproducibility is not claimed.

**The current release remains offline-only:** verify and offline-smoke
are the only permitted entrypoints. Public distribution, licensing,
credentials, live Lichess operation, host admission, playing strength
and release-policy promotion remain prohibited. Repository branch
protection is a separate governance task.

## Historical builder reconstruction

The original RELEASE-1 exact-head GitHub Actions job 117833117730 explicitly
recorded `pip==24.0`, `setuptools==68.1.2`, and `wheel==0.42.0`
and a `setup.py` chess wheel of **147,776 bytes** with SHA-256
`34bb710720cf88b78dda74a7a56892d376f84d13b1715d9113e98b7fa21685b4`.
The first 2B-1 candidate used pip 25.2 / setuptools 83.0.0 instead, which
reproducibly produced a different **147,878-byte** wheel of SHA-256
`b5772e0e46b1cbb502ce78e1045009bb6541a316da92bd1a8c9f46e83772227e`.
This mismatch was rejected. The builder was revised to the historical
versions; it remains a testable candidate until exact-head requalification.

### Timestamp provenance and exact historical reproduction

The first historical-toolchain attempt reproduced the original 147,776-byte
wheel size but produced SHA-256 `d3cef0507f6911c9d825661f27ab325863352b3444fcdb1b6fb8120cff65de27`
because its `SOURCE_DATE_EPOCH=1740500000` differed from the original.
Removing `SOURCE_DATE_EPOCH` caused fresh independent builds to diverge,
which was also correctly refused.

The actual RELEASE-1 job logs for job `117833117730` identify
`SOURCE_DATE_EPOCH=1720000000`. Restoring that precise value with
`pip==24.0`, `setuptools==68.1.2` and `wheel==0.42.0` yielded
**two separate clean builds** of the historical
`chess-1.11.2-py3-none-any.whl`, each 147,776 bytes and each
SHA-256 `34bb710720cf88b78dda74a7a56892d376f84d13b1715d9113e98b7fa21685b4`.

GitHub Actions candidate-only reproducibility run `39342057912`, job
`118365768904`, passed the twelve-wheel byte/metadata/source verifier.
It independently reacquired all eleven upstream published wheel bytes and
one original chess sdist before construction. This is not independent
two-job build attestation, OS dependency admission or distribution approval.
