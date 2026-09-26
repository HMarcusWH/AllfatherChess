> Repository integration: originally frozen as the 25 September 2026 post-#35 deployment audit;
> synchronized on 26 September 2026 after merged PR #36 / ONLINE-1.
> ONLINE-1 and ONLINE-2 are complete. M14-G3, LOCAL-1, packaging, lifecycle qualification,
> release qualification, account setup and the canary remain open. Hardware, licensing and
> branch-protection items remain explicit release gates.

# AllfatherChess — Final plan from current repository to online bot play

**Original review date:** 25 September 2026  
**Status synchronization:** 26 September 2026  
**Repository:** `HMarcusWH/AllfatherChess`  
**Current main commit:** `c499405fd97600546437c1f91b4c0e9e066023cb`  
**Current milestone:** PR #38 / ONLINE-2 merged and main-branch qualification green.  
**Current status authority:** [CURRENT_STATUS.md](CURRENT_STATUS.md)  
**Delivery:** source-backed audit plus synchronized implementation/deployment plan. The repository is not yet a deployed bot and makes no playing-strength claim.

## 1. Decision

Build a bounded online release, rather than continue an open-ended sequence of research features before playing a game.

The critical path is:

```text
PR #38 / ONLINE-2 merged
    -> M14-G3 clock-aware staged hybrid authority
    -> LOCAL-1 full-game lifecycle / controlled baseline matches
    -> ONLINE-3 reproducible package + pinned lichess-bot bridge
    -> ONLINE-4 network/restart/rollback qualification
    -> aggregate release qualification
    -> restricted, unrated Lichess bot canary
    -> broader online experiments
```

Keep a second, distinct promotion track for the original objective: statistically credible superiority over each constituent under the same declared and measured total resource contract. An online experimental release does not need to claim that result first. Conversely, a high online rating or a win against a bot called Stockfish does not establish that result.

Retain the repository's existing semantic milestones. The `ONLINE-*`, `CLOSE-35`, and proposed `M14-G3/G4` labels below are work-package names, not reserved future GitHub PR numbers. Existing M14-H/I and M15-A/B/C remain meaningful; this plan changes dependencies so optional transport/native work does not block the first online experiment.

## 2. Review scope and evidence

The review covered the live repository/tree and critical controller source paths; all 33 returned PRs' metadata, descriptions, and available discussion history; the historical Codex audit; relevant project conversations; current CI checks and downloaded main-branch evidence; and current primary documentation for the online integration. Source reads included complete small files and bounded excerpts of large modules. The approximately 215 KB `controller/shadow.py` was examined through its integration changes and related contracts, not certified line by line.

**This is not a line-by-line re-audit of every vendored engine file.** A full checkout could not be downloaded into the execution environment, so the complete source build and repository test suite were not independently rerun locally. Existing engine behavior was assessed through provenance, PR/audit history, current contracts, and CI artifacts. This limitation must remain visible when using this report as a handover.

### 2.1 Verified baseline

All four returned checks for the reviewed main SHA were successful. The heavy baseline job completed at **18:09:17 UTC**. Its steps included all three builds, frozen goldens, root/prefix ownership, recursive refinement, process accounting, typed cross-feed, regime classification, counterfactual decisions, staged VERIFY, unified routing, LC0 telemetry provenance, UCI shell, bounded hybrid authority, and active routing. [S01, S02]

Downloaded CI evidence:

- Workflow run: `36170876516`.
- Artifact ID: `10880230834`.
- Artifact: `engine-validation-evidence`.
- Archive SHA-256: `73476f4b0ff532be7b46b69a39b0e16f8f8bef178dcef375b37c65160cd78674`.

Independent local checks on that artifact passed: archive hash verification; parsing **51 JSON files** and **779 JSONL records**; **36 referenced stream/resource file checks**; and executing the CI-built LC0 defect-telemetry provenance tracker, which exited 0. These are narrower checks than rerunning the repository suites. The accompanying audit pack contains the original evidence, an inspection script, and its output. [E01]

### 2.2 What the passing artifacts actually demonstrate

| Evidence | Observed result on this exact CI run | Interpretation |
|---|---|---|
| Frozen goldens | Stockfish, Reckless, LC0: 12 cases each, zero mismatches | Regression reference reproduced; LC0's deterministic test backend is not a strength profile. |
| Active hybrid contract | `ANCHOR_FALLBACK`; `proposal_move=null`; reason `no frozen hybrid proposal` | Passing this run did not demonstrate an actual real-engine hybrid override. It does not prove that the implementation can never override. |
| Unified router | `BUY_STAGED_VERIFY`; neither model loaded; three extension resource authorizations | Missing-model conservative path was exercised. This was not a deployment-calibrated SKIP demonstration. |
| Staged VERIFY | Three base stages and three extension stages over one common candidate set | Same-process staged execution and separate authorization were exercised. |
| Active routing | Zero authorized stops; 79 denied stops | Current small-session evidence did not license suppression at the declared thresholds. Do not weaken thresholds just to manufacture activity. |
| Envelope controls | Tight negative control failed wall compliance; separately predeclared headroom positive control passed | The test distinguishes an honest failed certificate from a properly budgeted success. Preserve both controls. |

These observations come from `test-results/*/report.json` and the golden report in the downloaded artifact. [E01]

## 3. Decisions recovered from the project conversations

The September project discussions and the canonical build plan consistently establish the following requirements. [S03]

The product is one controller-owned UCI engine with Stockfish, Reckless, and LC0 as specialist backends—not three unrestricted full-budget engines with a majority vote glued on top. The finished competitive claim must include solver work, deliberate verification, controller overhead, and relevant hardware consumption in one resource contract.

Ownership is exact for assigned EXPLORE regions. Deliberate common-support re-search is VERIFY work and must be identified and charged. Native score and work namespaces remain distinct. Uncertainty should lead to justified additional work or abstention, not invented confidence. Control decisions are prospective, provenance-bound, and replayable.

Chess is an empirical test bed for the strategic controller. Architectural ideas about defect localization, persistence, re-locking, and certificates can guide implementation; neither theoretical vocabulary nor agreement between engines establishes move correctness by itself.

The practical online route is a disclosed bot account, first on Lichess. Do not use a human account or browser automation to bypass a platform's permitted integration.

## 4. PR history: what each step contributed

These are descriptive summaries, not claims that every historical diff was independently rerun. The full PR discussion retrieval and the canonical milestone ledger were used together. #26 and #27 are not missing implementation PRs in the returned series. [S03, S04]

| PR | Contribution | Consequence for this release |
|---|---|---|
| #1 | Three-engine monorepo and pinned import foundation | Preserve ancestry and engine notices. |
| #2 | Read-only CI, authoritative provenance lock, verified external inputs | Never re-vendor or silently update sources during validation. |
| #3 | Deterministic baseline/golden harness | Keep fresh-process reference tests. |
| #4 | Committed frozen goldens and verify-only gate | Behavioral changes must not rewrite their own reference. |
| #5 | Reckless restricted-root/searchmoves support | Root ownership is supported by actual backend restrictions. |
| #6 | Common raw telemetry contract | Keep observed facts separate from derived policy claims. |
| #7 | Per-engine telemetry adapters | Preserve native semantics and terminal facts. |
| #8 | Historical Codex repairs | Carry forward zero-root reuse, concurrency, portability, numeric-validation, and provenance regression tests. |
| #9 | Hybrid UCI/process shell | One public output authority; process failure must remain bounded. |
| #10 | Root ShardLedger | Exact legal-root coverage and disjoint owner assignments. |
| #11 | Shadow execution/replay design contract | Anchor and observation roles are deliberately separate. |
| #12 | Shadow execution, replay, residual calibration, and adaptive observation routing | Extensive review history shows why independent negative controls and narrow changes matter. |
| #13 | Closure/hardening and honest envelope positive/negative controls | Do not count reservation-only success as full resource compliance. |
| #14 | Common-support VERIFY | Expensive overlap is explicit and attributable. |
| #15 | COMPARE/RELOCK analysis | Agreement and persistence are descriptive evidence, not truth. |
| #16 | PrefixShardLedger and descendant dispatch | Prefix-free frontier and exact split/transfer semantics are reusable. |
| #17 | Live shadow REFINE and recursive provenance | Position restoration and quarantine are operational requirements. |
| #18 | Active specialist reservations and settlement | Verification, refinement, and oracle work spend real capacity. |
| #19 | Extended build-plan documentation | Preserve end-state requirements, update stale status. |
| #20 | Roadmap synchronization | Use stable milestone names rather than future PR numbers. |
| #21 | Typed cross-feed plane | Reuse existing evidence rather than invent another unbudgeted search phase. |
| #22 | Frozen counterfactual hybrid proposals | Proposal construction and actual output authorization are separate. |
| #23 | Whole-run VERIFY value-of-compute calibration | This intervention cannot simply substitute for same-process serving calibration. |
| #24 | Real-network LC0 reference qualification | Real inference exists as a reference; it is not a demonstrated competitive release. |
| #25 | Repository-wide runtime/replay/CI hardening | Bounded IPC, exact source qualification, and release controls must survive later work. |
| #28 | Measured process CPU, memory evidence, controller accounting | CPU evidence is implemented; GPU device-time qualification remains separate. |
| #29 | M14-C bounded hybrid authority | Current authority is narrowly qualified for `movetime_v0`. |
| #30 | Bounded multi-level REFINE | Deeper recursion remains outside the narrow authority profile. |
| #31 | Engine-specific cross-feed adapters | Qualified typed proposals are not yet a general live dispatch policy. |
| #32 | Imported LC0 adaptive-prefetch/telemetry provenance repair | Preserve generation-aware attribution and worker accounting. |
| #33 | Structural regime classification and support calibration | Do not infer unsupported entropy, exact-endgame, or time-critical semantics from convenient proxies. |
| #34 | Same-process staged VERIFY substrate | One fresh extension search per participant, with separate resource accounting. |
| #35 | M14-G2 BUY/SKIP staged verification router | Adds one live compute decision, not a general optimizer or integrated move authority. |

Codex usage-limit messages in historical discussions are not successful reviews. The historical audit explicitly records this distinction. [S04]

## 5. Current architecture and release gaps

### 5.1 Implemented mechanisms

The repository has a substantial mechanism stack: process isolation; synchronized chess history; root/prefix ownership; typed telemetry; replay and provenance; residual/calibration infrastructure; explicit verification and refinement; measured Linux process accounting; prospective proposals; bounded hybrid authorization; and the staged router. Rebuilding that foundation would be wasteful. [S03, S05]

### 5.2 Gaps that must be addressed deliberately

**Clock qualification.** `common/search_request.py` reconstructs UCI clock tokens, but reconstruction is not a time manager. The frontend forwards the original search command. Current hybrid authority accepts only `movetime_v0`; it is not qualified merely because a bridge sends `wtime/btime/winc/binc`. [S06, S07]

**Configuration composition.** `runtime.py` explicitly rejects staged VERIFY together with hybrid authority. Enabling both flags is not a valid integration. The shipped G2 profile disables hybrid authority and refinement. [S07, S08]

**Real deployment profile.** G2's validation config uses LC0's random backend and null models. Its fixed 7,000 ms wall budget, node limits, and reserves are test parameters—not a game-clock policy. The real BLAS reference and its measurements must be converted into a complete, tested deployment configuration rather than assumed to apply automatically. [S08, S09]

**Calibration strength.** The current route gates use an in-domain point estimate, minimum training/group support, and held-out bucket observation. A held-out row's existence is not a statistical upper bound on shortcut risk. Decision change is also not decision improvement. [S10, S11]

**Authority coverage.** The current downloaded real-process authority test fell back. A release gate must demonstrate both successful real-backend authority and rejection of invalid evidence. A deployment in which every position falls back is an anchor wrapper, not demonstrated hybrid play. [E01]

**Operational hardening.** Main is currently unprotected. The stable merge gate runs syntax and engine-independent suites; it is valuable, but is not itself an aggregate production-profile, online-lifecycle, and real-engine release gate. The top-level controller licensing decision is explicitly outstanding. [S12, S13, S14]

**Documentation drift.** The extended plan's opening current-state section still refers to the post-#24 baseline and old authority/accounting limitations. README and implemented code are newer. Update status descriptions; do not delete historical scope statements that remain valid for their old profiles. [S05, S15]

### 5.3 Non-overlap must retain its exact meaning

There are four process roles in the observation topology: unrestricted Stockfish anchor, restricted Stockfish, restricted Reckless, and restricted LC0. The three restricted workers have non-overlapping assigned EXPLORE regions. The anchor is not an EXPLORE owner and can overlap them; different paths may also transpose into equivalent boards. Therefore the supported claim is **no accidental overlap in assigned EXPLORE regions**, not zero duplicate position evaluations globally. Deliberate VERIFY and all anchor work remain chargeable. [S03, S05]

## 6. End-state of the first online release

```text
Lichess Bot API
    <-> pinned lichess-bot bridge
        <-> allfather-online launcher / public UCI endpoint
            -> clock normalization and per-move envelope
            -> independently budgeted Stockfish anchor
            -> exact restricted EXPLORE allocations
            -> common-support base VERIFY
            -> staged BUY/SKIP recommendation
            -> separate resource grant/denial
            -> optional complete extension round
            -> immutable, source-bound proposal
            -> versioned decision authorization
            -> exactly one outward bestmove
            -> bounded settlement and auditable artifacts
```

The first release is Linux, standard chess, one game at a time, no pondering, pinned real LC0 inference, and no external move assistance. It preserves an anchor-only operational mode and a qualified hybrid mode as separate profiles. Do not describe the anchor-only mode as a successful three-engine decision system.

A CPU-only reference is the shortest path to an auditable first experiment because Linux CPU accounting already exists. It must still be benchmarked and qualified, and it may not be LC0's best competitive hardware. A GPU release is a separate profile with declared device ownership and an explicitly qualified measurement contract; enabling CUDA is not enough to make a measured-resource claim. [S09]

## 7. Implementation work packages

**All paths marked NEW below are proposals. They do not exist merely because they appear in this plan.** Existing paths identify concrete integration seams. Each work package should be its own reviewable change, or smaller changes where the acceptance boundary warrants it.

### CLOSE-35 — Freeze the baseline and restore release controls

**Suggested title:** `chore: freeze post-M14-G2 release baseline and qualification matrix`

Update `README.md`, `docs/BUILD_PLAN.md`, `docs/EXTENDED_BUILD_PLAN.md`, `docs/CLAIM_LEDGER.md`, and stage-specific documents where their current-status language has drifted. Add NEW `docs/ONLINE_RELEASE_PLAN.md` and NEW `qualification/release-baseline.json`, recording exact commit, known profile capabilities, test artifact identities, and unsupported combinations.

Retain `.github/workflows/merge-gate.yml`. Add NEW `.github/workflows/release-qualification.yml` with explicit dependencies on the selected source/build/profile/online suites. A release gate must not succeed because a required job was skipped. Keep PR validation read-only and never expose deployment credentials to untrusted PR code.

The repository owner must enable protection for main and require the appropriate stable checks. The current connection's ability to inspect a branch is not permission to change its administration. Resolve the Allfather-specific license before distributing a combined release. Preserve upstream licenses and record model/bridge terms separately. [S12–S14]

**Done when:** the reviewed baseline is immutable and reproducible; the documentation agrees with the implementation; required release failures actually prevent promotion; and the remaining release blockers are machine-readable rather than hidden in prose.

### ONLINE-1 — Clock-derived budgets and deadline-safe UCI execution — **COMPLETED IN PR #36**

**Suggested title:** `feat: add clock-derived per-move envelopes and deadline-safe UCI execution`

Modify `common/search_request.py`, `controller/uci_frontend.py`, `controller/runtime.py`, `controller/budget.py`, `controller/routing.py`, and the timing seams in `controller/shadow.py`. Add NEW `controller/online_time.py`, NEW `tests/controller/test_online_time.py`, NEW `tests/controller/test_online_deadlines.py`, and NEW `scripts/online-clock-contract.py`.

Introduce an immutable per-move `TimePlan` containing the original UCI request; side to move; effective available clock; increment; optional moves-to-go; game/generation/position identity; receipt time; internal anchor command; dynamic wall/CPU allocation; and soft/hard deadlines. Use a monotonic clock. Freeze the time policy and its version in the release identity.

For the first implementation, a qualified clock-to-bounded-movetime transformation is acceptable. It must preserve both external and internal requests in evidence and use a new qualified request class. It must **not** rewrite the external request and pretend the old `movetime_v0` certificate already covers it. Existing movetime behavior remains a separate compatibility path.

A simple starting policy allocates a conservative fraction of remaining time plus a bounded portion of increment, capped by actual available time and an absolute profile cap. The coefficients are configuration parameters to qualify, not asserted optimal constants. Use both a soft stop deadline and a hard output deadline. Account for preparation, legal-root enumeration, process dispatch, proposal construction, stop latency, and bridge/network allowance. Never add a floor that exceeds the remaining clock.

The bridge already applies its own overhead logic. Record the boundary between bridge-adjusted clocks and the engine's effective clocks so latency margins are not silently omitted or double-applied. Preserve actual server clocks in game-level records.

Start the anchor before optional observational work where feasible. Do not allow replay-directory creation, stream draining, oracle calls, or cancelled prior-generation work to consume an unbounded portion of the move. Termination must have bounded stop/kill escalation. Never authorize late proposals or start new specialist searches after the decision window closes. Charge unavoidable cleanup and prevent it from contaminating the next move.

**Tests:** alternating colors; asymmetric clocks; increments and no increment; forced moves; one/two legal roots; malformed, duplicate, and conflicting go limits; near-zero clocks; slow startup; slow stop; blocked telemetry; queued previous-generation work; repeated `position/go/stop`; and preservation of legacy fixtures. Ponder and unsupported variants remain explicitly disabled in the online profile.

**Done when:** the same immutable TimePlan bounds all per-move work; clock inputs cannot accidentally enter legacy hybrid authority; adversarial lifecycle tests produce at most one valid outward answer for each accepted live search; and stale workers cannot write into the next move.

### ONLINE-2 — Hardware-bound real-inference deployment profile

**Suggested title:** `feat: qualify a real-network online execution profile`

Extend `controller/strength_profile.py`, `controller/resource_measurement.py`, and `adapters/resource/linux_proc.py` where required. Add NEW `config/allfather.online.cpu-reference.json`, NEW `qualification/online-cpu-reference.json`, NEW `scripts/qualify-online-profile.py`, and profile validation tests.

Pin engine binaries and derived source commit, Stockfish/Reckless network inputs, LC0 network content hash, backend, all effective options, Python/toolchain/package versions, CPU instruction-set target, host/resource isolation, and the controller/policy/model identities. A missing hash or an autodiscovered/fallback binary must not silently qualify a release.

Do not ship a `target-cpu=native` binary from an arbitrary CI machine and assume it is portable to the deployment host. Either compile for the declared target ISA or build/qualify on the target class. Verify inputs even after cache restoration.

Make thread and memory limits aggregate. Stockfish exists twice in the four-process topology; include both. Constrain LC0/BLAS helper threads and all descendants. Record real process CPU and controller CPU without adding stage-attribution deltas a second time. Bound total memory at the service/container boundary; process-lifetime high-water marks are not exact stage-local peaks. [S09]

Measure cold start, network loading, first move, warm moves, and stop tails. Move preflight outside an accepted game where possible, but do not remove real clock-consuming startup work from accounting by relabeling it preflight. Game-start/cache lifecycle must match the later experiments.

**Done when:** the profile uses actual inference; recorded identities match launched processes; all four backend/controller costs are visible; aggregate resources are enforced; and missing required measurement prevents qualification. This proves an operational profile, not superiority.

### M14-G3 — Compose staged verification with hybrid authority

**Suggested title:** `feat: qualify staged terminal evidence for bounded hybrid decisions`

Modify `controller/decision.py`, `controller/counterfactual.py`, `controller/staged_verification.py`, `controller/shadow.py`, `controller/runtime.py`, and `controller/final_decision.py`. Add NEW `tests/controller/test_staged_authority.py`, NEW `scripts/online-hybrid-contract.py`, and an explicitly versioned integration profile/schema.

Do not delete the runtime guard and call that integration. Keep v0 contracts unchanged and add a new authorization version whose evidence identifies the selected terminal source, base/extension stage IDs, exact candidate order, participating process generations, timing-plan identity, route recommendation, resource outcome, and proposal freeze boundary.

Freeze one source-selection table:

| Condition | Proposed v1 online disposition |
|---|---|
| Supported SKIP, clean base round, all authority gates pass | Base terminal evidence may support a proposal. |
| BUY, all extension stages complete before the boundary, all gates pass | Complete extension terminal evidence may support a proposal. |
| BUY denied, partially dispatched/completed extension, timeout, stale generation, or evidence loss | Anchor fallback. No mixture of base and extension terminals. |
| Anchor completes before proposal publication | Anchor fallback; late evidence is research-only. |
| No adequate candidate set, nonunanimous policy, unsupported request, or illegal proposal | Anchor fallback. |

This table is deliberately conservative. Any later permission to use base evidence after a denied extension needs its own tested semantics, not an accidental default.

Preserve routing recommendation, budget permission, and move authorization as different objects. A missing model's BUY recommendation does not override an exhausted budget. Preserve the PRE_ANCHOR causal barrier and exactly-once output. If the chosen move differs from the anchor's move, discard its ponder continuation.

The current frontend also forwards anchor info. Do not label an anchor PV/score as the hybrid's evaluation when the emitted move changes. Until an authoritative score contract exists, keep score provenance explicit and disable bridge score-based resign/draw decisions and misleading hybrid PGN evaluation annotations. [S06]

**Done when:** genuine real-network backend tests demonstrate an authorized override and an ordinary fallback; fault-injection covers every rejection path; terminal-source reconstruction matches live output; and neither after-the-fact evidence nor test-specific weakened policy is required to make the positive path happen.

### M14-G4 — Qualify production route models and intervention support

**Suggested title:** `feat: bind staged-routing calibration to qualified serving profiles`

Extend `controller/staged_value_of_compute.py`, `controller/staged_decision_calibration.py`, `controller/regime_calibration.py`, and `controller/unified_value_router.py`. Add NEW `scripts/online-calibration-sweep.py`, a versioned model-compatibility manifest, and targeted drift/leakage tests.

Collect the intervention actually served: same processes, same candidate universe/order, the declared warm-state lifecycle, and the exact base-to-extension limits. Retain #23's whole-run experiments as distinct data, not interchangeable labels. Preserve missing/censored labels as missing.

Bind model compatibility to engine/source/network/backend/options, feature extractor, intervention, clock/budget class, and relevant host profile. Test same-path network replacement and policy drift. Group splits by related positions and game/opening ancestry where needed; exact FEN deduplication alone should not allow adjacent positions from one game to leak into the holdout.

Retain separate train, calibration, and untouched qualification data. Strengthen shortcut qualification with a predeclared uncertainty-aware risk criterion and sufficient independent position-group support. Do not interpret the current held-out-observed check as a confidence guarantee. Freeze the test criterion before looking at its final holdout; collect a fresh holdout after material tuning.

Report two separate questions: whether extension changes the policy decision, and whether the resulting move/game outcome is better after paying for it. Cheap unanimous stability can be consistently wrong. Treat independent analysis as diagnostic evidence with its own limitations, and use controlled games for playing-strength claims.

For an initial experimental release, unsupported regimes may retain conservative BUY/resource-denial/fallback behavior. Such a release cannot claim successful learned compute savings. Before promoting the adaptive router, require prospective real-profile examples of supported SKIP and measured savings without an unacceptable degradation under the frozen criterion.

**Done when:** train/serve identity is checked; no unseen/OOD shortcut is admitted; both live policy branches are qualified; held-out risk and coverage are reported; and model updates are immutable offline promotions, not live self-modification.

### M15-B / LOCAL-1 — Full-game qualification and strength infrastructure

**Suggested title:** `test: add full-game lifecycle gauntlet and equal-envelope matches`

Add NEW `scripts/run-match-campaign.py`, NEW `config/matches/*.json`, NEW `tests/online/`, NEW `tests/fixtures/online/`, NEW `schemas/match-campaign-v1.schema.json`, and NEW `docs/MATCH_PROTOCOL.md`. Reuse a pinned established UCI match runner, such as Fastchess, rather than rebuilding a chess tournament manager. Verify its integration with the selected controller/profile. [S18]

Run full games rather than isolated `go` commands only. Cover castling, promotion, en passant, checkmate/stalemate, repetition and full move history, low clocks, long games, no legal moves, worker crashes, slow shutdown, filled replay storage, restarts, and fake network reconnects. Test hundreds of consecutive moves for resource/process leakage.

The minimum comparison matrix is:

| Arm | Purpose |
|---|---|
| Direct Stockfish / Reckless / real-network LC0 | Each constituent baseline under the declared host/envelope. |
| Allfather anchor-only | Wrapper, clock-policy, and process-management tax. |
| Anchor plus observational machinery, authority disabled | Cost of evidence collection without hybrid decisions. |
| Hybrid with fixed verification policy | Value of the decision mechanism before learned skipping. |
| Hybrid with qualified G2 routing | Incremental value of adaptive compute allocation. |
| Later REFINE/adapter/ownership policies | Separate ablations, not bundled explanations. |

Pin openings, play color-reversed pairs, record seeds/versions, and control caches, pondering, books, tablebases, CPU/GPU availability, memory, and workload isolation. Native node counts are useful intervention parameters but not a cross-engine compute currency. Keep wall-time and measured-resource comparisons explicit. Count controller, anchor, all specialists, and cleanup; do not hide extra research work between turns.

Use a predeclared fixed-sample analysis or suitable paired-game sequential test. Fishtest's methodology is a useful implementation reference, not a guarantee that its service will accept this hybrid engine. Do not use optional stopping with ordinary repeated significance checks. Freeze the superiority/non-inferiority hypotheses, practical effect size, error budget across the three opponent claims, maximum budget, and inconclusive outcome. [S17, S18]

An initial **proposed engineering gate** is 200 completed local games across the selected qualification matrix with no illegal outputs, unexplained duplicate moves, leaked processes, or controller-attributable time forfeits. This count is not a statistical strength sample-size calculation. Expand it when failure modes or uncertainty require it.

**Done when:** complete-game lifecycle reliability is demonstrated; a genuine hybrid decision path is exercised; results and overhead are reproducible; and the experiment can return failure or inconclusive without changing its criteria. Superiority is a later promotion criterion, not a prerequisite for a clearly labeled online experiment.

### ONLINE-3 — Package the engine and reuse lichess-bot

**Suggested title:** `feat: package AllfatherChess for controlled Lichess bot play`

Add NEW `deploy/Dockerfile`, NEW `deploy/compose.yml` (or one equivalent systemd deployment, not two mandatory platforms), NEW `deploy/lichess/config.example.yml`, NEW `deploy/bin/allfather-online`, NEW `scripts/release-manifest.py`, and NEW `docs/ONLINE_OPERATIONS.md`. Add bridge/launcher smoke tests using the exact pinned bridge version.

The launcher sets a known working directory and executes the controller with an immutable online configuration. Keep all diagnostic output off UCI stdout. Store durable game/replay data on a writable bounded volume while release files and model inputs remain read-only. Run as a non-root service, enforce CPU/memory/process limits, and ensure a supervisor terminates the entire engine process group on failure. GitHub Actions builds and qualifies releases; it is not the host for persistent games.

Do not write a new Bot API client without a demonstrated missing requirement. The maintained `lichess-bot` bridge supports UCI and already provides challenge handling, game integration, and optional move sources. Pin a tested revision and dependencies. [S16]

Start from the pinned bridge's complete default config, not an unvalidated minimal replacement. Apply this intent:

| Setting | Initial choice |
|---|---|
| Engine | Allfather launcher, `protocol: uci`, explicit working directory. |
| UCI options | Empty/only actually advertised supported options; not copied Stockfish Hash/Threads defaults. Internal allocation belongs in Allfather's profile. |
| Ponder | Disabled. |
| Game slots | `challenge.concurrency: 1`. |
| Incoming opponents | Bots only; explicit initial allow-list. |
| Variant | Standard only. |
| Initial time control | 10+5, unrated/casual. |
| Matchmaking | Disabled during first canary; enabled deliberately after qualification. |
| Move sources | Polyglot, cloud analysis, online opening books, and bridge-owned tablebases disabled. |
| Score-driven adjudication | Automatic resign and draw logic disabled until score provenance is compatible. |
| Records | Per-game PGN plus linked per-move controller evidence. |
| Credentials | Least-privilege bot token injected as a secret/environment credential, never committed or logged. |

These settings are selected to observe the engine itself, not a hidden book/cloud-assisted composite. A later assisted profile can be separately declared and tested. [S16]

Lichess's current guide calls for a new account with no prior games, a `bot:play` token, and an irreversible BOT upgrade. Its `-u` upgrade command can also start the bot, so configure the restricted policy before performing that operator-approved step. The guide supports `LICHESS_BOT_TOKEN`. Do not convert the user's normal account. [S19, S20]

**Done when:** a clean deployment host can reproduce the package; the bridge completes startup with the actual advertised UCI options; token handling is tested; and a fake server can exercise game lifecycle without public games.

### ONLINE-4 — Network/game lifecycle and observability

**Suggested title:** `test: qualify bot reconnects, terminal states, and deployment recovery`

Build fake-server transcripts and integration tests around the chosen bridge. Reuse its state machinery; patch a pinned fork only when a demonstrated gap exists. Map every accepted game and ply to a controller run ID and release ID, including the full move history. Preserve official game clocks, effective bridge clocks, and internal TimePlan separately.

Exercise duplicated/out-of-order observations, disconnect while thinking, disconnect after sending a move but before acknowledgement, HTTP errors/rate limiting, invalid token, service restart, opponent disconnect, game end during search, and late worker output. Reconcile uncertain submissions against authoritative game state before retrying. Do not create a fresh search from a bare FEN that discards repetition history. Respect the platform/client's documented retry rules; no aggressive polling loop.

Monitor authority counts, actual anchor replacements, fallback reasons, staged BUY/SKIP/denial counts, phase timings, deadline headroom, crashes, CPU/memory totals, and storage pressure. A move matching the anchor and a move authorized by the hybrid are different metrics. Record whether a resource certificate was qualified, failed, or incomplete; never convert absent evidence into zero cost.

Rollback means stop accepting new games, finish or safely terminate the current game according to the incident, and return to a previously qualified immutable profile. Do not hot-swap weights/models/binaries halfway through a game. Do not count silently excluded failed games as a successful test campaign.

**Done when:** uncertain network outcomes do not duplicate moves; restarts reconstruct correct history; every game has an auditable release identity; and the operator has one reliable stop-new-games switch and a tested rollback procedure.

### ONLINE-RC — First public bot experiment

Freeze an experimental release such as `v0.1.0-online-rc1` only after the preceding operational gates pass. This name is proposed, not an existing tag.

First run a deliberately restricted canary: one concurrent game, standard chess, real inference, unrated 10+5, approved available bots, full records, and no external move assistance. A proposed starting batch is **50 completed canary games**. This is an operational learning batch, not proof of an Elo advantage.

Review any time forfeit, illegal output, missing replay, failed resource certificate, repeated all-anchor behavior, unexpected route suppression, or unexplained move submission immediately. Expand opponents and test 5+3 only after the first clock/lifecycle profile is sound. Enable broader matchmaking and rated play only for a release that genuinely tries to win and meets the frozen operating policy. Do not guarantee admission to other competitions or bot opponents' acceptance.

**Done when:** the named release plays real bots through the permitted API; its results are viewable and traceable to per-move evidence; genuine hybrid decisions occur where qualified; failures are retained; and rollback is operational.

## 8. After first deployment: how the original strength objective is closed

The first online release ends the integration project, not the research objective.

**M15-A — Governed policy evolution.** Improve candidate nomination, root ownership/allocation, verifier selection, reserve fractions, and source-specific evidence use as separate immutable policy generations. Current modulo partitioning is an exact ownership baseline, not a demonstrated optimal assignment of positions to engine strengths. Promote only on prospective quality/resource evidence. Keep runtime policy immutable during a game.

**M14-H/I — Measured IPC/native experiments.** Profile the deployed UCI boundaries first. Consider structured IPC or native hooks only where measured benefit exceeds integration and semantic risk. Require source/behavior parity, ownership/provenance preservation, and fresh license review for tighter combinations. These experiments are not prerequisites for the first public bot. [S03]

**Recursive refinement and adapter dispatch.** Existing deeper REFINE and typed adapter proposals can graduate into live resource/routing/decision policy only with their own capability, restoration, depth, budget, and outcome gates. Do not enable every completed research module simultaneously and lose the ability to attribute changes.

**M15-B/C — Comparative qualification and claim-bounded release.** Run the frozen campaign against each pinned constituent and then a clearly defined external reference set. Give every arm the same declared hardware/clock/resource opportunity, and report actual consumption. A constituent that cannot use an offered GPU does not create a universal CPU/GPU equivalence; label the resource regime. Show effect sizes, uncertainty, all losses/timeouts, and ablations. A CPU-reference win, a GPU-profile win, and “best engine overall” are different claims.

If the hybrid cannot beat the best constituent in its tested regime, retain the functional online release as an experiment and use the ablations to identify the bottleneck. Do not convert that outcome into a stronger statement by selecting weaker opponents, changing the reference version after seeing results, or dropping overhead from the accounting.

## 9. Current next implementation after ONLINE-1

ONLINE-1 is merged. The next behavior-changing release milestone is **ONLINE-2:
Hardware-bound real-inference deployment profile**. It should build on the existing
pinned LC0 BLAS/network qualification rather than inventing a second real-inference
scheme, and it must bind the actual online host/resource class, engine binaries,
networks, options and aggregate process/controller costs.

M14-G3 follows ONLINE-2 and must add a new clock-aware staged authority contract;
removing the current staged/hybrid or clock/hybrid runtime guards is not sufficient.
The positive integration gate must exercise a genuine real-backend HYBRID override as
well as ordinary fail-closed anchor fallback.

M14-G4 production SKIP qualification may proceed alongside LOCAL-1/ONLINE-3. It becomes
a hard gate before learned SKIP is promoted, but a conservative first canary may retain
BUY/resource-denial/fallback behavior when shortcut evidence is unsupported.

## 10. Final acceptance checklist

A first online release is ready when the source/binaries/networks/config/model identities are pinned; a real inference profile is qualified; all work follows the same clock-derived envelope; staged routing and move authority have explicit compatible semantics; both positive authority and fallback paths are tested; no unqualified shortcut is admitted; whole games finish legally without controller lifecycle failures; the bridge contributes no hidden move source; credentials remain secret; the account is a disclosed bot; game-to-replay linkage works; and rollback is tested.

Remaining operator inputs are the deployment host/resource class, the new bot-account identity/token, approved initial opponents, and the explicit controller licensing decision. This plan does not pretend those inputs have already been supplied or actions performed.

**Bottom line:** qualify the existing mechanism as one clock-safe, real-inference, observable playing engine; deploy it through an established bot bridge; then let controlled matches determine whether the metacontroller repays its cost.

---

## Sources and evidence registry

Repository links below are anchored to the reviewed SHA unless the item is inherently a live status endpoint. Source descriptions distinguish code, declared intent, and observed CI output.

- **S01 — PR #35:** `https://github.com/HMarcusWH/AllfatherChess/pull/35`
- **S02 — Main CI:** `https://github.com/HMarcusWH/AllfatherChess/actions/runs/36170876516`; all-check status read for commit `9a1414b8d7897e856364b15423fe3efb5a7cc7f7`.
- **S03 — Canonical build plan:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/docs/BUILD_PLAN.md`
- **S04 — Historical review audit:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/docs/CODEX_REVIEW_AUDIT.md`
- **S05 — Current README:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/README.md`
- **S06 — UCI/request paths:** `controller/uci_frontend.py` and `common/search_request.py` at the reviewed SHA.
- **S07 — Configuration and authority:** `controller/runtime.py`, especially staged/hybrid incompatibility and request-class loading; `controller/decision.py`, especially `authorize_decision`, at the reviewed SHA.
- **S08 — G2 fixture:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/config/allfather.unified-value.validation.json`
- **S09 — Resource semantics:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/docs/RESOURCE_ACCOUNTING.md`
- **S10 — Router:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/controller/unified_value_router.py`
- **S11 — Staged calibration:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/controller/staged_decision_calibration.py`
- **S12 — Branch state:** `https://api.github.com/repos/HMarcusWH/AllfatherChess/branches/main` and `/rulesets`, read 25 September 2026; branch reported `protected:false`, required checks disabled, and no listed rulesets.
- **S13 — Merge gate:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/.github/workflows/merge-gate.yml`
- **S14 — Licensing notice:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/LICENSES.md`
- **S15 — Extended roadmap:** `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/docs/EXTENDED_BUILD_PLAN.md`
- **S16 — Bridge and configuration:** `https://github.com/lichess-bot-devs/lichess-bot`; `https://github.com/lichess-bot-devs/lichess-bot/blob/master/config.yml.default`; `https://github.com/lichess-bot-devs/lichess-bot/wiki/Configure-lichess-bot`. Read 25 September 2026; implementation must pin its tested revision.
- **S17 — Primary match-methodology reference:** `https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html`
- **S18 — Primary UCI match-runner reference:** `https://official-stockfish.github.io/docs/fishtest-wiki/Running-Fastchess.html`. The official documentation supplies an example with repeated paired games, a pinned opening input, UCI configuration, seed, concurrency, and PGN output. Verify the chosen runner revision and CLI as part of LOCAL-1 implementation.
- **S19 — Bot account/token setup:** `https://github.com/lichess-bot-devs/lichess-bot/wiki/How-to-create-a-Lichess-OAuth-token`
- **S20 — Irreversible BOT upgrade:** `https://github.com/lichess-bot-devs/lichess-bot/wiki/Upgrade-to-a-BOT-account`
- **S21 — Chess.com public API limitation:** `https://www.chess.com/news/view/published-data-api`; current documentation explicitly says PubAPI is read-only and cannot submit moves. This is why it is not the proposed first deployment path, not a claim that no private/organizer integration can ever exist.
- **S22 — PR #36 / ONLINE-1:** `https://github.com/HMarcusWH/AllfatherChess/pull/36`; merged main commit `64aa8fd13c390b9b37b8825d8f39e73d9bdbdbf8`.
- **S23 — Current main qualification runs:** Merge gate `36193538618`; Controller shell `36193538737`; Telemetry `36193538611`; LC0 real inference `36193538614`; Baseline engine validation `36193538665`.
- **S24 — Current synchronized status:** `docs/CURRENT_STATUS.md` and `qualification/release-baseline.json` on the post-#36 documentation branch.
- **E01 — Downloaded CI evidence:** `allfather-main-9a1414-validation-evidence.zip`; inspection output `audit/independent-checks.json`; reproducible local inspector `audit/check_ci_evidence.py` in the accompanying audit pack.

For core source paths not expanded as a URL above, prepend `https://github.com/HMarcusWH/AllfatherChess/blob/9a1414b8d7897e856364b15423fe3efb5a7cc7f7/`.
