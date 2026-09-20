# Golden constituent baselines

This directory freezes the pre-controller behavior of the three constituent engines before AllfatherChess begins changing search semantics.

The suite has three separate jobs:

1. enforce UCI lifecycle and process reliability;
2. freeze a cross-checked legal-root move snapshot using Stockfish and Reckless as independent move-generation oracles;
3. record only search-output fields that reproduce across repeated fresh-process runs.

The corpus is intentionally small and branch-oriented. It is **not** a tactical benchmark, Elo test, or strength claim.

## Profiles

- Stockfish and Reckless run single-threaded with fixed small hash tables and fixed node budgets.
- Reckless uses the exact NNUE pinned in `vendor.lock.json`.
- LC0 uses the compiled-in deterministic `random` backend with a fixed seed and no weights file. This is a UCI/search-control regression profile, not a playing-strength profile.
- Every case starts in a fresh process, eliminating transposition/history/cache contamination between cases.

## Recording

```bash
python3 scripts/golden-baselines.py --record
```

Recording repeats every engine/case in fresh processes. `bestmove` must be deterministic. Other normalized fields are promoted into the hard golden fingerprint only when all record-time observations agree.

## Verification

```bash
python3 scripts/golden-baselines.py --verify
```

Verification never modifies committed expectations. It re-checks legal move sets, protocol invariants, terminal handling, legal best moves, and every field that was stable when the baseline was recorded.

Timing, NPS, hashfull, absolute paths, banners, and incidental info strings are not golden fields.

Run evidence is written below `build/test-results/golden/`, including normalized actual outputs and an environment/provenance manifest.
