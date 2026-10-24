# ONLINE-PLAY-1 PR-A — source and qualification handover

**Repository:** `HMarcusWH/AllfatherChess`  
**PR:** [#76](https://github.com/HMarcusWH/AllfatherChess/pull/76)  
**Base main:** `ad1e582637d981199550caf389b43861ce3d2596`  
**Scope:** PRIVATE fake-Lichess dry run only. Not live authorization, deployment,
account conversion, distribution, or approval to merge.

## Exact state and claim cap

PR-A is an **implemented source candidate under adversarial qualification**,
not a verified `CANARY_DRY_RUN_PASS`. Always re-read `git rev-parse HEAD`
and inspect the exact-head workflow before recording a new verdict.

The separate `qualification/online-live-canary-v1.json` retains `null` owner
and challenger identities and all operator/public/live/legal/host approval
flags **false**. No GitHub Actions job has a real Bot API token. Original
RELEASE-1 `entrypoint.py` / `runtime-contract.json`, RELEASE-2B-2
native/wheel/engine inputs, LC0 b4 and controller move authorization have
not been promoted or modified. Original pinned lichess-bot commit is
`df7e730de58cc3ef2f1415a0dc2eeda842d39167`.

## Implemented source boundaries

- **Contract + runtime admission:** strict source policy and schemas; external
  Ed25519 signature verification, exact commit/tree/private-image/policy/host
  and BOT identities, short UTC lease and durable one-session claim.
- **Gateway:** fixed `https://lichess.org` production origin, credential
  only inside gateway, upstream real-token check and BOT profile, synthetic
  keyed token-test response for bridge, exact GET/POST allowlist, no redirects,
  proxies, insecure TLS or alternate move sources.
- **Streaming:** bounded incremental NDJSON with blank heartbeat forwarding,
  one global account event stream and one admitted game stream, verified
  `challenge` and `gameFull`, no non-stream mutex held by a stream.
- **Move authorization:** unchanged sealed UCI + existing durable SQLite
  `MoveJournal`, `UNKNOWN_ACK` and epoch semantics, supplemented with a
  gateway read-only check of the *actual* prepared `SUBMITTING` SQLite row,
  independently legal UCI, player-to-move and one POST per ply.
- **Process control:** supervisor PID1, separate persistent recovery ledger
  and disposable temporary bridge checkout, bounded SIGTERM/SIGKILL reaper,
  one-game container lifetime and a shared durable stop marker.
- **Isolation:** bridge attached only to Docker `internal: true` network;
  gateway dual-homed with its own namespace and the sole synthetic CI token
  file; private host example is inert `verify-only`, no host ports.
- **Evidence:** real pinned bot/four-process UCI fake game scripts (white/black),
  actual fake HTTP and stream witnesses, source/engine/overlay identities,
  journal, PGN, Docker topology/negative TCP evidence and an independently
  authored replay verifier. Synthetic signing key is not mounted into containers.

## Required exact-head checks — NOT waived

The mandatory GitHub workflow is
[`online-live-canary-qualification.yml`](../.github/workflows/online-live-canary-qualification.yml).
Its five principal check groups and final summary must all be successful:
`source-boundary`, `gateway-unit`, `bridge-supervisor-unit`,
`network-integration`, `independent-canary-verdict`, plus `summary`.
A queued/skipped/cancelled or failed job is **not** successful evidence.

On 2026-10-22, prior source-boundary and RELEASE-1 publication-denial
checks were observed passing on earlier heads, but the new canary workflow
was still queued/pending. **No actual PR-A completed full game in both
colors, dual-container network proof, exact-head full OCI regression or
independently verified `DRY_RUN_PASS` was available at this handover.**
Do not infer success from the synthetic harness or its presence in Git.

Mandatory withheld claims:

| Gate | Status until new evidence |
| --- | --- |
| Policy/lease/gateway source implementation | CANDIDATE |
| Complete negative unit and fault matrix | UNVERIFIED |
| Real pinned lichess-bot + sealed UCI full fake game as White | NOT VERIFIED |
| Same full fake game as Black | NOT VERIFIED |
| Physical two-namespace/egress/secret isolation | NOT VERIFIED |
| Durable restarts, process crash and bounded cleanup fault cases | NOT FULLY VERIFIED |
| Independent exact-head verdict | NOT VERIFIED |
| RELEASE-1 offline OCI and all legacy regression checks at this head | NOT FULLY VERIFIED |
| GPL/AGPL legal review / real host / operator admission | BLOCKED, PR-B only |
| Live account conversion, Bot API, deployment and publication | FORBIDDEN |

## Operator-safe reproduction

Run only in a verified clean checkout and inspect actual evidence:

```bash
git status --porcelain --untracked-files=all
git rev-parse HEAD
python3 scripts/check-live-canary-boundary.py
python3 -m unittest discover -s tests/lichess -p 'test_live_*.py' -v
python3 -m unittest discover -s tests/release -p 'test_live_release_boundary.py' -v
# Follow docs/ONLINE_PLAY_1_RUNBOOK.md for the real sealed UCI and
# two-container FAKE-API image/game harness; never supply a real token.
```

Then inspect the GitHub Actions **exact PR head** for failures; repair actual
code and tests and rerun after the new SHA. Do not change historical PASS
records, weaken assertions, or authorize a release to turn the PR green.

## PR-B: separate operator approval boundary

After all PR-A checks and an independent source review have passed, PR-B
may propose **one named Linux x86_64 host**, >=4 effective CPUs/6144MiB,
verified network/PID/storage containment, approved BOT account and
single BOT opponent, external operator public-key trust anchor, checked
GPL/AGPL obligations, gateway-only `bot:play` credential and one-use
signed admission. BOT account upgrade is irreversible and must be performed
by the human operator **only after separate approval**. A first real casual
10+5 game is an additional one-time human decision, never a CI job.

## PR-A repair implementation status — 22 October 2026

The latest source repair branch is a **new candidate**, not a new release claim.
The repair adds typed, redacted upstream staging failures; a single terminal
shutdown with separately retained cleanup failure; recomputed host-observation
SHA-256 over actual contents; one WAL snapshot for independent move-intent
verification; independently observed running Docker image/source-mount identity;
and separate `NOT_COMPLETED` diagnostic artifacts.

The shared Ubuntu hosted CI prerequisite remediation is proposed independently
in draft PR #77. Do not use its successful unit test as proof of native release
dependency reproduction; RELEASE-2B-3 and RELEASE-2B-4 are still separate.

**Claim firewall:** The original two-color exact-head integration was failing.
Until the changed PR-A head obtains new successful games and independent
`DRY_RUN_PASS`, it remains not verified. Do not merge, use actual tokens,
publish, convert BOT accounts or authorize a live game.

---

## PR-A executable staging diagnostic repair (24 October 2026)

The exact-head preflight on `54340f04777c15ac747d161b138df4829911ef28`
failed before complete games with
`OFFLINE_STAGE_COPY_OR_CONFIG / OFFLINE_STAGE_CONFIG_GENERATE` and
`OtherError`. The pinned upstream validator uses generic `Exception`
for config assertions and checks launcher execution permission. The
new candidate keeps the original frozen offline staging and safety checks,
uses a separate private executable staging mount instead of widening
general `/tmp`, and adds allowlisted configuration substage diagnostics
and mount/evidence negatives.

The prior three failed CI checks represent an original failing integration
and deliberate dependent `NOT_RUN` refusals. Neither an executable mount
nor a preflight positive verdict alone authorizes a merge. White/Black
synthetic complete games and a source-independent `DRY_RUN_PASS` remain
mandatory on the **new exact source head**; review the actual artifacts
before attributing the cause or promoting any qualification.

## Stream delivery and fake challenge transition repair (24 October 2026)

Source candidate adds HTTP/1.1 chunked forwarding for the pinned upstream
client's default `requests.Response.iter_lines()`. The previous
`chunk_size=1` test did not exercise the actual buffering boundary. The
new real-socket test holds the upstream account stream open while checking
challenge, one approved accept and gameStart arrival. The failed synthetic
White game retained only a phase; the new transition receipt includes only
bounded booleans/counts, no raw credential/request/event material.

No claim of completed White/Black synthetic games or independent
`DRY_RUN_PASS` transfers from earlier heads. Original offline stage, Lichess
origin, durable journal, gateway authority, and PR-B admission prohibitions
remain unchanged.

## Intermediate two-color completion, verifier still unqualified

The exact-head `f20b95c17d7612007cff97e28091f77606890994` synthetic
integration completed White and Black with exit 0 after the chunked
transport fix. The subsequent host-side Python verifier subprocess exited
1 without producing a qualification verdict. A new candidate uses only
the existing source-verified offline wheelhouse and its generated runtime
wheel hash lock to supply the host verifier interpreter. Neither missing
verifier dependencies nor producer game completion alone can be reinterpreted
as an independent DRY_RUN_PASS. Review exact new-head evidence before merge.
