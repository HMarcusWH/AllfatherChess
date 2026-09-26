# AllfatherChess documentation map

**Current main milestone:** PR #39 / M14-G3  
**Current execution milestone:** LOCAL-1  
**Status authority:** [CURRENT_STATUS.md](CURRENT_STATUS.md)  
**Forward roadmap:** [ROADMAP.md](ROADMAP.md)

Use this index to distinguish live status/roadmap documents from component contracts and
historical design/audit material.

## Current status and execution

- [CURRENT_STATUS.md](CURRENT_STATUS.md) — authoritative short-form repository state,
  qualification evidence, profile matrix and current gate.
- [ROADMAP.md](ROADMAP.md) — canonical forward execution order from LOCAL-1 to the first
  public canary, plus the parallel strength/optimization track.
- [ONLINE_RELEASE_PLAN.md](ONLINE_RELEASE_PLAN.md) — detailed deployment audit and
  implementation acceptance criteria. ROADMAP owns current ordering where the documents
  differ.
- [BUILD_PLAN.md](BUILD_PLAN.md) — architectural milestone lineage and immediate build-plan
  history.
- [EXTENDED_BUILD_PLAN.md](EXTENDED_BUILD_PLAN.md) — deeper post-controller architecture
  roadmap and research lineage.
- [CLAIM_LEDGER.md](CLAIM_LEDGER.md) — what is PROVED, MEASURED, DERIVED, CALIBRATED,
  POLICY or OPEN.

## Current integrated online stack

- [ONLINE_TIME.md](ONLINE_TIME.md) — ONLINE-1 TimePlan/deadline contract.
- [ONLINE_PROFILE.md](ONLINE_PROFILE.md) — ONLINE-2 real-network CPU reference.
- [ONLINE_HYBRID_AUTHORITY.md](ONLINE_HYBRID_AUTHORITY.md) — M14-G3 qualified
  clock-aware staged hybrid authority.
- [FULL_GAME_QUALIFICATION.md](FULL_GAME_QUALIFICATION.md) — LOCAL-1 pinned Fastchess
  lifecycle gate, exact UCI transcript/replay linkage, and descriptive five-arm baseline.
- [DECISION_AUTHORITY.md](DECISION_AUTHORITY.md) — proposal/authorization/outward-decision
  authority layers, including M14-C and M14-G3.
- [RESOURCE_ACCOUNTING.md](RESOURCE_ACCOUNTING.md) — measured CPU/memory/resource
  certificate semantics and PR #39 two-phase freeze.
- [REPLAY_FORMAT.md](REPLAY_FORMAT.md) — replay bundle/evidence integrity.

## Controller architecture and ownership

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [UCI_SHELL.md](UCI_SHELL.md)
- [SEARCH_SPACE_OWNERSHIP.md](SEARCH_SPACE_OWNERSHIP.md)
- [SHARD_LEDGER.md](SHARD_LEDGER.md)
- [PREFIX_SHARDS.md](PREFIX_SHARDS.md)
- [SHADOW_EXECUTION.md](SHADOW_EXECUTION.md)
- [REFINEMENT.md](REFINEMENT.md)
- [ACTIVE_SPECIALIST_SCHEDULER.md](ACTIVE_SPECIALIST_SCHEDULER.md)

## Evidence, routing and decision research

- [TELEMETRY_SCHEMA.md](TELEMETRY_SCHEMA.md)
- [TELEMETRY_MAPPING.md](TELEMETRY_MAPPING.md)
- [RESIDUAL_CALIBRATION.md](RESIDUAL_CALIBRATION.md)
- [BUDGET_ROUTING.md](BUDGET_ROUTING.md)
- [VERIFY_RELOCK.md](VERIFY_RELOCK.md)
- [COMPARE_RELOCK.md](COMPARE_RELOCK.md)
- [CROSS_FEED.md](CROSS_FEED.md)
- [CROSS_FEED_ADAPTERS.md](CROSS_FEED_ADAPTERS.md)
- [SEARCH_REGIMES.md](SEARCH_REGIMES.md)
- [STAGED_VERIFY.md](STAGED_VERIFY.md)
- [UNIFIED_VALUE_ROUTER.md](UNIFIED_VALUE_ROUTER.md)
- [VALUE_OF_COMPUTE.md](VALUE_OF_COMPUTE.md)

These component documents describe the authority of their own layer. A sentence such as
"this layer has no move authority" should not be read as saying the repository as a whole
cannot emit a G3 HYBRID move; the separate DecisionAuthorization composition owns that
transition.

## Backend/provenance

- [STRENGTH_BACKENDS.md](STRENGTH_BACKENDS.md)
- [UPSTREAM_PROVENANCE.md](UPSTREAM_PROVENANCE.md)

## Theory mapping and audits

- [THEORY_IMPLEMENTATION_MAP.md](THEORY_IMPLEMENTATION_MAP.md) — what architectural ideas
  transferred into controller mechanisms and what explicitly did not.
- [ADVERSARIAL_AUDIT.md](ADVERSARIAL_AUDIT.md) — historical adversarial audit of the
  earlier controller stack. Read with CURRENT_STATUS for present authority.
- [CODEX_REVIEW_AUDIT.md](CODEX_REVIEW_AUDIT.md) — historical early-PR Codex audit.
  It is not the current PR #39 review ledger.

## Reading rule

When documents disagree about **current** milestone order or current repository authority:

1. current code/configuration and qualification artifacts win;
2. CURRENT_STATUS defines the synchronized state;
3. ROADMAP defines forward ordering;
4. component/historical documents retain their narrower scope and lineage.
