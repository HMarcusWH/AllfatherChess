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
