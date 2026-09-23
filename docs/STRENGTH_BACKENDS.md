# LC0 strength-facing backend qualification

## Status

PR #24 introduces the first **real-network, real-backend LC0 qualification
profile**. It does not establish an Elo gain or make the current reference
machine a final equal-resource match platform.

The repository now distinguishes three layers:

```text
backend-light controller validation
    random LC0 backend
    no network
    fast/deterministic
    NOT strength evidence

real-inference qualification
    pinned LC0 source
    pinned network bytes
    non-random backend
    explicit ScoreType/options
    observed hardware/software report
    eligible as real LC0 evidence

strength campaign
    paired games / fixed physical resource envelope
    measured CPU/GPU accounting
    statistical promotion gates
    NOT established by PR #24
```

## Frozen source

LC0 source ancestry remains governed by `vendor.lock.json`.

The qualification lock repeats the LC0 commit/tree identity so a strength-facing
profile cannot silently drift away from the vendored source:

```text
qualification/lc0-strength.lock.json
```

The lock is intentionally separate from `vendor.lock.json`: the latter
requires GitHub-hosted artifacts, while LCZero training networks are distributed
through the LCZero training service.

## Network identity

The initial network candidate is the exact network already pinned by the
vendored LC0 `appveyor.yml` for its non-OpenCL/non-DX release builds:

```text
training id: 791556
sha256: f404e156ceb2882470fd8c032b8754af0fa0b71168328912eaef14671a256e34
```

The qualification workflow downloads the file, verifies SHA-256, and records
its observed byte size. The lock is not considered frozen until that byte size
is committed as a positive integer.

No strength-facing run may use:

- an empty `WeightsFile`;
- `<autodiscover>`;
- a different network at the same path;
- an unverified network cache.

## Reference backend

The first portable reference uses LC0's real BLAS backend on Ubuntu x86-64.

The build explicitly disables alternative accelerator backends and pins:

- `Backend = blas`;
- `BackendOptions = ""`;
- `ScoreType = WDL_mu`;
- `NNCacheSize = 0`;
- `MinibatchSize = 32`;
- `MaxConcurrentSearchers = 1`;
- `TaskWorkers = 0`;
- `Threads = 1`;
- `MultiPV = 3`.

The reference profile records the concrete CPU model, logical CPU count,
memory, OpenBLAS package version, kernel/platform string and commit SHA at
runtime.

Because GitHub-hosted CPU models are not a fixed competitive machine class,
`strength_campaign_eligible` remains false. The report qualifies the **reality
and provenance of inference**, not final match comparability.

## ScoreType firewall

Before PR #24, shipped shadow profiles declared:

```text
shadow.lc0_score_type = centipawn
```

but relied on LC0's implicit UCI `ScoreType` default.

That was a semantic hole: the adapter label was explicit while the engine option
was not.

PR #24 requires:

```text
LC0 UCI option ScoreType
==
shadow.lc0_score_type
```

for every shadow/active runtime.

All backend-light profiles now set `ScoreType = centipawn` explicitly.
The strength-facing reference sets both sides to `WDL_mu`.

A mismatch is a configuration error before any search is dispatched.

## Replay provenance

`BackendManager.engine_identity` already binds:

- engine family/role;
- executable path;
- executable SHA-256;
- process arguments;
- UCI options.

PR #24 extends LC0 identity so an explicit `WeightsFile` also records:

- resolved path;
- byte size;
- SHA-256.

That identity is captured before process launch.

Consequently a replay cannot honestly compare two runs as the same upstream
condition when their LC0 network bytes differ, even if the path string is the
same.

## Backend observation

Configuration alone is not accepted as proof that the requested backend ran.

The real-inference contract requires LC0 stderr to contain:

```text
Creating backend [blas]
```

after the qualified options are applied and a real search is executed.

The qualification report therefore binds both:

```text
requested_backend
observed_backend
```

and requires them to agree.

## Warmup

The profile requires one fixed 64-node warmup search before the evidence search.

This avoids treating backend/network initialization as part of the qualified
search path while keeping warmup behavior explicit and reproducible.

The qualification search then runs a separate real 256-node LC0 search.

## Telemetry semantics

The real search is passed through the production `Lc0TelemetryAdapter`.

The contract requires at least one real candidate evaluation to carry:

```text
lc0.uci_score.WDL_mu
```

for the reference profile.

This verifies the complete chain:

```text
LC0 ScoreType option
  -> real UCI output
  -> production adapter
  -> declared telemetry semantics
```

without converting LC0 output onto a Stockfish/Reckless scale.

## Standard CI remains backend-light

`scripts/build-baselines.sh` is deliberately unchanged in purpose:

```text
-Dbuild_backends=false
Backend=random
```

Normal controller/baseline CI must stay fast and deterministic.

The separate workflow:

```text
.github/workflows/lc0-strength-qualification.yml
```

owns real-network qualification.

That workflow:

1. verifies vendored source ancestry;
2. validates qualification schemas;
3. installs OpenBLAS and LC0 build dependencies;
4. records the host environment;
5. fetches and hashes the pinned network;
6. requires the network lock to be frozen;
7. builds the BLAS backend;
8. runs the real-inference contract;
9. uploads the qualification report.

## Claim boundary

A passing PR #24 report supports:

> This LC0 evidence used the declared vendored source, exact network bytes,
> non-random BLAS inference backend, explicit runtime options and the recorded
> host environment, and produced real search/telemetry output.

It does **not** support:

- LC0 is stronger than Stockfish;
- AllfatherChess is stronger than any constituent;
- BLAS is an optimal LC0 backend;
- the GitHub-hosted runner is a fair competitive platform;
- the profile's resource cost is comparable to Stockfish/Reckless;
- a hybrid proposal is better than the Stockfish anchor;
- any Elo claim.

Those require measured resource accounting and the later strength campaign.
