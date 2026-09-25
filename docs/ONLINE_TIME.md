# ONLINE-1: clock-derived envelopes and deadline-safe UCI execution

This opt-in timing/lifecycle milestone implements the first work package of
[ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md), anchored at merged PR #35.
It does not implement a deployed bot or widen M14-C move authority.

## Supported surface

Only the new `online_time.enabled: true` active CPU profile changes behavior.
All pre-existing configuration files, source trees, golden expectations, and
hybrid-authority policy are unchanged. Standard chess is the initial scope.
The admitted requests are one `go movetime T`, or both `wtime` and `btime` with
optional nonnegative increments and positive `movestogo`. One canonical,
nonempty, deduplicated `searchmoves` set may accompany either form. Legality
remains the backend/root-oracle responsibility, not the syntactic clock parser.
Duplicates, unknown/malformed tokens, infinite/ponder, mixed work/time limits,
missing clocks, and insufficient time for declared margins are rejected explicitly.

ONLINE-1 refuses hybrid_authority, GPU envelopes and recursive REFINE. Every LC0
instance must explicitly configure a CPU-only `Backend` (`random`, `trivial`, `blas`,
`eigen`, `onnx-cpu`, or `tensorflow-cc-cpu`); omission, accelerator selections, and
unknown selections are rejected because procfs cannot account implicit or device work. The shipped ONLINE-1 validation profile pins
`random`; ONLINE-2 will tighten complete device/profile identity. Converting `clock_v1`
into a bounded anchor command never makes it `movetime_v0` authority.
The anchor remains the exact outward source. The released validation profile
still uses random-backend LC0: it qualifies timing, not playing strength.

## Immutable plan and clock origin

`TimePlan` binds generation, reconstructed position ID, side to move, complete
external command, actual internal anchor command, settings, configured envelope,
per-move envelope and original monotonic receipt. The input reader timestamps
commands before they queue behind state synchronization; delayed processing
cannot start a new budget clock. Startup/network loading precedes the UCI ready
state and is not represented as free game-time initialization.

For clock requests, with own remaining clock C, own increment I and reserve R:

```
horizon = min(movestogo, moves_horizon) when declared; otherwise moves_horizon
desired = floor(max(0, C-R)/horizon + increment_fraction*I)
          + stop_grace_ms + output_margin_ms
hard_ms = min(C-R, desired, max_move_ms, configured_wall_ms)
soft_ms = hard_ms - stop_grace_ms - output_margin_ms
cpu_ms  = min(configured_cpu_ms, hard_ms * cpu_parallelism)
```

For fixed movetime, its value is the entire controller wall allowance; no network
reserve is deducted and preparation does not add time. Increment influences the
allocation but cannot be spent before the current move earns it. A nonpositive
soft interval is a rejection, not an invented minimum think time. `cpu_parallelism`
is an admission policy, not a measurement of actual thread use or CPU isolation.
Specialist fractions are retained and the controller reserve scales with the
per-move CPU cap. Actual overspend is still charged, never clamped away.

The request sent to the anchor is `go movetime soft_ms` with any root restriction
preserved. The independent soft watchdog closes **all** new observation dispatch
windows from the original receipt and asks the anchor to stop. Preparation,
procfs start samples and stream setup share one smaller preparation deadline;
unavailable observation proceeds without a qualified resource claim.

`network_reserve_ms` is an additional declared local reserve from the clock
supplied by the caller. The bridge may already have adjusted that clock; its
allowance and this reserve are separate. ONLINE-3 must freeze both explicitly.

## Lifecycle and failures

The hard watcher cannot be blocked by a stop write: stop IO runs independently.
Process-level token checks span actual stop dispatch, and permit checks run again
at the physical stdin write. Late callbacks cannot emit for another generation.
Only one outward result is emitted. A deadline-expired anchor is terminated and
the shell becomes unhealthy, emitting `bestmove 0000` plus an explicit diagnostic.
This is a failed request/game, **not** a legal fallback move. ONLINE-2/4 supplies
supervisor recovery; no incomplete principal variation is invented as an answer.

Anchor observation is a bounded FIFO on a separate worker. Terminal events use a
reserved slot. Loss/translation failure taints evidence; blocked observation does
not withhold an otherwise timely anchor result. Early anchor completion closes
new work and cancels shadows. A generation-scoped hard tail guard terminates
searches still running at the original hard deadline, without killing a reused
process on a later token. An online quiesce deadline quarantines any un-drained
workers before state mutation. An unfinished replay is never silently replaced.
Backend command/readiness timeouts are shortened only after online startup.

These are bounded controller mechanisms on a normal operating system, not a hard
real-time theorem. An unscheduled Python process, blocked external stdout, process
startup failure, hostile command flood, or queued state synchronization can still
lose a game. Such lateness must remain a failure in qualification, not be hidden by
rebasing a clock. Reconnection and successful recovery are not claimed here.

## Evidence and qualification

Optional `time_plan` and `clock_outcome` fields are added only to online replay
manifests and route artifacts. The original external request stays original;
the anchor stage and its telemetry contain the actual bounded command. Plan IDs
are content digests, not authentication signatures. Replay integrity reconstructs
the policy and checks the plan, source request, position, generation, actual
anchor command and output timing; an altered hash alone cannot authorize policy.

Physical CPU is still measured from procfs plus controller process CPU. Stage
attribution is not added again to whole-process totals. Missing start/terminal
samples deny a measured certificate. A delayed measurement interval crossing a
new anchor generation is marked incomplete, including in `resource.json`, rather
than presented as a clean per-move total. A timely move can coexist with a denied
resource claim: these are distinct facts. Closure/serialization CPU and OS tails
remain visible in the existing measurement contract and qualification caveats.

Run:

```
make online-time-tests
make controller-tests
make telemetry-contract telemetry-adapters
make build-baselines
make online-clock-contract
```

The fast suite uses real subprocess fakes to exercise soft-stop success, ignored
stop/hard failure, blocked stop IO, blocked observation/preparation, asymmetrical
clocks, history parity, stale stop/complete callbacks, repeated games, and explicit
request rejection. Tests are registered in `controller-tests`, so both existing
fast CI workflows run them. `baseline.yml` runs the real-engine contract and
retains its report and replay artifacts. The real positive case requires a full
measured envelope certificate; unsupported/zero-clock rejection is a separate
negative control. No limits or historical goldens are loosened for these tests.

## Next milestone

ONLINE-2 freezes a real-network hardware-bound playing profile. M14-G3 separately
qualifies a clock-aware staged hybrid decision boundary. Full games, network
recovery, packaging, account configuration and the canary remain release gates.
