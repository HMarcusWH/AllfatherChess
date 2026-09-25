# ONLINE-2: real-network hardware-bound online CPU reference

ONLINE-2 composes the already-merged ONLINE-1 clock/lifecycle contract with the
already-qualified LC0 real-inference BLAS/network identity. It remains deliberately
**Stockfish-anchor authoritative**. M14-G3 is the later milestone that may allow
qualified staged specialist evidence to replace the anchor move.

## Frozen scope

The reference profile is:

- Linux x86-64, CPU only;
- Stockfish built for explicit portable `ARCH=x86-64`;
- Reckless built for explicit `x86_64-unknown-linux-gnu` / `target-cpu=x86-64`
  using `CARGO_ENCODED_RUSTFLAGS`, overriding the repository's local
  `target-cpu=native` development default;
- LC0 built with the existing pinned BLAS real-inference profile and exact frozen
  791556 network bytes;
- LC0 `ScoreType=WDL_mu`;
- BLAS worker limits explicitly passed to the LC0 process as
  `OPENBLAS_NUM_THREADS=1`, `GOTO_NUM_THREADS=1`, and `OMP_NUM_THREADS=1`;
- one isolated generated artifact root under `build/online-cpu-reference/`;
- no binary fallback glob or network autodiscovery;
- ONLINE-1 `clock_envelope_v1` unchanged;
- conservative routing with no calibration and therefore no learned suppression;
- no VERIFY extension, REFINE, cross-feed decision path, M14-C authority or staged
  authority composition.

The environment map is part of runtime engine identity and replay provenance. Only
declared overrides are recorded; credentials and unrelated inherited process
environment are not copied into replay artifacts.

## Isolated artifact bundle

`scripts/build-online-cpu-reference.sh` produces:

```text
build/online-cpu-reference/
├── bin/
│   ├── stockfish
│   ├── reckless
│   └── lc0
├── networks/
│   ├── nn-134a887f4c8f.nnue
│   ├── v60-7f587dfb.nnue
│   └── 791556.pb.gz
└── build-manifest.json
```

The manifest binds the source commit, vendor commits/trees, qualification/config
hashes, explicit build targets, LC0 process environment, every binary hash/size, and
every network hash/size. Runtime configuration points only to this bundle.

## Qualification

Reference CI runs:

```bash
make online-profile-tests
make build-online-cpu-reference
python3 scripts/lc0-strength-profile-contract.py
make online-profile-contract
```

The dedicated ONLINE-2 workflow also records the reference host/toolchain. A later
deployment host can run the same qualifier with `--mode deployment`; that creates a
new host-bound report rather than pretending the GitHub-hosted machine is the final
service host.

The end-to-end qualifier requires:

1. frozen lock/profile/config/build-manifest compatibility;
2. actual bundle bytes matching every manifest digest;
3. independent LC0 real-inference report proving the *same LC0 binary hash* used by
   the online bundle observed the BLAS backend with the frozen network;
4. runtime engine identities matching the isolated bundle;
5. three real-controller clock cases returning exactly one legal anchor-authoritative
   move inside the hard deadline;
6. replay integrity, WDL_mu LC0 telemetry, and replay-bound BLAS thread environment;
7. a complete physical CPU envelope certificate for every positive case;
8. explicit zero-clock failure as a negative control.

## Claim boundary

A passing ONLINE-2 report supports only:

> The exact declared source, portable CPU-target binaries, frozen engine networks,
> real LC0 BLAS inference, clock policy, process environment, and measured online CPU
> envelope operated together on the recorded host.

It does **not** establish hybrid move authority, learned SKIP value, Elo, optimal time
management, a final equal-resource platform, or playing-strength superiority.

The next behavioral release milestone is M14-G3.
