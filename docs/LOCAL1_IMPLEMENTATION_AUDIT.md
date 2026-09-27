# LOCAL-1 implementation audit

See [FULL_GAME_QUALIFICATION.md](FULL_GAME_QUALIFICATION.md) for the campaign contract.
This is an implementation/hardening record, not an exact-head CI success certificate.

## Consolidation decision

Two independent LOCAL-1 implementations were accidentally opened in parallel. PR #41 is
the canonical implementation because it already had the more complete isolated qualification
architecture: independent chess parsing, process/subreaper accounting, explicit
session/game/search identity and a modular `tools/local_game/` validator.

The stronger ideas from the duplicate implementation were moved into this branch rather than
merging two overlapping qualification systems. In particular the canonical PR now also has:

- canonical README/roadmap/claim-ledger/release-plan integration;
- stronger prerequisite replay retention and binding;
- exact source-test/build attestation for Fastchess;
- campaign-wide replay identity checks;
- independent resource/envelope reconstruction;
- mandatory failure-injection lifecycle coverage;
- sharded extended soak execution.

The duplicate PR is superseded and must not be merged beside this one.

## Codex hardening incorporated

The implementation now addresses the first two Codex review rounds by enforcing:

- replay finalization before a synthetic finalized-run reuse test advances;
- ONLINE-2/G3 prerequisite replay retention, not report-only evidence;
- mandatory worker crash, slow shutdown, replay-storage, active-stop/new-game and long-reuse cases;
- finite per-search CPU observations;
- resource qualification reconstructed from primitive process/stage/budget rows;
- detached engine-process-group cleanup after outer-runner timeout;
- exact manifest campaign identity;
- replay directory ↔ sealed run-ID binding;
- clean tracked source plus untracked/ignored-fixture rejection;
- frozen base clock and increment checks on actual transmitted UCI commands;
- contiguous game ordinals starting at one;
- exact proxy spec, environment and launched command;
- natural Fastchess termination plus independent rule-result agreement;
- campaign-wide replay run/content uniqueness;
- a workflow-sharded 200-game engineering soak.

The validator still treats every missing/ambiguous datum as failure rather than converting
absence into zero or "not observed".

## Fastchess host discovery

The initial lifecycle build exposed an upstream-host detail: the pinned Fastchess unit test
binary can segfault at process teardown on the Ubuntu 24.04 hosted image even after clean
test-object construction, while Fastchess's own workflow tests Linux on Ubuntu 22.04 with
`vm.mmap_rnd_bits=28`.

LOCAL-1 therefore separates the claims:

1. exact pinned Fastchess source tests run on the upstream-supported Ubuntu 22.04 host;
2. their commit/tree/lock/compiler identity is sealed into an attestation;
3. the Ubuntu 24.04 lifecycle job builds the runner from the same exact source identity and
   records the produced binary bytes/compiler;
4. qualification requires both identities to agree.

This is stronger than ignoring the upstream test crash or falsely claiming the 24.04 test
suite passed.

## Existing controller limitation remains visible

Rapid successive requests can legitimately publish a new anchor while replay-only work from
the prior generation is still draining; in that condition the coordinator declines a second
shadow bundle rather than overwrite callback state.

The synthetic reuse regression now waits for each finalized replay because that test is
specifically about reuse of **finalized** generations. The actual Fastchess campaign does
not add such an artificial replay wait. If natural game pacing produces a searched G3 ply
without its required replay, LOCAL-1 remains red. The qualification harness therefore does
not conceal this current-controller limitation.

## Claim discipline

Implementation completeness does not close LOCAL-1. Only an exact-head successful workflow
with the frozen natural games, mandatory failures, rule probes, prerequisite evidence and
five-arm matrix may do that.

A same-clock W/D/L table remains descriptive only. M15-B/C is the separate equal-resource
strength campaign.


## Third exact-head review and first complete campaign findings

The first run that reached the full tournament exposed two runner-integration defects in
addition to the four Codex evidence findings:

- Fastchess `-strict` makes its benign "No info line available to extract score" warning
  fatal for Allfather, whose qualified UCI contract does not require score-bearing `info`
  lines. The harness now rejects all warnings except that exact wrapper-specific warning
  instead of asking the chess engine to invent a score channel.
- Fastchess rejects `option.BackendOptions=` because its CLI requires a nonempty value.
  The direct LC0 arm now omits empty-valued options and leaves the pinned engine default in
  force; actual transmitted options remain validated.

The same run confirmed that mandatory injected lifecycle faults all passed and that every
pre-existing engine/controller qualification family remained green.

Codex's four new findings are repaired by removing the aliased positive-G3 replay copy,
retaining only freshly report-referenced prerequisite replays, performing unconditional
post-run detached-process cleanup while retaining pre-cleanup leak evidence, and separating
partial soak-shard success from the aggregate 10-shard/208-game soak claim.

## Fourth hardening pass — process identity, raw-shard authentication, and exact Fastchess clocks

The first complete 28-game campaign reached every lifecycle and baseline pairing, then failed
only because the validator incorrectly expected UCI clocks to begin at the nominal base.
Pinned Fastchess initializes `time_left = base + increment`; LOCAL-1 now mirrors that source
semantics. The first searched ply must carry base+increment for both sides, the non-moving
clock must remain exact between consecutive searched plies, and the previous mover may gain
at most one increment after non-negative elapsed time.

The same pass closes the next Codex evidence/security findings:

- subprocess ownership is inherited through a unique LOCAL-1 environment token and bound to
  live non-zombie PID/start identities instead of reusable PGIDs;
- cleanup runs after every bounded subprocess exit and detects both same-group and detached
  descendants before emergency TERM/KILL cleanup;
- proxy child registration is cleanup-safe even if its first evidence write fails;
- rule probes use isolated managed process groups and fail if cleanup was needed;
- replay retention rejects symlinks and special files and copies only fresh report-selected runs;
- Fastchess output is rebuilt into a clean target and its Ubuntu 22.04 source-test attestation
  is mandatory during independent validation;
- specialist reservation closure is reconstructed from real `authorize -> settle|release`
  token transitions rather than trusting `open_reservations`;
- all required/soak jobs consume one exact shared Fastchess + ONLINE-2 binary bundle;
- soak aggregation independently reruns the raw shard validator before allowing the ten-shard,
  208-game aggregate report to promote any full-campaign claim.

None of these changes weakens G3 policy, match clocks, pairing structure, or lifecycle
acceptance criteria.

## Fifth hardening pass — crash-safe Fastchess pin and exact retained-clock state

The source-test job at exact Allfather head `a97f106cfc421f4e8082588d544097010da357c9` exposed the previously frozen Fastchess SIGPIPE crash. LOCAL-1 now pins upstream commit `ccb85325b1db322658687b8be8cfe9f54c495840` / tree `fd43662006af6419242d4f9093344df3520c0660`, which handles that crashed-engine pipe as an ordinary error instead of masking the signal in our harness.

Clock validation now consumes one exact Fastchess `tl=<seconds.millis>s` field per searched PGN ply and reconstructs both UCI clocks from `base + increment`, the frozen opening prefix, and every retained post-move clock. The same pass extends cleanup across all post-Popen proxy initialization, adds whole-tree replay-copy digests, hardens specialist reservation terminal states, and rejects duplicate campaign/replay identities across independently requalified soak shards. No engine, G3 policy, clocks, pairings, envelopes or acceptance thresholds are weakened.
