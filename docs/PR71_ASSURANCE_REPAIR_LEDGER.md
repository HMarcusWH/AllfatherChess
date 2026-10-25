# PR #71 — Assurance Repair and Review Closure Ledger

> **Dated PR #71 historical review ledger (25 October 2026 synchronization).**
> The original `pending` and `candidate` review findings below describe
> earlier exact-head work. PR #71 merged a source-verified offline-only assurance
> pipeline; source and CI evidence, not the mere presence of a repair sentence,
> determines a claim. Subsequent #74/#75 dependency locks are **unapproved**
> candidates and #76's live-capable gateway has only synthetic fake-API
> qualification. See [CURRENT_STATUS](CURRENT_STATUS.md) and
> [CLAIM_LEDGER](CLAIM_LEDGER.md).


**Scope:** source-confirmed repair chain for PR #71, based on original head `887acbb2b1f33c65a9a322eae32fa1327ce3d203`. This ledger is historical and must be updated against the final pushed SHA and latest CI evidence before merger.

**Authority:** Offline assurance only. No public bot, real credentials, production host qualification, publisher signature, licensing approval, artifact distribution, Elo or deployment authority.

## Existing qualification blockers (observed October 17, 2026)

| Issue | Evidence | Targeted repair | Exit condition |
| --- | --- | --- | --- |
| Invalid move path retained | ONLINE-3B/4A/4B reject `/api/bot/game/off3b001/move/garbage` retained after UCI redaction | Strict complete-endpoint publication at fake server/trace and report serializer; adversarial tests | Three real suites green, final artifacts contain no malformed path canaries |
| Scanner builder identity | RELEASE-1 pinned Syft/BuildKit scanner fails `SLSA v1 builder identity or field shape invalid` | Pin declared provenance builder ID for the real scanner probe; separate builder object/id/version/dependency diagnostics | Pinned scanner and full OCI integration green without weakening the validator |

## Non-outdated Codex threads requiring evidence review

These threads are **not** mechanically closed by source presence. For each, retain test/CI evidence, determine whether the original counterexample is rejected or valid input is admitted, and only then resolve or explain its disposition.

| Comment | Source | Finding | Status |
| --- | --- | --- | --- |
| [r4263102049](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102049) | `scripts/qualify-release-smoke.py` | <sub><sub></sub></sub>  Bind the R02 unknown ACK to the move endpoint | Source regression/CI review pending |
| [r4263102056](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102056) | `scripts/verify-release-oci.py` | <sub><sub></sub></sub>  Validate the attestation subject descriptor size | Source regression/CI review pending |
| [r4263102061](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102061) | `scripts/qualify-release-smoke.py` | <sub><sub></sub></sub>  Require client-side evidence of the R10 response | Source regression/CI review pending |
| [r4265369283](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265369283) | `scripts/qualify-release-smoke.py` | <sub><sub></sub></sub>  Validate the persisted cooldown deadline | Source regression/CI review pending |
| [r4303841556](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303841556) | `tests/lichess/fake_lichess_api.py` | <sub><sub></sub></sub>  Attest the imported HTTP target policy | Source regression/CI review pending |
| [r4304011435](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304011435) | `scripts/qualify-online-recovery-v2.py` | <sub><sub></sub></sub>  Validate raw targets in ONLINE-4B request evidence | Source regression/CI review pending |
| [r4304011445](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304011445) | `scripts/verify-release-oci.py` | <sub><sub></sub></sub>  Enforce URI delimiters per component | Source regression/CI review pending |
| [r4304111685](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111685) | `qualification/online-recovery-v2.json` | <sub><sub></sub></sub>  Attest every direct recovery dependency | Source regression/CI review pending |
| [r4304111688](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111688) | `scripts/qualify-online-recovery-v2.py` | <sub><sub></sub></sub>  Reject unsuccessful canonical requests in ONLINE-4B | Source regression/CI review pending |
| [r4304111692](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111692) | `scripts/check-release-scanner.py` | <sub><sub></sub></sub>  Reject non-object rows in scanner statement arrays | Source regression/CI review pending |
| [r4304111718](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111718) | `scripts/qualify-release-smoke.py` | <sub><sub></sub></sub>  Bind every committed origin to the side to move | Source regression/CI review pending |
| [r4304394201](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394201) | `scripts/verify_assurance_contract.py` | <sub><sub></sub></sub>  Enforce the declared image-integrity prerequisites | Source regression/CI review pending |
| [r4304394202](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394202) | `.github/workflows/online-recovery-v2.yml` | <sub><sub></sub></sub>  Trigger recovery CI for every new verifier dependency | Source regression/CI review pending |
| [r4304394206](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394206) | `scripts/check-release-scanner.py` | <sub><sub></sub></sub>  Validate complete in-toto objects in every scanner row | Source regression/CI review pending |
| [r4304394212](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394212) | `deploy/release/install-pinned-apt.sh` | <sub><sub></sub></sub>  Permit the standard transient install summary | Source regression/CI review pending |
| [r4304394221](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394221) | `scripts/verify-release-oci.py` | <sub><sub></sub></sub>  Validate required SPDX package fields | Source regression/CI review pending |

## Full historical inline-review inventory

GitHub recorded 58 threads at the time of ledger construction: 42 outdated diff contexts, 16 non-outdated, and 0 formally resolved. "Outdated" means changed diff location, **not** independent proof of repair.

| Comment | Date (UTC) | Path | Diff context |
| --- | --- | --- | --- |
| [r4253078059](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253078059) | 2026-10-12 23:00 | `scripts/verify-release-oci.py` | Outdated |
| [r4253078070](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253078070) | 2026-10-12 23:00 | `deploy/release/offline_smoke.py` | Outdated |
| [r4253714587](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714587) | 2026-10-13 01:00 | `scripts/qualify-release-smoke.py` | Outdated |
| [r4253714591](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714591) | 2026-10-13 01:00 | `scripts/qualify-release-smoke.py` | Outdated |
| [r4253714599](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714599) | 2026-10-13 01:00 | `scripts/verify-release-oci.py` | Outdated |
| [r4253714605](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714605) | 2026-10-13 01:00 | `scripts/verify-release-oci.py` | Outdated |
| [r4253714615](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714615) | 2026-10-13 01:00 | `scripts/inspect-release-oci.py` | Outdated |
| [r4263102049](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102049) | 2026-10-13 19:30 | `scripts/qualify-release-smoke.py` | Current |
| [r4263102056](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102056) | 2026-10-13 19:30 | `scripts/verify-release-oci.py` | Current |
| [r4263102061](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102061) | 2026-10-13 19:30 | `scripts/qualify-release-smoke.py` | Current |
| [r4263102073](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102073) | 2026-10-13 19:30 | `scripts/verify-release-oci.py` | Outdated |
| [r4265369277](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265369277) | 2026-10-14 00:02 | `scripts/verify-release-oci.py` | Outdated |
| [r4265369279](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265369279) | 2026-10-14 00:02 | `scripts/verify-release-oci.py` | Outdated |
| [r4265369283](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265369283) | 2026-10-14 00:02 | `scripts/qualify-release-smoke.py` | Current |
| [r4265635092](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265635092) | 2026-10-14 00:50 | `scripts/verify-release-oci.py` | Outdated |
| [r4265635097](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265635097) | 2026-10-14 00:50 | `scripts/qualify-release-smoke.py` | Outdated |
| [r4265635102](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265635102) | 2026-10-14 00:50 | `scripts/verify-release-oci.py` | Outdated |
| [r4265635112](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4265635112) | 2026-10-14 00:50 | `scripts/verify-release-oci.py` | Outdated |
| [r4271701069](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4271701069) | 2026-10-14 13:54 | `scripts/verify-release-oci.py` | Outdated |
| [r4271701077](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4271701077) | 2026-10-14 13:54 | `scripts/verify-release-oci.py` | Outdated |
| [r4271701092](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4271701092) | 2026-10-14 13:54 | `scripts/verify-release-oci.py` | Outdated |
| [r4272459243](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4272459243) | 2026-10-14 14:58 | `scripts/qualify-release-aggregate.py` | Outdated |
| [r4272459258](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4272459258) | 2026-10-14 14:58 | `scripts/inspect-release-oci.py` | Outdated |
| [r4272459266](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4272459266) | 2026-10-14 14:58 | `scripts/verify-release-oci.py` | Outdated |
| [r4272459279](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4272459279) | 2026-10-14 14:58 | `scripts/qualify-online-recovery-v2.py` | Outdated |
| [r4272459292](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4272459292) | 2026-10-14 14:58 | `scripts/check-release-snapshot.py` | Outdated |
| [r4272459302](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4272459302) | 2026-10-14 14:58 | `deploy/release/install-pinned-apt.sh` | Outdated |
| [r4303673549](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303673549) | 2026-10-17 17:14 | `scripts/verify-release-oci.py` | Outdated |
| [r4303673561](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303673561) | 2026-10-17 17:14 | `scripts/release_http_contract.py` | Outdated |
| [r4303673565](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303673565) | 2026-10-17 17:14 | `scripts/qualify-online-recovery.py` | Outdated |
| [r4303673570](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303673570) | 2026-10-17 17:14 | `scripts/inspect-release-oci.py` | Outdated |
| [r4303673578](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303673578) | 2026-10-17 17:14 | `scripts/verify-release-oci.py` | Outdated |
| [r4303673584](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303673584) | 2026-10-17 17:14 | `deploy/release/install-pinned-apt.sh` | Outdated |
| [r4303841556](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303841556) | 2026-10-17 18:01 | `tests/lichess/fake_lichess_api.py` | Current |
| [r4303841560](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303841560) | 2026-10-17 18:01 | `scripts/verify-release-oci.py` | Outdated |
| [r4303841564](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303841564) | 2026-10-17 18:01 | `tests/lichess/fake_lichess_api.py` | Outdated |
| [r4303841565](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303841565) | 2026-10-17 18:01 | `scripts/verify-release-oci.py` | Outdated |
| [r4303841567](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4303841567) | 2026-10-17 18:01 | `scripts/qualify-online-recovery.py` | Outdated |
| [r4304011435](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304011435) | 2026-10-17 18:50 | `scripts/qualify-online-recovery-v2.py` | Current |
| [r4304011445](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304011445) | 2026-10-17 18:50 | `scripts/verify-release-oci.py` | Current |
| [r4304011453](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304011453) | 2026-10-17 18:50 | `scripts/verify-release-oci.py` | Outdated |
| [r4304111685](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111685) | 2026-10-17 19:15 | `qualification/online-recovery-v2.json` | Current |
| [r4304111688](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111688) | 2026-10-17 19:15 | `scripts/qualify-online-recovery-v2.py` | Current |
| [r4304111692](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111692) | 2026-10-17 19:15 | `scripts/check-release-scanner.py` | Current |
| [r4304111694](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111694) | 2026-10-17 19:15 | `scripts/verify-release-oci.py` | Outdated |
| [r4304111700](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111700) | 2026-10-17 19:15 | `scripts/verify-release-oci.py` | Outdated |
| [r4304111708](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111708) | 2026-10-17 19:15 | `scripts/qualify-online-recovery.py` | Outdated |
| [r4304111713](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111713) | 2026-10-17 19:15 | `scripts/verify-release-oci.py` | Outdated |
| [r4304111718](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304111718) | 2026-10-17 19:15 | `scripts/qualify-release-smoke.py` | Current |
| [r4304394181](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394181) | 2026-10-17 20:35 | `scripts/verify_source_closure.py` | Outdated |
| [r4304394188](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394188) | 2026-10-17 20:35 | `scripts/verify-release-oci.py` | Outdated |
| [r4304394194](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394194) | 2026-10-17 20:35 | `scripts/http_evidence_verdict.py` | Outdated |
| [r4304394201](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394201) | 2026-10-17 20:35 | `scripts/verify_assurance_contract.py` | Current |
| [r4304394202](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394202) | 2026-10-17 20:35 | `.github/workflows/online-recovery-v2.yml` | Current |
| [r4304394206](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394206) | 2026-10-17 20:35 | `scripts/check-release-scanner.py` | Current |
| [r4304394209](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394209) | 2026-10-17 20:35 | `scripts/sanitize_qualification_evidence.py` | Outdated |
| [r4304394212](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394212) | 2026-10-17 20:35 | `deploy/release/install-pinned-apt.sh` | Current |
| [r4304394221](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4304394221) | 2026-10-17 20:35 | `scripts/verify-release-oci.py` | Current |

## Closure rules

1. Before any push, run compile/fast unit suites, counterexamples for repaired endpoints and SLSA/OCI generated fixtures.
2. Test the real pinned scanner and the genuine ONLINE-3B/4A/4B + RELEASE-1 integrations; a skipped producer or missing download artifact is not green.
3. Require all eleven exact-head workflow conclusions success, including the seven previously green workflow groups.
4. Preserve independently verified source byte closure, chess legality, actor attribution, HTTP statuses, SQLite replay, OCI archive identity and all safe-output boundaries.
5. Request fresh exact-head Codex code and security review. Reconcile every actionable new finding with source/test/CI evidence.
6. Leave all source availability, distribution, licensing, operator, host, live transport, and publication gates closed.

## October 18 Codex repair wave (source-implemented, exact-head CI pending)

The five fresh [Codex findings](https://github.com/HMarcusWH/AllfatherChess/pull/71)
from review of `16faccaba5` remain actionable until their source, negative
counterexamples, **real pinned scanner**, packaged OCI integration and new
exact-head review are reconciled. No finding is declared closed by this
ledger alone.

| Finding | Source repair | Required negative oracle |
| --- | --- | --- |
| [r4305073147](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4305073147) SPDX 2.2/2.3 | Version-specific package fields, strict 2.2 analyzed package code, valid 2.3 optional semantics | Rehashed 2.2 missing fields reject; 2.3 valid optional cases accept |
| [r4305073150](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4305073150) SLSA probe | Require exactly one v1 provenance statement with pinned builder | Missing, duplicate, wrong-version, wrong-builder all reject |
| [r4305073152](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4305073152) License expressions | Offline SPDX 2.x parser and pinned 740-license/86-exception catalogue v3.29.0 | Unknown IDs, malformed operators and injected free text reject |
| [r4305073153](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4305073153) Verifier source | Compare stage/Git/executed bytes and import from private verified snapshot | Modified, rehashed, linked and post-snapshot mutations reject |
| [r4305073154](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4305073154) SPDX file checksums | Valid SHA-1 mandatory, others permitted if well-formed | SHA256-only, malformed and duplicate SHA-1 reject |

**Stop rule:** all eleven exact-head workflow groups complete successfully,
including real RELEASE-1 OCI/smoke, guarded recovery, LC0 and LOCAL-1;
then a fresh exact-head Codex code/security review with no outstanding
unaddressed findings. Offline test authority never becomes online authority.

## October 18 — pinned SHA1 scanner follow-up (candidate-only)

Real RELEASE-1 run [39087354015](https://github.com/HMarcusWH/AllfatherChess/actions/runs/39087354015) passed all 181 fast contracts and failed only at pinned scanner real SPDX files: `SPDX file requires a valid SHA1 checksum`. Source analysis of upstream v1.11.0 (commit `d88056b4e5b61d0ca037340df91be47d343b4386`) traced the default file hasher to SHA256 alone.

Implementation now sources a single SHA1+SHA256 patch in `deploy/release/scanner/target.go.patch`, compiles it twice using vendored dependencies, and routes it through an isolated job-local OCI registry. A candidate scanner digest is **not an approved image**. Full qualification remains blocked until both `approved_binary_sha256` and `approved_manifest_digest` are independently reviewed, frozen and rerun at one SHA. Only after real scanner/OCI positives and new Codex review may the PR be considered for merge; all public distribution/live-play grants remain false.

## Scanner reproducibility positive, approval lock frozen

Source-bound [RELEASE-1 run 39130571069](https://github.com/HMarcusWH/AllfatherChess/actions/runs/39130571069):
Go 1.25.8 produced two identical binary hashes,
`a1043ee5fa8285740548b600fd24d106b4e68211327238b4edbbe70765bd67ca`;
two BuildKit scratch exports produced the identical OCI manifest
`sha256:fe9efbc9d42e6ddbbe44f0f35e2daa45299123891076c1700414a74978a51d2a`.
The pinned Syft v1.11.0 patch then passed strict SPDX (93 packages, 2265 files)
and SLSA v1 builder checks. The run correctly refused final compatibility at
the independent **unapproved digest** gate, which now has these reviewed
identities frozen in `qualification/release-scanner-v1.json`.

The following exact-head run must independently remeasure those hashes and
check actual base-image `/usr/bin/dpkg` contents against scanner SHA1/SHA256
before any full RELEASE-1 image qualification. Code review, distribution and
host/live authorizations remain separate, closed gates.

## October 18 — independent manual review follow-up after green 11/11

Independent exact-head [manual review](https://github.com/HMarcusWH/AllfatherChess/pull/71#pullrequestreview-5564010246)
identified five source-admission and evidence-integrity counterexamples on
`18b5e1f2fd65`: full-image SLSA builder/version not pinned; OCI verifier
executed before committed-source check; ONLINE-4B phase log re-use and
path escapes; ONLINE-4A replay route/manifest symlink escapes; ONLINE-4A/4B
mutable imports executing before committed-byte verification.

The source repair wave:
- Full OCI verifies exactly one SLSA **v1** statement with the same pinned
  builder ID as the actual release policy (not merely the scanner probe).
- Rehashed image fixtures with v0.2, wrong builder, or duplicate provenance
  fail even when all descriptor hashes match.
- Independent RELEASE-1 smoke, OCI diagnostics, OCI assurance and aggregate
  execute in a fresh detached `git worktree` at the exact source commit,
  created after mutable build steps.
- ONLINE-4A/4B separate independent verifier jobs construct their own
  fresh detached exact-commit worktree and verify full attested source closure
  **before any local recovery Python imports**; ONLINE-4B fetches independently
  pinned bridge source inside that worktree.
- ONLINE-4B binds sanitized bridge logs to exact phase index and
  `bridge-phase-N.log` with file containment, symlink refusal and byte digest.
- ONLINE-4A replay manifest and route files refuse symlink escapes,
  nonregular files and lexical traversal.

All five fixes have regression or source-runner contract tests. No item is
declared qualified until the **new** exact-head real CI matrix and independent
review pass. Prior green runs and historical comment resolution do not satisfy
the new head's review gate.
