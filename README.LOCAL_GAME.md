# LOCAL-1 execution entry point

See [Full-game qualification](docs/FULL_GAME_QUALIFICATION.md) for the audited design,
five-arm baseline definitions, mandatory failure cases, evidence schema, failure criteria,
and reproduction.

The normal local entry point is:

```bash
# After installing the hash-pinned test dependency and ONLINE-2 build prerequisites:
make -f Makefile.local-game qualification
```

That path runs the LOCAL-1 contract tests, binds a pinned Fastchess source/test/build
identity, builds the frozen ONLINE-2 bundle, freshly reruns LC0 / ONLINE-2 / positive-G3
prerequisites, executes forced rule-transition witnesses, runs the mandatory lifecycle and
failure-injection cases, runs the five-arm same-clock baseline, and independently validates
the complete artifact graph.

The baseline arms are:

```text
Stockfish
Reckless
LC0
Allfather-Anchor
Allfather-G3
```

`Allfather-Anchor` is the native-clock single-Stockfish wrapper control. It is not an
ONLINE-2 or G3-minus-one-toggle profile, so score differences must not be described as a
clean causal estimate of controller overhead.

The required campaign records eight natural lifecycle games plus the twenty color-reversed
baseline games, alongside forced chess-rule witnesses and mandatory injected lifecycle
failures (worker crash, slow shadow shutdown, replay-storage failure, active stop/new-game,
and long generation reuse).

The extended 200-baseline-game soak is an engineering reliability workload, not a strength
sample-size calculation. CI executes it in ten bounded shards; local reproduction may run
the whole soak serially or one shard at a time:

```bash
make -f Makefile.local-game soak
make -f Makefile.local-game soak-shard SHARD=0 SHARDS=10
```

This implements the current roadmap gate. It does not predeclare that LOCAL-1 has passed,
and it makes no Elo/equal-resource/superiority claim.
