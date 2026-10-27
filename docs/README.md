# AllfatherChess documentation map

**Synchronized 25 October 2026; source baseline `b1ee33b19b6c`.** This is an
exhaustive index of repository Markdown documents under `docs/` (except this
index itself). Use [CURRENT_STATUS.md](CURRENT_STATUS.md) for current mechanism
and qualification state, [ROADMAP.md](ROADMAP.md) for future order, and
[CLAIM_LEDGER.md](CLAIM_LEDGER.md) for nonclaims and evidence boundaries.

**How to read this repository:** committed code and immutable policy constrain
what is permitted; source- and execution-domain-bound qualification says what
was demonstrated; historical PR plans do not amend either. Four distinct claim
levels are implemented, qualified, deployed and strength-supported.

## Read first / current authority

- [CURRENT_STATUS.md](CURRENT_STATUS.md)
- [ROADMAP.md](ROADMAP.md)
- [CLAIM_LEDGER.md](CLAIM_LEDGER.md)
- [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md)

## Architecture, resource and engine contracts

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DECISION_AUTHORITY.md](DECISION_AUTHORITY.md)
- [ENGINE_OPTIMIZATION.md](ENGINE_OPTIMIZATION.md)
- [M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md](M14_J_ADAPTIVE_RESOURCE_ORCHESTRATION.md)
- [RESOURCE_ACCOUNTING.md](RESOURCE_ACCOUNTING.md)
- [SEARCH_SPACE_OWNERSHIP.md](SEARCH_SPACE_OWNERSHIP.md)
- [SHARD_LEDGER.md](SHARD_LEDGER.md)
- [PREFIX_SHARDS.md](PREFIX_SHARDS.md)
- [SHADOW_EXECUTION.md](SHADOW_EXECUTION.md)
- [REPLAY_FORMAT.md](REPLAY_FORMAT.md)
- [UCI_SHELL.md](UCI_SHELL.md)
- [STRENGTH_BACKENDS.md](STRENGTH_BACKENDS.md)
- [UPSTREAM_PROVENANCE.md](UPSTREAM_PROVENANCE.md)

## Evidence, routing and calibration

- [ACTIVE_SPECIALIST_SCHEDULER.md](ACTIVE_SPECIALIST_SCHEDULER.md)
- [BUDGET_ROUTING.md](BUDGET_ROUTING.md)
- [COMPARE_RELOCK.md](COMPARE_RELOCK.md)
- [CROSS_FEED.md](CROSS_FEED.md)
- [CROSS_FEED_ADAPTERS.md](CROSS_FEED_ADAPTERS.md)
- [REFINEMENT.md](REFINEMENT.md)
- [RESIDUAL_CALIBRATION.md](RESIDUAL_CALIBRATION.md)
- [SEARCH_REGIMES.md](SEARCH_REGIMES.md)
- [STAGED_VERIFY.md](STAGED_VERIFY.md)
- [TELEMETRY_MAPPING.md](TELEMETRY_MAPPING.md)
- [TELEMETRY_SCHEMA.md](TELEMETRY_SCHEMA.md)
- [UNIFIED_VALUE_ROUTER.md](UNIFIED_VALUE_ROUTER.md)
- [VALUE_OF_COMPUTE.md](VALUE_OF_COMPUTE.md)
- [VERIFY_RELOCK.md](VERIFY_RELOCK.md)

## Online, game and release runbooks

- [G3_EXPLORATORY_PILOT.md](G3_EXPLORATORY_PILOT.md) — pre-registered real Fastchess G3-versus-anchor descriptive pilot (NOT META-1 or equal compute)
- [G3_PILOT_DURABILITY_INCIDENT_2026-10-26.md](G3_PILOT_DURABILITY_INCIDENT_2026-10-26.md) — negative full-run wall evidence, source-locked postmortem, and proposed versioned repair
- [FULL_GAME_QUALIFICATION.md](FULL_GAME_QUALIFICATION.md)
- [ONLINE_3B_BRIDGE_QUALIFICATION.md](ONLINE_3B_BRIDGE_QUALIFICATION.md)
- [ONLINE_4B_RECOVERY.md](ONLINE_4B_RECOVERY.md)
- [ONLINE_4_RECOVERY.md](ONLINE_4_RECOVERY.md)
- [ONLINE_HYBRID_AUTHORITY.md](ONLINE_HYBRID_AUTHORITY.md)
- [ONLINE_OPERATIONS.md](ONLINE_OPERATIONS.md)
- [ONLINE_PLAY_1_PR_A_HANDOVER.md](ONLINE_PLAY_1_PR_A_HANDOVER.md) — dated PR-A development evidence; final qualification supersedes intermediate failures
- [ONLINE_PLAY_1_RUNBOOK.md](ONLINE_PLAY_1_RUNBOOK.md)
- [ONLINE_PLAY_1_THREAT_MODEL.md](ONLINE_PLAY_1_THREAT_MODEL.md)
- [ONLINE_PLAY_1_PR_B_READINESS.md](ONLINE_PLAY_1_PR_B_READINESS.md) — operator/legal/host trust required; no live authorization
- [ONLINE_PROFILE.md](ONLINE_PROFILE.md)
- [ONLINE_PUBLICATION_DISPOSITION.md](ONLINE_PUBLICATION_DISPOSITION.md)
- [ONLINE_TIME.md](ONLINE_TIME.md)
- [RELEASE_1_OPERATIONS.md](RELEASE_1_OPERATIONS.md)
- [RELEASE_2A_DEPENDENCY_OBSERVATION.md](RELEASE_2A_DEPENDENCY_OBSERVATION.md)
- [RELEASE_2B_PYTHON_LOCKS.md](RELEASE_2B_PYTHON_LOCKS.md)
- [RELEASE_2B_UBUNTU_LOCKS.md](RELEASE_2B_UBUNTU_LOCKS.md)
- [RELEASE_2B_3_4_PLAN.md](RELEASE_2B_3_4_PLAN.md) — independent dependency comparison and separate lock review
- [META_1_EXECUTION_RUNBOOK.md](META_1_EXECUTION_RUNBOOK.md) — real J12-host-only 100-game control, no Elo

## Strength research and evidence

- [STRENGTH_S0_BASELINE.md](STRENGTH_S0_BASELINE.md) — historical G3 nested-stage correction, read-only audit and no-strength-claim boundary
- [STRENGTH_S1_DIAGNOSTIC_CONTROL.md](STRENGTH_S1_DIAGNOSTIC_CONTROL.md) — opt-in G3-only output suppression and pending whole-participant matched control tests
- [STRENGTH_S1_PARTICIPANT_RESOURCES.md](STRENGTH_S1_PARTICIPANT_RESOURCES.md) — strict cgroup-v2 resource wrapper and E01 16-game harness (real evidence pending)
- [STRENGTH_S2_NATIVE_ANCHOR.md](STRENGTH_S2_NATIVE_ANCHOR.md) — native Stockfish observe-only epochs and flight evidence (E00 real-engine qualification pending)
- [STRENGTH_S3A_RESOURCE_CONTRACTS.md](STRENGTH_S3A_RESOURCE_CONTRACTS.md) — S3-A typed candidates, comparator-first one-slot ledger reservations and inert anchor-only opt-in
- [STRENGTH_E00_NATIVE_EQUIVALENCE.md](STRENGTH_E00_NATIVE_EQUIVALENCE.md) — source-locked fixed-node real-Stockfish observer tests, independent evidence and full qualification pending

## Historical plans, incidents and audit trail

- [ADVERSARIAL_AUDIT.md](ADVERSARIAL_AUDIT.md)
- [BUILD_PLAN.md](BUILD_PLAN.md) — historical record (do not read as current status)
- [EXTENDED_BUILD_PLAN.md](EXTENDED_BUILD_PLAN.md) — historical record (do not read as current status)
- [CODEX_REVIEW_AUDIT.md](CODEX_REVIEW_AUDIT.md)
- [CODEX_POSTMERGE_CLOSURE.md](CODEX_POSTMERGE_CLOSURE.md)
- [LOCAL1_IMPLEMENTATION_AUDIT.md](LOCAL1_IMPLEMENTATION_AUDIT.md)
- [LOCAL1_RESOURCE_INCIDENT_2026-10-23.md](LOCAL1_RESOURCE_INCIDENT_2026-10-23.md) — historical record (do not read as current status)
- [PR71_ASSURANCE_REPAIR_LEDGER.md](PR71_ASSURANCE_REPAIR_LEDGER.md)
- [THEORY_IMPLEMENTATION_MAP.md](THEORY_IMPLEMENTATION_MAP.md)

## Claim-reading rule

1. **Current authority:** machine-readable policies/configuration and source-bounded checks.
2. **Qualified:** retained exact-commit evidence with named host, scenario and method.
3. **Not established:** legal/operator approval, public distribution, deployed BOT, or equal-total-resource playing superiority until an explicit independent gate says so.
4. **Historical:** plans, audits and incidents preserve original counterexamples; older “NEXT” text is not current guidance.

Governance issues [#26](https://github.com/HMarcusWH/AllfatherChess/issues/26)
and [#27](https://github.com/HMarcusWH/AllfatherChess/issues/27) track the
still-unprotected `main` branch; presence of a Merge gate workflow does not
by itself enable branch protection.
