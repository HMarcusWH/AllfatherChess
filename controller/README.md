# Controller

PR #9 introduces the first externally visible AllfatherChess UCI shell.

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

The controller owns backend startup, UCI handshakes, deterministic validation configuration, position/game-state synchronization, process health, stop/ponder lifecycle, and external UCI identity.

It does **not** yet own chess search allocation. In PR #9 only Stockfish receives `go`; Reckless and LC0 stay synchronized and ready but do not search. There is no voting, residual calculation, shard allocation, automatic fallback, or resource-routing policy.

Run the validation shell after the three baseline engines have been built:

```bash
python3 -m controller --config config/allfather.validation.json
```

The top-level `allfather-chess` launcher invokes the same module entry point.

See `docs/UCI_SHELL.md` for the frozen PR #9 contract.
