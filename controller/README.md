# Controller

PR #9 introduced the first externally visible AllfatherChess UCI shell. PR #10 added the first controller-owned chess search-space object: the root ShardLedger. PR #11 is the current shadow-execution milestone.

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

The current implementation remains this PR #10 anchor mode until PR #11 code lands. The frozen PR #11 target adds a **second Stockfish process** so the unrestricted outward anchor and restricted Stockfish shadow worker can search concurrently without conflating roles:

```text
GUI / tournament
      |
      v
AllfatherChess UCI shell
      |
      +--> stockfish-anchor   [unrestricted / outward authority]
      +--> stockfish-shadow   [restricted ledger owner]
      +--> reckless-shadow    [restricted ledger owner]
      '--> lc0-shadow         [restricted ledger owner]
```

Only the anchor may determine the outward bestmove. Shadow workers exist to create replayable evidence for later residual calibration.

Run the validation shell after the three baseline engines have been built:

```bash
python3 -m controller --config config/allfather.validation.json
```

The top-level `allfather-chess` launcher invokes the same module entry point.

See `docs/UCI_SHELL.md` for the frozen PR #9 contract.


See `docs/SHARD_LEDGER.md` for the frozen root-v1 ownership and state-machine contract.

See `docs/SHADOW_EXECUTION.md` for the PR #11 authority, execution, replay, and non-claim contract.
