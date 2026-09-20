# Upstream provenance

The monorepo vendors complete source snapshots rather than using git submodules. Each engine retains its upstream files, notices, authorship information, and license text inside its vendored tree.

## Pinned imports

| Engine | Source repository | Source ref | Pinned commit |
| --- | --- | --- | --- |
| Stockfish | `HMarcusWH/Stockfish` | `master` | `17a6c8f1eb0da45c2ca405321919519bf4e211ba` |
| Reckless | `HMarcusWH/Reckless` | `main` | `31d9cd6fd2bea6d9f72eeb35e0bac70daa295fb1` |
| LC0 | `HMarcusWH/lc0` | `master` | `5cbfeb924c0fcf5efc1b2a43813ac8d63cfd9904` |

The LC0 pin intentionally includes the previously merged adaptive-prefetch and defect-telemetry work.

## Import method

`scripts/vendor-engines.sh` clones each declared repository, checks out the exact commit, removes only the nested `.git` metadata, and copies the full working tree into `engines/<name>`.

Each imported tree receives an `.allfather-origin` file recording its source repository and exact commit.

## Update policy

Engine updates must be explicit:

1. choose a new source commit;
2. update `vendor.lock.json` and the import script together;
3. re-run the vendor import;
4. inspect the complete diff;
5. rerun baseline regression and build checks;
6. only then merge the provenance update.

No moving branch head is considered a reproducible dependency.
