# PR #71 — Assurance Repair and Review Closure Ledger

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
