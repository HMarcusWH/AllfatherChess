# S1 — whole-participant resource evidence and E01 control games

**Status:** implementation candidate, source/destructive tests only; **NOT** a
passed real-game campaign, matched-resource strength claim, deployment or release.

**Dependency:** PR #84 (S1 G3-only output-control). The preceding PR #83 must
be merged in stack order after #81. None of the code here changes move
authority, standard LOCAL-1's participant, original G3, or source
`config/allfather.online-hybrid-v2.validation.json`.

## 1. What is now implemented

- `tools/strength_lab/participant_scope.py` is a transparent UCI process
  supervisor. Fastchess still talks to **exactly** the original
  `tools.local_game.proxy` via inherited pipes. The supervisor never reads or
  writes chess protocol content.
- Before the proxy executes, the wrapper creates a **fresh and private cgroup
  v2** and moves the proxy process into it via child `preexec_fn`. The entire
  controller and engine descendant tree inherits that membership, so the
  `cpu.stat:usage_usec` counter includes startup, search, inter-move work,
  descendants, replay finalization, and their shutdown up to the observed
  cleanup boundary.
- The supervisor remains outside that child cgroup. Its cumulative
  `RUSAGE_SELF` CPU includes Python startup and launcher supervision. Total
  recorded CPU is the **disjoint** sum of child-tree cgroup CPU plus launcher
  self CPU. Do not also add the proxy's old session `proxy_cpu_ms`,
  `reaped_subtree_cpu_ms`, stage CPU or engine-native nodes: that would
  double-count.
- `memory.peak` captures the simultaneous cgroup-wide peak of the proxy and
  its descendants. Launcher `ru_maxrss` is separately captured; the sum is an
  **upper bound** for the combined peak, **not** an exact simultaneous peak.
  The receipt explicitly says `peak_is_exact=false`.
- The scope must be delegated/writable with `cpu.stat`, `memory.current`,
  `memory.peak`, `cgroup.events`, `cgroup.procs` and `cgroup.kill`. Missing
  access is a **failed participant**, never a fabricated zero or a fallback
  to PID/name scans. Symlinked or replaced controls, live descendants,
  interrupted shutdown and missing sessions also fail. Emergency cleanup
  cannot erase the recorded leak.
- `tools/strength_lab/participant_evidence.py` joins **one receipt to each
  independently sealed proxy session**, rehashes session evidence and rejects
  extra/missing files, mismatched scopes, missing resource coverage, impossible
  CPU/memory totals and any attempted qualification-bit promotion.

The wrapper's measurement boundary is named explicitly:
**post-proxy cleanup, before final receipt serialization**. The small
final-receipt write itself is not measured as participant CPU and neither
the wrapper nor the verifier treats these values as a complete energy or
exact peak-memory measurement. Resource reports remain source-bound host
observations, not independent remeasurement of vanished cgroups.

## 2. E01 same-source paired control

`tools/strength_lab/s1_campaign.py` freezes an experimental 16-game
Fastchess campaign:

| Contract | Frozen choice |
| --- | --- |
| Source | original G3-v2 config with the exact #84 S1 Git-blob contract |
| Live arm | `allfather-g3` |
| Output-only control | `allfather-g3-anchor-control` |
| Changed runtime setting | `hybrid_authority.outward_mode` only |
| Openings | source-locked META-1 indices 0, 6, 12, 18, 24, 30, 36, 42 |
| Games | eight matched opening pairs, both colors (16 total) |
| Time control | 30+1, serial; pinned Fastchess |
| Resource capture | strict cgroup-v2 wrapper on **both** arms |
| Acceptance | complete rule-legal games, raw PGN↔UCI↔authority replay joins, verified cgroup/session receipts, no leaked processes |
| Claim | descriptive outcome and per-arm resource consumption; no Elo or equal-compute superiority |

The two arms exercise the same G3 proposal/authorization machinery, but
their complete games naturally diverge if a G3 non-anchor move is published.
They do **not** provide identical per-position search trajectories or
identically consumed CPU merely because the openings, clocks and source
settings match. If the live arm never actually changes an anchor move,
report zero and no positive intervention coverage.

The validator checks full position history, clocked UCI requests, move
legality, source provenance, replay integrity and resource ledgers using
the existing LOCAL-1 code, and independently verifies the cgroup receipts.
Missing work is a failed qualification rather than a dropped game.

## 3. Run on a supported isolated game host

The GitHub-hosted **source test** workflow does not qualify real E01 games.
Use a Linux game host with a writable delegated cgroup v2 subtree, with
the pinned Fastchess and the real frozen b4 engine/network bundle built
against the **same exact Git commit**.

After creating those exact-head engine artifacts and running the standard
G3 pilot real-LC0 preflight on the **same game host**, run:

~~~bash
python3 -m tools.strength_lab.s1_campaign --phase contracts
python3 -m tools.strength_lab.s1_campaign --phase run
~~~

Each attempt receives an immutable source/run/attempt identity under
`build/test-results/strength-s1-e01/`. To re-verify a retained attempt on
the same source checkout:

~~~bash
python3 -m tools.strength_lab.s1_campaign --phase verify \
  --output build/test-results/strength-s1-e01/EXACT-ATTEMPT-ID
~~~

If `cgroup.procs` or `cgroup.kill` is unavailable, refuse the
experiment; do not silently switch to procfs, substitute another LC0
network, lower integrity standards, or count an unfinished game.

## 4. What remains

- **Real E01 evidence still needs execution** on a supported cgroup host.
- Baseline resource envelopes and host pressure/topology must be frozen
  separately before a later matched-envelope *strength* comparison.
- **S2** native-clock/incumbent-epoch evidence contracts, **S3** two-root
  comparison and **S4** publication-time quality veto are separate PRs.
- Public Lichess, distribution and dependency-release authorizations remain
  false.

Source-only tests check the schedule, command construction, original
G3-source derivation and destructive resource/receipt cases. Passing this
work's CI demonstrates code/contracts, not a completed match or a proven
chess-strength improvement.
