# Upstream provenance

AllfatherChess is a derived-engine monorepo. The three engine directories were bootstrapped from exact pinned upstream snapshots, but they are live Allfather source trees after import and are expected to accumulate project-specific modifications.

The provenance contract therefore distinguishes **upstream ancestry** from **current monorepo contents**.

## Pinned upstream ancestry

| Engine | Source repository | Source ref | Pinned commit | Pinned tree | Tracked entries |
| --- | --- | --- | --- | --- | ---: |
| Stockfish | `HMarcusWH/Stockfish` | `master` | `17a6c8f1eb0da45c2ca405321919519bf4e211ba` | `b14521db7dc6f5747042d76579a0b171e0a89f3f` | 119 |
| Reckless | `HMarcusWH/Reckless` | `main` | `31d9cd6fd2bea6d9f72eeb35e0bac70daa295fb1` | `88763d8e81ea45b938403f7f4feee990ad244c2d` | 85 |
| LC0 | `HMarcusWH/lc0` | `master` | `5cbfeb924c0fcf5efc1b2a43813ac8d63cfd9904` | `6dd8aad55c79fddc291abfede17934f9ce4ca928` | 399 |

The LC0 pin intentionally includes the previously merged adaptive-prefetch and defect-telemetry work.

The authoritative machine-readable record is `vendor.lock.json`. Repository URLs, branch hints, commit SHAs, source tree SHAs, tracked-entry counts, destinations, and pinned external engine artifacts must be read from that file rather than duplicated in maintenance scripts.

## Baseline ancestry is not live-tree equality

The import record answers:

> Which exact upstream source snapshot did this derived engine subtree start from?

It does **not** assert:

> The current engine subtree must remain byte-identical to that upstream snapshot forever.

Future Allfather work will intentionally modify `engines/reckless/`, `engines/stockfish/`, and `engines/lc0/` to add restricted-search controls, telemetry, adapters, and other project-specific integration points. Those modifications are tracked by normal monorepo Git history.

Accordingly:

- changing an engine source file does not itself invalidate upstream provenance;
- changing the declared upstream ancestry requires an explicit lockfile/provenance update;
- CI verifies the declared ancestry record and structural anchors, but does not continuously replace live engine trees with upstream snapshots.

## Import method

`scripts/vendor-engines.sh` is an explicit destructive maintenance tool. It:

1. reads the selected engine definition from `vendor.lock.json`;
2. clones the declared source repository without checking out a working tree;
3. verifies that the pinned commit resolves to the locked Git tree;
4. verifies the locked tracked-entry count;
5. exports the pinned commit with `git archive`;
6. checks archive completeness;
7. writes `.allfather-origin` with repository, commit, tree, and tracked-entry identity.

The script refuses to overwrite an existing engine tree unless `--overwrite` is supplied. It is intentionally **not** used as a CI source normalizer.

Example:

```bash
./scripts/vendor-engines.sh --engine reckless --overwrite
```

Running that command after Allfather-local changes exist will replace the selected live subtree with the exact locked upstream baseline, so it should only be used during an intentional refresh operation.

## External engine artifacts

Source provenance alone is insufficient when an engine build fetches an external model.

The current Reckless baseline uses:

- file: `v60-7f587dfb.nnue`
- size: `63266880` bytes
- SHA-256: `7f587dfb1fe5d74d53909328afa6fd51650c8c7f45907602db7fbb1e52948c61`

Those values are locked under the Reckless artifact record in `vendor.lock.json`.

`scripts/fetch-reckless-network.sh` downloads the file into the ignored top-level build artifact area, verifies both size and SHA-256, and only then exposes the path to the build. `scripts/build-baselines.sh` passes that verified absolute path to Reckless via `EVALFILE`, preventing Reckless's fallback downloader from selecting unverified bytes.

Future external model dependencies, including production LC0 networks when frozen, should use the same lock-and-verify pattern.

## Update policy

An upstream engine refresh must be explicit:

1. choose the new source commit;
2. record its exact Git tree and tracked-entry count in `vendor.lock.json`;
3. review any external model/artifact identity changes;
4. run the explicit vendor refresh for the selected engine;
5. inspect the complete source delta against the current derived tree;
6. reapply or port Allfather-specific modifications deliberately;
7. run the full baseline and regression gates;
8. update this provenance document when the declared ancestry changes.

No moving branch head is considered a reproducible dependency.

## CI boundary

Repository validation CI is read-only with respect to source control. It validates lock syntax, provenance declarations, structural anchors, pinned artifact integrity, engine builds, and UCI startup.

CI must not auto-commit or auto-revendor engine source. This prevents the validation system from erasing legitimate derived-engine development and ensures that source mutations remain explicit reviewed repository changes.

## LC0 strength-facing network provenance

Backend-light LC0 validation intentionally uses no neural network. Strength-facing
LC0 experiments use the separate
`qualification/lc0-strength.lock.json` contract.

An explicit LC0 `WeightsFile` is hashed before process launch and recorded in
the replay engine identity by resolved path, byte size and SHA-256. Changing the
bytes at the same path therefore changes upstream experiment identity.

The initial qualification candidate is network 791556, whose SHA-256 is pinned
by the vendored LC0 release configuration. A strength-facing qualification is
not frozen until its exact byte size is also committed and verified.

