# AllfatherChess — Post-Merge Codex Debt Ledger (draft PR #71)

Scope: 18 code-review findings from #66–#69, plus two independently discovered admission holes.
Base main commit: 449ff7eb1d4728b6d2a6ec8d6e84a85d3bf4b685.
Status: code implementation in progress; not evidence of green CI or completed reviewer approval.

## Review comments and remedy

| PR | Original Codex review | Root problem | Repair |
|---|---|---|---|
| #66 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/66#discussion_r4242842043) | Untracked and ignored bridge code | Canonical tracked-only export for all bridge consumers; poisoning tests |
| #67 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/67#discussion_r4243620547) | State fault labels without semantic effect | R05 delay and R07 predecessor witness; negative trace mutations |
| #67 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/67#discussion_r4243620550) | Missing executed bridge overlay in attestation | Executed source inventory and HEAD-bound extra_game_handlers export |
| #67 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/67#discussion_r4243620555) | No independent diagnostic after fault job failure | Always-run downstream verdict; missing evidence fails |
| #67 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/67#discussion_r4243620556) | Incomplete postfault observation window | Bounded deadline extension and server-side monotonic window witness |
| #67 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/67#discussion_r4243620558) | Fenced chunked stream remains open | Normal termination chunk/EOF; R08 malformed chunk remains deliberate |
| #68 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/68#discussion_r4243997490) | Forbidden API requests overlooked | Full POST allowlist and server-forbidden audit, including OCI smoke |
| #68 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/68#discussion_r4243997492) | Overlay identities replaced with counts | Independently rebuild expected patched manifest from frozen upstream |
| #68 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/68#discussion_r4243997494) | Duplicate HTTP submissions not rejected | Bijection between trace, request audit, durable intents and board ply |
| #68 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/68#discussion_r4243997497) | Illegal chess moves accepted as syntactically valid | Reconstruct board in trace order and reject turn/legality violations |
| #68 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/68#discussion_r4243997499) | Intent history not bound to server prefix | Exact game ID/ply/prior-history verification |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576126) | Bridge commit is self-attested | Bundle and verify independent pinned bridge lock and source manifest |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576132) | Release OCI workflow misses input changes | All-PR and main-push RELEASE-1 with input-aware ONLINE-4A/4B triggers |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576137) | Example misses required replay bind | Executable shell syntax and required mount contract tests |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576147) | Smoke finally return can swallow exceptions | Return after finally and propagate unexpected teardown failures |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576153) | SBOM/provenance not bound to actual image | Validate descriptor reference and each in-toto subject against exact image |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576159) | Release policy leaves two flags unchecked | Explicit fail-closed host/publication prohibition mutations |
| #69 | [Codex comment](https://github.com/HMarcusWH/AllfatherChess/pull/69#discussion_r4252576166) | OCI compressed layers not bound to diff_ids | Streaming decompression and config.rootfs.diff_ids match with attack fixture |

## Two additional admission failures

- ONLINE-4A runner could exit with code zero after recorded harness errors, while the verifier accepted a complete-looking run. The runner and qualifier now deny that condition without relabeling a legitimately unsafe experiment as a broken harness.
- RELEASE-1 packaged smoke and aggregate originally had incomplete prohibited-POST checking. Both now check the server forbidden-attempt record, legal endpoint scope, 429 cooldown evidence and restart confirmation instead of merely trusting a positive producer status.

## Merge and release conditions

Keep draft until all exact-head fast/real-process checks pass and final Codex code/security reviews finish. Preserve failing and incomplete evidence. Do not reduce any chess authority or runtime-budget requirement. Remain offline-only: no license approval, OS/wheel dependency promotion, public image upload, destination-host authorization, real credentials, BOT account conversion, live transport, or playing-strength claim.
On squash merge, verify new main SHA through the explicit RELEASE-1 main-push workflow.

## PR #71 Codex review and final qualification follow-up

- [P1: image-level in-toto subject](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253078059): now accepted only when every subject digest equals the selected application-image manifest, not its config or individual layer. Added config-only/layer-only/empty-subject destructive tests.
- [P2: BuildKit metadata digest](https://github.com/HMarcusWH/AllfatherChess/pull/71#pullrequestreview-5496871107): only the verified OCI index or selected application manifest can match `containerimage.digest`; an otherwise-valid attestation manifest cannot substitute for the image.
- [P2: packaged illegal HTTP move attempt](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253078070): the fake server now correlates every move request ID with its HTTP status. The image retains its ordered trace and a separate host verifier reconstructs board legality, no-blind-retry, durable intent, R02 and R10 witnesses before aggregate admission.

CI discovered that the untagged OCI archive export lacked BuildKit default image subjects even though the standalone Docker image had an image name. The OCI export now specifies an explicit **local** image tag and records sanitized attestation structure before strict verification. Missing subject evidence is NEVER accepted as a workaround.

The RELEASE-1 host aggregate now also recomputes the actual OCI archive SHA-256 and requires all independent OCI-verifier booleans. Smoke evidence schema 2 replaces incomplete schema 1 for qualification; no producer readiness bit alone can authorize a verdict.

**Status:** this entry records implemented source changes, not successful exact-head OCI integration or an approved code/security review. Fresh final-head tests/reviews and disposition of any new feedback are mandatory before merging. Offline-only/publication-prohibited policy remains unchanged.

## Final PR #71 follow-up: six fresh P1/P2 findings

**Implemented source and destructive fixtures; actual exact-head CI and Codex review still required.**

| Finding | Source repair and test evidence |
|---|---|
| [P1 nested OCI root](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714605) | Select only the BuildKit-reported root descriptor from the OCI layout; compare its digest, media type and byte size to BuildKit metadata. Nested and flat layouts, wrong-root and unrelated-attestation fixtures. |
| [P2 OCI descriptor size](https://github.com/HMarcusWH/AllfatherChess/pull/71#pullrequestreview-5497647677) | Validate every embedded index, manifest, config, image/attestation layer descriptor with exact actual bytes; reject boolean, absent, negative or wrong sizes. Outer index.json is the layout entrypoint, not an embedded descriptor. |
| [P2 nested diagnostics](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714615) | Bounded recursive diagnostic inspector exposes nested indexes, manifest identities, sizes and summarized in-toto subjects; never grants integrity_verified. |
| [P2 SLSA predicate](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714599) | Version-specific v1/v0.2 nested structure, buildType URI, parameters, and builder.id URI; workflow requests a meaningful builder ID; synthetic empty-schema and malformed-ID attacks fail. |
| [P2 persisted journal](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714591) | Independent host-side read-only SQLite transaction, integrity_check, owner and all four tables exact snapshot match. Real MoveJournal-schema fixtures verify WAL, corruption, missing file, symlinks and forged snapshots. |
| [P2 R02 response](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253714587) | Trace response and close events carry per-request IDs with HTTP/1.1 reset. Target lost-ACK case rejects completed response, requires correct close, first-epoch unknown-ACK and no same-intent successful ACK. |

The prior illegal POST and image-manifest subject protections remain enforced. These changes are local qualification and provenance controls, not move-search or HYBRID authorization changes.

**Merge hold:** Do not merge PR #71 until final-head fast contracts, RELEASE-1 real OCI and packaged games, all ONLINE/LOCAL/LC0 checks, and fresh code/security review are green; no public deployment or licensing grant.

## PR #71 final follow-up — four new P2 findings and external build blocker

The Codex review of `d75f10e976decd80c4c0f0c8e74296fbb65751cc` identified four actionable P2 evidence gaps. The following remedies and destructive fixtures were staged in the existing draft branch; **do not mark these findings closed until final-head tests, actual packaged-OCI evidence and review succeed**.

| Codex finding | Source repair | Negative proof fixture |
|---|---|---|
| [Application platform](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102073) | `scripts/verify-release-oci.py` requires a runnable nested-index descriptor to advertise `linux/amd64` consistent with its config. An optional explicit flat-manifest platform must also match. | Wrong OS, wrong architecture, absent nested platform, malformed platform; legitimate flat omission remains supported. |
| [Attestation subject size](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102056) | A present OCI attestation manifest `subject` must equal the independently verified application-manifest descriptor's digest, media type and integer byte size. | Missing, boolean, floating, negative, incorrect sizes and wrong media type, with rehashed parent blobs; valid optional subject accepted. |
| [R02 unknown ACK endpoint](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102049) | First-epoch unique `http_unknown_ack` must explicitly have `endpoint="move"` and match the game/owner. | Missing/wrong endpoint and duplicate conflicting unknown ACK. |
| [R10 client-observed HTTP 429](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4263102061) | Require the targeted completed 429 response (request/path/status/order), exactly one first-epoch `global_rate_limit_block` at endpoint `move`, and the persisted `UNKNOWN_ACK` intent with `http429:move` cooldown. | Missing, unrelated, duplicate, premature or wrong-status response; absent/misowned/wrong-endpoint guard and wrong durable intent. |

An older [illegal-POST review](https://github.com/HMarcusWH/AllfatherChess/pull/71#discussion_r4253078070) receives a direct producer-side negative test alongside existing host-side chess legality checks: an otherwise completed game with a 400 illegal-move response cannot set `ready` to true.

**RELEASE-1 previous exact-head failure:** Docker's pinned Ubuntu snapshot `20261001T000000Z` returned HTTP 503 while downloading native packages, before OCI export or packaged smoke testing. The Dockerfile now uses bounded APT acquisition retries/timeouts against the **same GPG-verified snapshot**. The workflow retains a small, failure-only structured report and exits nonzero if the build remains unavailable. No fallback mirror, floating repository, unsigned package or publication permission was added.

The proof obligation remains eleven required exact-head workflows green, genuine standalone OCI SBOM/provenance verified, all four installed-image scenarios qualified against real SQLite, final Codex code/security review with no actionable findings, and explicit disposition of the remaining prior review threads. No online play or public distribution grant is part of this PR.
