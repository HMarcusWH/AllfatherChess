# ONLINE-PLAY-1 PR-B — independent operator, host and legal readiness

**State on 25 October 2026:** proposed separate approval package; NOT
implemented, approved, deployed or a public BOT release. PR-A source and two-color
fake-API qualification merged in [PR #76](https://github.com/HMarcusWH/AllfatherChess/pull/76).
No operator trust, owner/opponent Bot API names, real token, legal conclusion or
named destination host is source-authorized in the current checkout.

## 1. Trust boundary and exact source

- The live candidate is **separate** from the immutable RELEASE-1 `verify` /
  `offline-smoke` no-egress executable.
- `deploy/live_canary/entrypoint.py` requires the independently approved
  `qualification/online-canary-operator-trust-v1.json` record; this file is
  intentionally **absent** in PR-A.
- Operator-signed admission binds source commit/tree, candidate image digest,
  policy hash, host ID and observation digest, legal-review digest, owner BOT,
  opponent BOT, one-use nonce, `max_games=1` and a bounded short UTC lease.
- An external, separately reviewed Ed25519 public trust anchor must agree with
  the operator-trust Git record; self-signed fixture input is not authority.
- A signed `synthetic-ci-fixture-only` host observation is not sufficient for
  real gateway or bridge execution. Production accepts real
  `host-observation-only` receipts and fail-closes missing/changed facts.

## 2. Required independent decisions before real operation

| Area | Required source-backed result | Deny on |
| --- | --- | --- |
| Legal | Reviewed Stockfish/LC0 GPL, Reckless/lichess-bot AGPL, controller and network-use/combinations; corresponding source and notices | `legal_review_completed=false`, uncertain AGPL rights, absent review digest |
| Host | Named physical host, reproducible release image/digest, verified cgroup 4 effective CPUs and 6144 MiB reference minima, durable independent volumes | Synthetic capacity, changed image/source, missing host or storage witness |
| Network | Bridge isolated in Docker internal-only network; gateway dual-homed; no published ports, distinct namespaces, observed external TCP egress denial for bridge | Bridge can directly reach public API or gateway token |
| Account | Separate BOT owner and explicit BOT opponent; account is confirmed BOT; standard start position, unrated rapid 600+5 | Human/rated/alternative variant/game/clock, other owner or opponent |
| Credential | Real `bot:play` token only as a gateway-owned 0600 secret file; no bridge env/secret file, no proxy env | Token leaks to bridge/config/artifact/log |
| Lease | Independent short Ed25519 operator signature, one-session/non-replayable SQLite claim, matching trust key and exact host/source/image/legal digests | Missing or expired signature, second challenge/game, unknown identity |
| Lifecycle | Gateway immutable HTTPS origin and exact ingress allowlist; durable accepted move journal; one POST per ply; stop flag and PID1 bounded reap | Uncertain ACK retry, illegal move, missing terminal reconciliation, detached orphan |
| Evidence | Reconciled gameFull/state, final PGN and replay for each played move, independent verdict and negative failure report | Any incomplete replay, missing CPU resource claim, no terminal witness |

These are *pre-operation* gates; a green PR-A CI or RELEASE-2B candidate
workflow does **not** satisfy them.

## 3. Frozen first-game scope

Only an explicitly preapproved incoming challenge from one named BOT to the
authorized owner BOT, unrated standard chess from startpos, 600 seconds plus 5
seconds increment, exactly one game. Prohibit outbound challenges, public
matchmaking, opening books, external analysis/move assistance, chat, draw offer,
takeback, abort, resign, upgrade and move retries. Preserve gateway synthetic
bridge token separation and TLS verification; refuse redirect and alternate URL.

The operator owns the decision to approve a real candidate, credentials, a
named host, the stop window and any legal/publication obligations. A source
documentation PR cannot grant any of these. A permission record that is missing
today must remain absent rather than being manufactured from CI fixtures.

## 4. Verification and stop/rollback

1. Inspect actual host/topology/image/secret mounts and reject drift from the
   signed admission (not merely a signed JSON fixture).
2. Verify source HEAD and tree, clean checkout, immutable engine seal and
   pinned `lichess-bot` source; re-run exact-head offline two-color canary and
   all release/publication denial checks.
3. Independently review license/network-service source obligations and
   operator authorization through a separate PR and human approval.
4. Require a durable gateway POST-attempt ledger and read-only bridge
   `SUBMITTING` journal row with matching epoch, player-to-move and legal UCI;
   on uncertain ACK never resubmit automatically.
5. Stop on mismatch, rate limit, unexpected game or missing evidence. Persist
   operator stop marker *before* bounded bridge shutdown. Retain negative
   outcomes for review without deleting them.

## 5. Claim ceiling and next authority

A completed privately authorized game could establish controlled real network
integration only. It is not production rollout, general host portability,
network crash recovery across host power loss, signed public distribution,
equal-compute superiority or Elo. J14 production-profile comparisons and
M15-B/C formal strength remain separate.

Sources: [PR-A runbook](ONLINE_PLAY_1_RUNBOOK.md),
[threat model](ONLINE_PLAY_1_THREAT_MODEL.md),
[release plan](ONLINE_RELEASE_PLAN.md),
`qualification/online-live-canary-v1.json`,
`deploy/live_canary/admission.py`, and
`deploy/lichess/live_gateway_state.py`.
