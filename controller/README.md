# Controller

PR #9 introduced the first externally visible AllfatherChess UCI shell. PR #10 adds the first controller-owned chess search-space object: the root ShardLedger.

Generation 1 is deliberately an **anchor-mode process controller**:

```text
GUI / tournament
      |
      v
AllfatherChess UCI shell
      |
      +--> Stockfish  [searching anchor]
      +--> Reckless   [managed / ready]
      '--> LC0        [managed / ready]
```

The controller owns backend startup, UCI handshakes, deterministic validation configuration, position/game-state synchronization, process health, stop/ponder lifecycle, external UCI identity, live legal-root qualification, and root-v1 shard ownership.

`BackendManager.legal_root_moves()` uses the Stockfish anchor's `go perft 1` output as the canonical live root oracle. `RootShardLedger` can then atomically lease those roots across the authorized owner set with exact coverage and zero assigned-prefix overlap.

Live gameplay is deliberately unchanged: Stockfish alone still receives external `go`; Reckless and LC0 stay synchronized and ready but do not search. The PR #10 real-engine contract exercises their owned regions sequentially only as qualification. There is still no voting, residual calculation, adaptive routing, automatic fallback, or resource scheduling.

Run the validation shell after the three baseline engines have been built:

```bash
python3 -m controller --config config/allfather.validation.json
```

The top-level `allfather-chess` launcher invokes the same module entry point.

See `docs/UCI_SHELL.md` for the frozen PR #9 contract.


See `docs/SHARD_LEDGER.md` for the frozen root-v1 ownership and state-machine contract.
