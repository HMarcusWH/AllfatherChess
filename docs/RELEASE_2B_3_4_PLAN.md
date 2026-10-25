# RELEASE-2B-3 / 2B-4 — reproducible dependency candidate and reviewed lock

**Source-grounded status, 25 October 2026:** This is an implementation plan,
not an approval or completed qualification. RELEASE-2B-1 [PR #74](https://github.com/HMarcusWH/AllfatherChess/pull/74)
merged source-selected and exact-wheel-byte candidates for 12 Python
dependencies; RELEASE-2B-2 [PR #75](https://github.com/HMarcusWH/AllfatherChess/pull/75)
merged signed Ubuntu snapshot/native acquisition and candidate environment
evidence. PR #77 hardened unrelated CI host APT prerequisites. No 2B-3
independent comparison or 2B-4 approval has been performed in this baseline.

This plan follows the attached *AllfatherChess RELEASE-2B Complete
Implementation Specification* (2026-10-20), §§0, 6–7. Status and exact
source inputs are checked against [CURRENT_STATUS](CURRENT_STATUS.md)
and the repository policy, not the specification's historical `main` SHA.

## 1. Exact source-input contract

- `qualification/release-image-v1.json`: Ubuntu OCI base digest,
  `linux/amd64`, signed APT snapshot `20261001T000000Z`, and existing
  no-egress/offline runtime boundaries.
- `qualification/release-bootstrap-v1.json` and
  `qualification/release-os-packages.lock`: snapshot trust sources,
  native archive version/architecture/pool path/SHA256 and signed
  `InRelease` → `Packages` provenance, plus 92 base, 94 bootstrap,
  120 final observed dpkg inventories. These are the observed candidate
  bounds, not unqualified generic-host measurements.
- `deploy/lichess/requirements.release.lock`,
  `qualification/release-python-wheels-v1.json` and
  `qualification/release-python-builder-v1.json`: twelve Python
  dependencies and exact wheel choices, builder identity and hashes.
- `qualification/release-native-build-v1.json`: candidate dependency
  stage and installed-environment identity.

**Meaning of "reproducible" here:** Eleven third-party Python wheels are
reacquired and compared to their pinned bytes; they are **not** rebuilt
from upstream source. Only the pinned `chess==1.11.2` sdist is rebuilt
into a checked wheel. Ubuntu packages are signed-source-acquired, not
recompiled from Ubuntu source. Matching inventories do not imply
bit-for-bit reproduction of the whole OCI image.

## 2. RELEASE-2B-3 — independent execution and evidence comparison

The candidate reproducibility PR must freeze a separate source closure,
execute **two genuine clean build jobs** with independent full checkouts,
builder instances and wheelhouse acquisition, and retain a third,
source-independent verification job. No sibling build imports the other
builder's output or shares a mutable acquisition cache.

### Required planned paths

| Path | Responsibility |
| --- | --- |
| `qualification/release-dependency-build-v1.json` | Frozen source/input/toolchain/host/receipt policy |
| `qualification/schemas/release-dependency-receipt-v1.json` | Typed and bounded build evidence schema |
| `qualification/schemas/release-dependency-comparison-v1.json` | Independent comparison/verdict schema |
| `scripts/verify-release-dependency-build.py` (extend existing) | Exact source/installed-native/Python inventory verification; candidate receipt |
| `scripts/compare-release-dependencies.py` (new) | Rebuild receipt identity, compare both complete observed inventories/wheel maps and validate CI evidence |
| `.github/workflows/release-dependency-repro.yml` (new) | Contracts → build A/B → independent compare → explicit summary |
| `tests/release/test_release_dependency_comparison.py` (new) | Tampered source/job identity, missing evidence, mismatches and skipped-run destructive controls |
| `docs/RELEASE_2B_REPRODUCIBILITY.md` (future implementation PR) | Run and artifact-custody procedure |

These filenames are **planned**; their absence from the present PR is
expected and must not be presented as completed implementation.

```text
source-contracts + frozen exact source SHA
                  |
            dependency-contracts
                  |
     +------------+------------+
     |                         |
dependency-build-a       dependency-build-b
clean Git checkout       clean Git checkout
isolated Builder A       isolated Builder B
     |                         |
     +------------+------------+
                  |
         independent-compare
         (third clean source)
                  |
        qualification-summary
            PASS / FAIL /
            INCONCLUSIVE
```

Both producers must attest exact source SHA/tree/input-closure hashes,
the real signed APT source evidence, complete OS/Python inventories,
twelve wheel filenames/SHA256, builder/platform identities, executable
dependencies, **actual** GitHub job/run/artifact provenance and
well-typed negative dispositions. Raw evidence must be uploaded before
producer verdicts can be read as authoritative. A `run_id` printed
inside JSON is not proof it came from the claimed GitHub job; the
independent verifier must corroborate externally obtainable execution/
artifact metadata or fail/inconclusive.

**Claim ceiling:** The third job can independently inspect retained
sanitized receipts and source and compare the builders' measured
inventories/hashes; it **cannot rehash raw wheel bytes which are not
retained for that job**. This is *independent execution and evidence
comparison*, **not** cryptographic third-party build attestation.

Retain one independent complete compare verdict for two distinct build
jobs, then repeat the **entire DAG in a second distinct Actions run on
the exact same Git SHA and dependency closure**. Require both executions
to succeed and retain their distinct run/job IDs, artifact digests and
comparison verdicts. Fail on skipped/cancelled/missing prerequisite
jobs; no summary may be green by default when the comparator never ran.

A mismatch, changed signed `Packages` origin, wrong architecture,
altered wheel metadata, missing hash, unexpected mutable fallback,
unverified job separation, bad package post-install behavior, expired/
missing artifacts, or absent repeat must return NOT QUALIFIED, with
sanitized failure evidence preserved.

RELEASE-2B-3 can at most qualify a reproducible **dependency candidate**;
it must **not** turn on the positive lock or public release flags.

## 3. RELEASE-2B-4 — distinct reviewed lock admission

Only after all 2B-3 independent candidate evidence exists, submit a
**separate** source-reviewed approval PR. Its eventual
`qualification/release-dependency-lock-v1.json` must be the single
approved input-closure authority, binding real signed native origins,
installed package inventory, Python source/wheel SHA identities,
builder identities, immutable evidence and an independently verified
approval disposition.

Required source changes **in that future PR only**:

- `scripts/qualify-release-dependency-lock.py`: independently derive
  `FROZEN_APPROVED` from source, observed bytes and comparison evidence,
  never a producer's self-reported PASS.
- `scripts/observe-release-dependencies.py`: add explicit candidate and
  approved verification modes while still rechecking *actual* package
  and wheel bytes.
- `qualification/release-image-v1.json`: populate approved OS/wheel
  hash slots and permit `os_package_lock_complete` /
  `wheel_lock_complete=true` **only** under the source-admitted
  independent approved-lock verdict; set
  `ca_bootstrap_snapshot_locked=true` only when that proof is complete.
- `scripts/check-current-status.py` and
  `qualification/current-status-v1.json`: migrate today's false-only
  **dependency lock** guard to an enumerated independently verified
  candidate/approved state; preserve the unrelated false-only public/
  license/host/network/strength denials. This docs PR deliberately does
  not make that migration.
- `scripts/qualify-release-aggregate.py`,
  `.github/workflows/release-candidate.yml`, and applicable
  source/seal/dependency tests: require an approved verifier result,
  not only matching policy flags plus digest fields.

The approval PR necessarily has a **different Git commit SHA** from the
candidate PR. Admit the older candidate evidence only under exactly
unchanged dependency-input closure, then rerun the complete offline
RELEASE-1 OCI/SLSA/SPDX/source/engine/bridge/packaged-game,
source-status and LOCAL-1 qualifications **at the new approval SHA**.
The final OCI digest may differ; do not falsely attribute the older
image to the approval commit.

## 4. Mandatory remaining denials after an approved dependency lock

Lock approval cannot grant licensed combined distribution, publisher
signature, a qualified physical destination host, live transport,
public Bot API use, network-failure recovery, or superior chess
strength. Even in the future `FROZEN_APPROVED` dependency state,
`public_bot_release=false`,
`artifact_distribution_authorized=false`,
`offline_candidate_qualified=false`,
`destination_host_qualified=false`,
`network_recovery_qualified=false`,
`live_transport_qualified=false`, and human legal review remains
separate. RELEASE-1 still only admits `verify` and `offline-smoke`.

See [RELEASE-1](RELEASE_1_OPERATIONS.md),
[2B Python candidates](RELEASE_2B_PYTHON_LOCKS.md),
[2B Ubuntu candidates](RELEASE_2B_UBUNTU_LOCKS.md),
[PR-B](ONLINE_PLAY_1_PR_B_READINESS.md),
and [claims](CLAIM_LEDGER.md).
