# ONLINE-PLAY-1 — restricted Lichess canary (PR-A only)

**Status: candidate code; no permission to connect to Lichess, convert an account,
publish a build, merge or deploy.** PR-B is a separate human approval boundary.
The source candidate deliberately has no account names and all permissions false.

## Purpose and scope

The experiment is to run the existing source-sealed Stockfish-anchor +
Stockfish-shadow + Reckless + BLAS-LC0 Allfather UCI client using the pinned
`lichess-bot` client through a private gate. The experiment does not qualify
playing strength, GPU performance, an Elo gain, or general agent authority.
The existing RELEASE-1 image entrypoint remains `verify`/`offline-smoke`,
its runtime contract remains no egress/no real token, and the controller search
implementation, models, LC0 b4 and outward move authority are unchanged.

The canary only considers an **incoming** unrated standard start-position rapid
600+5 challenge from one explicitly named BOT to an explicitly named owner BOT.
No outbound challenge, matchmaking, chat, cloud analysis, opening books,
tablebases, draw/takeback, abort, resignation or BOT account upgrades can be
requested. An adversarial challenge or `gameFull` mismatch is a denial, not
an opportunity for automatic policy relaxation.

## Actual trust boundaries

- **Bridge**: synthetic `offline-fixture-token` only, no secret volume,
  Docker internal-only network and fixed gateway `http://live-gateway:8765/`.
  The guarded transport default stays offline-only; the opt-in `live-canary`
  profile checks a runtime Ed25519 admission and never targets public HTTPS.
- **Gateway**: only compartment with the actual bot token *if separately
  authorized in PR-B*. Token is read from a nonlinked regular 0600 file owned
  by the gateway UID. The production origin is the literal
  `https://lichess.org`; redirects/proxies/verify=False and URL overrides
  are forbidden. It re-verifies token scopes/account BOT identity and
  replaces the bridge's synthetic test-token response with a synthetic-keyed
  view of a genuinely verified real token. No full credential-keyed JSON
  leaves the gateway.
- **Protocol**: exact method/path/body/headers before any real request.
  The pinned bridge's move `?offeringDraw=false` is accepted, but `true`
  and all other query keys are refused. Both account and game stream GETs
  undergo gateway authorization. One account stream and one admitted game
  stream can coexist; other requests use a separate mutex. Blank NDJSON
  lines are heartbeats, not EOF. A 429 establishes a persisted account-wide
  cooldown of at least 60 seconds.
- **Lifecycle**: `SessionOwner` + `MoveJournal` persist UCI intent and owner
  epochs. Gateway has its own persisted accept/game/move-once ledger. A
  single HTTP POST is a spent attempt even if the response is lost; duplicate
  same-game/same-ply attempt is denied. The supervisor is PID1 and reaps
  child processes after a bounded shutdown, but never auto-aborts/resigns.
  A terminal result needs both independently observed gateway history and
  SQLite/PGN reconciliation.
- **Admission**: source policy fields are fixed and negative in PR-A. A
  separate, operator-signed runtime-only admission must bind source HEAD,
  source tree, private image digest, policy hash, host observation hash,
  two BOT identities, license review digest and a short UTC lease. Its
  public trust anchor and real approval procedure are **not** created by
  PR-A or a GitHub workflow. CI signatures are ephemeral synthetic test
  fixtures, incapable of authorizing an actual Bot API account.

## Synthetic developer tests

This is an implementation/verification target, not a positive attestation.

```bash
# From exact, clean source, with Python, python-chess, PyYAML and requests:
python3 scripts/check-live-canary-boundary.py
python3 -m unittest tests.lichess.test_live_gateway \
  tests.lichess.test_live_bridge_policy \
  tests.lichess.test_live_canary_supervisor \
  tests.lichess.test_live_canary_redaction -v

# Real three-engine UCI build and the pinned upstream:
make build-online-engine-opt-v2
python3 scripts/qualify-engine-opt-v2.py
python3 scripts/release-manifest.py create
python3 scripts/release-manifest.py verify
python3 scripts/package-online.py
python3 deploy/lichess/fetch_bridge.py
mkdir -p build/online3b-wheels
python3 scripts/prepare-online3b-wheels.py \
  --source-lock deploy/lichess/requirements.release.lock \
  --wheelhouse build/online3b-wheels

# Build PRIVATE synthetic images from the existing offline fixture.
# Absolutely no push, real token or direct live API.
docker build -f deploy/lichess/Dockerfile.offline -t allfather-online4b-offline:pr-a-test .
docker build -f deploy/live_canary/Dockerfile.test \
  --build-arg BASE_IMAGE=allfather-online4b-offline:pr-a-test \
  -t allfather-canary-test:private .
SHA="$(git rev-parse HEAD)"
python3 scripts/run-live-canary-dry-run.py --color white \
  --image allfather-canary-test:private --source-sha "$SHA" \
  --output build/test-results/online-play-1/white
python3 scripts/run-live-canary-dry-run.py --color black \
  --image allfather-canary-test:private --source-sha "$SHA" \
  --output build/test-results/online-play-1/black
python3 scripts/verify-online-live-canary.py --mode offline-dry-run \
  --evidence build/test-results/online-play-1 \
  --source-sha "$SHA" \
  --output build/test-results/online-play-1/independent-verdict.json
```

The test gateway is **not** the live gateway executable: it injects a
source-controlled local `requests.Session` test adapter that rewrites only
the literal `https://lichess.org/api/` request to a real local fake HTTP
server. The production entrypoint cannot select that adapter. The fake
server accepts only a newly generated synthetic test token mounted on the
gateway (never on the bridge); the token and ephemeral signing key are
deleted before retaining test artifacts. Tests should inspect two distinct
Docker namespaces and a real failed external TCP attempt from the bridge.
The real sealed UCI and pinned upstream bot must finish one legal game
each color. If a host cannot support Docker/cgroups/4 CPU/6144MiB or
the frozen toolchain, report **NOT_RUN/INCONCLUSIVE**, not PASS.

## Interpreting the evidence

The independent verdict recomputes rules legality and PGN, compares the
server's accepted UCI moves with the durable engine journal, validates
source SHA and Docker separation reports, counts exact allowed API
operations and one-shot move attempts, checks one-game durable lockout,
and requires zero living bridge descendants.

`DRY_RUN_PASS` is allowed only if both colors and all required negative
controls really passed. It is **not** `LIVE_CANARY_PASS`. A partial game,
skipped job, missing PGN, user-ID mismatch, 429, UNKNOWN_ACK or unverified
host must remain unqualified. Do not claim result by writing a JSON PASS flag.

The **host operator example Compose** uses profiles that are inert by default.
Its `verify-only` command is deliberately not a play command. It is
not usable to start a real game until an independently reviewed PR-B
attaches a named approved host/account, trust anchor, token file, legal
review, kill-switch owner and operation-specific one-shot approval.

## PR-B boundary

After PR-A's exact-head verified dry-run, separately:
1. choose and measure actual Linux x86_64 destination host (>=4 effective
   CPUs, >=6144MiB, process isolation, disk, Docker bridge no-egress);
2. obtain independent GPL/AGPL legal obligations and source-offer disposition;
3. separately authorize BOT account conversion if needed; never from code;
4. bind one approved BOT opponent and one incoming casual standard 10+5
   challenge with a gateway-only `bot:play` token read-only file;
5. pin a real, external operator public key/trust anchor, review current
   source/image/host and sign a short-lived one-use admission;
6. perform a separate real-host approval and one-game go/no-go.
No public OCI publication or general release flag is permitted.

**No action in this PR performs any of these PR-B operations.**

### Completed PR-A boundary hardening
A live move requires a read-only independent copy of the actual bridge SQLite
journal at `/bridge-state/recovery/move-journal.sqlite3`. The gateway compares
the owner epoch, `SUBMITTING` intent, canonical history, game ID, ply, legal
UCI and approved side before spending a POST. Synthetic bearer headers alone
never confer move permission.

A dedicated shared stop-control volume (`/run/canary-control`) is not a
secret store. The supervisor creates a durable `stop.flag` during terminal
cleanup or emergency shutdown; the gateway denies new HTTP actions when it
appears. Operator intervention can also shut down both containers directly.
The flag must survive process restart and is not cleared automatically.

The bridge's staged upstream source belongs on ephemeral `/tmp` storage.
The owner epoch and full-move journal remain on a distinct persistent volume.
Do not delete the durable journal to resolve an UNKNOWN_ACK.

### Source-bound production operator trust (PR-B only)

The PR-A production `entrypoint.py` deliberately refuses both `gateway`
and `bridge` modes even when a runtime-only lease is valid, unless a
separately reviewed **PR-B** commit provides
`qualification/online-canary-operator-trust-v1.json`. This future file
will pin the approved operator **public key digest**, exact Git commit/tree,
private image digest, named host and verified legal-review digest. It is
absent in PR-A. A self-generated key plus self-signed lease is not a
source-independent approval.

The synthetic integration harness calls test-only fake gateway/bridge scripts
under isolated Docker namespaces, not the production credential entrypoint;
it does not require or create this PR-B trust registry.

### Diagnostic failures and escalation

A failed PR-A integration has a separate `online-play-1-incomplete-diagnostics`
artifact retaining only synthetic `failure.json`, `incomplete.json` and
`cleanup-incomplete.json` records. The `stage_code` distinguishes pinned
upstream/engine source verification, effective bridge configuration validation,
recovery patching and live guard sealing; the local exception's text remains
unpublished. No failure artifact can satisfy `DRY_RUN_PASS`.

Inspect the `network-integration` job first, then fix the specifically
reproduced code path and repeat both real sealed UCI fake games. The independent
verdict job deliberately yields `NOT_RUN` when the producer fails, instead
of treating a missing artifact download as evidence. Retain full private
runtime logs only in bounded offline debugging environments; do not upload
unrestricted stdout, bearer, live config or key material.
