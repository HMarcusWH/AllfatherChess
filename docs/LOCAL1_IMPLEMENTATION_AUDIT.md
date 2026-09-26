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
