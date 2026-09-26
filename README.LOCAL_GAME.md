# LOCAL-1 execution entry point

See [Full-game qualification](docs/FULL_GAME_QUALIFICATION.md) for the audited design,
five-arm baseline definitions, evidence schema, failure criteria, and reproduction.

```bash
# After installing the pinned test dependency and ONLINE-2 build prerequisites:
make -f Makefile.local-game qualification
```

The entry point includes the existing real LC0, ONLINE-2 and positive G3 prerequisites,
eight lifecycle games, twenty same-clock descriptive round-robin games, and independent
rule/PGN/transcript/replay/resource checks. `soak` expands the baseline to 200 games.
This implements the next roadmap gate; it does not predeclare that gate passed.
