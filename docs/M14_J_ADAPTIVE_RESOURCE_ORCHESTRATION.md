# AllfatherChess — M14-J Adaptive Resource Orchestration Rebuild Plan

> **Status:** active implementation; J0-J7 merged, J8 in progress / PR #55  
> **Current main:** `730d8884c5878b28b5d67492583efb678750bc04` (PR #54 merge)  
> **Frozen fallback composition:** ENGINE-OPT-V2 / PR #44  
> **Latest completed stage:** J7 evidence-backed profile-selection freeze / PR #54  
> **Authority:** documentation only; this plan does not itself promote M14-J behavior  

**Program:** M14-J — Adaptive Resource Orchestration

## 1. End State

The rebuild should turn Allfather from a fixed hybrid chess configuration into a resource-aware metareasoning system over Stockfish, Reckless, and LC0.

```text
                       ┌──────────────────────┐
                       │   HostCapabilities   │
                       │ CPU / memory / GPU   │
                       │ affinity / pressure  │
                       └──────────┬───────────┘
                                  │
┌──────────────────┐              │
│ GameEnvironment  │──────────────┤
│ time control     │              ▼
│ concurrency      │      ┌──────────────────┐
│ network policy   │      │ Game Profile     │
└──────────────────┘      │ Selection         │
                          └────────┬─────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
external UCI clock ────►│ Move Resource Plan │
                        │ wall / CPU / GPU    │
                        │ deadline / reserves │
                        └─────────┬───────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │ Progressive Allocator    │
                    │ "what computation next?"│
                    └──────┬──────┬──────┬────┘
                           │      │      │
                    ┌──────┘      │      └──────┐
                    ▼             ▼             ▼
              Stockfish       Reckless         LC0
              Resource        Resource       Resource
              Profile         Profile        Profile
                    │             │             │
                    └──────┬──────┴──────┬──────┘
                           ▼             ▼
                       Evidence       VERIFY
                           │             │
                           └──────┬──────┘
                                  ▼
                       DecisionAuthorization
                                  │
                         HYBRID / anchor
                                  │
                               bestmove
```

The governing invariant remains:

> **ResourceAuthorization != DecisionAuthorization**

The orchestration layer decides how much computation may be bought. It never grants move authority merely because a solver received more compute.

---

## 2. Freeze the Current Qualified System

PR #44 becomes the frozen fallback reference.

Do not rewrite:

- `config/allfather.online-hybrid-v2.validation.json`
- the selected LC0 `b7-p8-c256k-warm64` profile
- the existing G3 authority policy
- the existing LOCAL-1-v2 qualifier
- `clock_envelope_v1`
- `unified_value_v1`

Add a separate opt-in runtime:

`config/allfather.orchestrated-v1.validation.json`

Fallback semantics:

```text
ORCHESTRATED-v1
       │
       ├── qualified environment → adaptive path
       │
       └── anything uncertain ───→ ENGINE-OPT-v2 fallback
```

No orchestration failure is allowed to weaken the current qualified path.

---

## 3. Introduce Explicit Orchestration Types

### New file

`controller/resource_profiles.py`

Define immutable types such as:

- `HostProfile`
- `EngineResourceProfile`
- `WorkChunk`
- `CompositionProfile`
- `ProfileEvidence`

An `EngineResourceProfile` should bind:

```text
profile_id
family

process identity:
    binary
    network/model
    backend
    environment

game-static:
    Threads
    Hash
    NNCacheSize
    MinibatchSize
    TaskWorkers
    MaxConcurrentSearchers
    MaxPrefetch
    etc.

dynamic:
    phase option overlays
    allowed work chunks

resource requirements:
    cpu_slots
    expected memory
    GPU requirement

qualification:
    source commit
    evidence artifact
    evidence SHA
    supported host domain
```

The controller selects a qualified profile ID such as `stockfish/cpu-medium-v1` rather than synthesizing arbitrary UCI settings.

---

## 4. Classify Engine Settings by Mutation Boundary

| Setting class | Mutation boundary | Examples |
|---|---|---|
| Process-static | process creation only | binary, NNUE/net, LC0 backend, executable args, ISA |
| Session/game-static | idle + game boundary | Threads, Hash, NNCache, minibatch topology, TaskWorkers, affinity |
| Search-dynamic | idle between stages | MultiPV, native work limit, participation, VERIFY allocation |

### Stockfish

Game-static:

- Threads
- Hash
- NumaPolicy

Search-dynamic:

- MultiPV
- bounded `go` request

Frozen:

- Skill Level 20
- UCI_LimitStrength false
- Ponder false
- no Syzygy

### Reckless

Game-static:

- Threads
- Hash

Search-dynamic:

- MultiPV
- bounded `go` request
- Minimal only if explicitly required

Frozen:

- NNUE identity
- no Syzygy in the portable build

### LC0

Game-static:

- backend
- network
- NNCacheSize
- MinibatchSize
- Threads
- TaskWorkers
- MaxConcurrentSearchers
- MaxPrefetch

Search-dynamic:

- MultiPV
- nodes/visits
- participation

Frozen unless separately researched:

- Cpuct family
- FPU
- smart pruning
- contempt
- PolicyTemperature
- HistoryFill
- search algorithm itself

Resource orchestration must not quietly become chess-parameter tuning.

---

## 5. Build Real Host Detection

### New file

`controller/host_capabilities.py`

Detect and record:

```text
architecture
logical CPUs
sched_getaffinity CPU set
physical/core topology when available
SMT topology
NUMA nodes
cgroup v2 cpu.max
cgroup cpuset.cpus.effective
memory.max / memory.current
host physical memory
CPU PSI
memory PSI
GPU presence/type where supported
```

Produce:

```text
HostCapabilities
host_fingerprint
host_class
```

Example future host classes:

```text
cpu-2c
cpu-4c
cpu-8c
cpu-16c
gpu-cuda-X
```

Unknown or unsupported capabilities must fall back to the frozen v2 profile.

### Tests

Add `tests/controller/test_host_capabilities.py` covering:

- cgroup-constrained CPU sets
- CPU quota below visible CPU count
- unknown topology
- missing PSI
- memory restriction
- unsupported accelerator
- stable fingerprinting

---

## 6. Add a Frozen Resource-Profile Catalog

### New file

`qualification/resource-profile-catalog-v1.json`

Initial catalog must contain one exact profile equivalent to the current PR #44 v2 composition.

Example conceptual engine profile:

```json
{
  "profile_id": "stockfish/cpu-small-v1",
  "family": "stockfish",
  "host_domain": "cpu-x86_64",
  "cpu_slots": 1,
  "options": {
    "Threads": 1,
    "Hash": 16,
    "MultiPV": 1
  },
  "work_chunks": []
}
```

The controller may select only catalogued qualified operating points.

---

## 7. Build the Resource Laboratory Before Adaptivity

### New package

```text
tools/resource_lab/
    runner.py
    candidate_matrix.py
    measure.py
    pareto.py
    compose.py
    qualify.py
```

### Stage A — isolated engine profiles

Measure candidate profiles over a frozen position corpus.

Stockfish axes:

```text
Threads
Hash
work budget
```

Reckless axes:

```text
Threads
Hash
work budget
```

LC0 axes:

```text
Threads
NNCache
Minibatch
MaxPrefetch
TaskWorkers
work budget
```

Do not change chess-policy settings.

Record:

```text
move
PV
evaluation
native work
wall
CPU
RSS
repeatability
```

Keep only Pareto-efficient candidates.

### Stage B — composition interference

Measure whole-machine layouts such as:

```text
anchor 1t + SF 1t + Reckless 1t + LC0 1t
anchor 2t + Reckless 1t + LC0 1t
anchor 2t + LC0 2t
```

The optimization target is the whole composition, not isolated engine speed.

---

## 8. Add `CompositionProfile`

A composition profile binds the complete host layout.

Example:

```text
composition/cpu4-balanced-v1

anchor:
    stockfish/cpu2-v1
    CPU set {0,1}

stockfish specialist:
    cpu1
    CPU {2}

reckless specialist:
    cpu1
    CPU {3}

lc0:
    scheduled rather than concurrent
```

The actual winning layout must be measured rather than assumed.

---

## 9. Add Resource Enforcement

### New file

`controller/resource_control.py`

Support:

- effective affinity inspection
- process CPU affinity
- resource-layout identity
- verification that effective CPU placement matches the profile

Later/self-hosted support:

- cgroup v2 `cpuset.cpus`
- `cpu.max`
- `cpu.weight`
- memory ceilings

Supported modes:

```text
enforcement = observed
enforcement = affinity
enforcement = cgroup_v2
```

A profile requiring stronger enforcement may not run under weaker enforcement.

---

## 10. Generalize Existing Runtime Profile Machinery

The repository already has:

- `BackendSpec.phase_options`
- `BackendManager._effective_options`
- `BackendManager.configure_shadow_phase()`
- `UciProcess.configure_idle()`

Generalize these rather than replacing them.

### Modify `controller/runtime.py`

Add:

```text
configure_game_profile(instance, profile)
configure_phase_profile(instance, phase_profile)
effective_profile(instance)
effective_profile_id(instance)
```

Keep `configure_shadow_phase()` as a compatibility wrapper.

### Game-profile transition sequence

```text
all engines quiescent
        ↓
apply affinity/layout
        ↓
configure game-static UCI options
        ↓
isready
        ↓
profile warmup if required
        ↓
ucinewgame/reset
        ↓
isready
        ↓
seal effective profile identity
```

Any mismatch fails closed.

---

## 11. Handle Time-Control Discovery Correctly

Pure UCI often reveals the time control only with the first clocked `go`.

Support two environment sources.

### Preferred: bridge-predeclared

The online bridge supplies before the first search:

```text
GameEnvironment
base_ms
increment_ms
concurrency
network policy
```

Heavy game-static profile selection happens outside the move clock.

### Generic UCI fallback

If no predeclared environment exists:

- keep host-static settings unchanged
- first `go` determines the clock regime
- only cheap dynamic budget decisions may change
- no heavy Threads/Hash/backend mutation on the first clocked path

---

## 12. Introduce `GameEnvironment`

### New file

`controller/game_environment.py`

Contain:

```text
time control
source of time-control information
game concurrency
network reserve policy
host profile
composition-profile candidates
```

Give it a stable canonical digest.

---

## 13. Add a Versioned Adaptive Time Policy

Do not modify `clock_envelope_v1`.

Add:

`controller/adaptive_time.py`

New policy:

`adaptive_clock_envelope_v1`

Inputs:

```text
remaining time
increment
game ply
movestogo if present
host/composition
position complexity
clock differential
declared safety reserves
```

Output:

```text
soft deadline
hard deadline
wall envelope
CPU envelope
GPU envelope
prepare budget
output reserve
```

Unsupported contexts fall back to current v2 `clock_envelope_v1`.

---

## 14. Introduce `MoveResourcePlan`

The move planner decides how much compute is available, not which engine receives it.

```text
plan_id
generation
position_id

total:
    wall
    CPU
    GPU

deadlines:
    soft
    hard

reserves:
    controller
    verification
    emergency/fallback

composition_profile_id
allocator_policy_id
```

---

## 15. Introduce Typed `WorkGrant`s

### New file

`controller/work_grant.py`

Every constituent computation must be admitted by a WorkGrant.

```text
WorkGrant:
    grant_id
    generation
    owner
    instance
    profile_id
    phase

    native_limit:
        kind
        value
        semantics

    reserved_cpu_ms
    reserved_gpu_ms
    wall_deadline_ms

    effective_option_digest
    reason
    allocator_decision_digest
```

Examples:

```text
stockfish / EXPLORE / 50k nodes
lc0 / VERIFY / 128 visits
reckless / VERIFY / 100k nodes
```

Native work units remain engine-specific.

---

## 16. Keep `BudgetLedger` as the Resource Authority

Do not rebuild `controller/budget.py`.

It already provides:

- reservation before execution
- total CPU/GPU envelope
- purpose lanes
- concurrency-safe admission
- settlement
- native work recorded with semantics

Extend reservations to bind:

```text
grant_id
profile_id
allocator_decision_id
```

A WorkGrant without a valid BudgetLedger reservation cannot dispatch.

---

## 17. Replace Fixed Stages with Progressive Computation

Current G3:

```text
EXPLORE n16
VERIFY n16
maybe STAGED_VERIFY n32
```

Adaptive path:

```text
initial cheap landmarks
        ↓
observe
        ↓
choose highest-value next computation
        ↓
execute WorkGrant
        ↓
observe again
        ↓
repeat until:
    confidence sufficient
    no affordable useful compute
    soft deadline reached
    fallback required
```

Example:

```text
ROUND 0
SF probe
Reckless probe
LC0 probe

ROUND 1
allocator chooses one:
    +SF
    +Reckless
    +LC0
    VERIFY

ROUND 2
allocator chooses again
```

Require a strict `max_allocation_rounds`.

---

## 18. Do Not Mutate `UnifiedValueRouter`

Keep current qualified evidence intact.

Add:

`controller/adaptive_resource_router.py`

Its output becomes resource proposals:

```text
BUY(stockfish, chunk S2)
BUY(lc0, chunk L3)
BUY_VERIFY(chunk V2)
STOP_BUYING
FALLBACK
```

The existing unified router remains the v2 regression reference.

---

## 19. Define a Value-of-Computation Decision

Conceptually:

\[
VOC(g | x)
=
P(\text{decision changes} | x,g)
\times
E[\text{benefit} | \text{change},x,g]
-
C(g)
-
R_{\text{deadline}}(g)
\]

Version one remains deterministic and table/model driven.

Candidate features:

```text
owner
profile
work already spent
leader stability
leader flips
PV persistence
cross-engine disagreement
score spread
regime
legal root count
clock state
remaining envelope
host pressure
```

Unknown value means buy conservative evidence or stop/fallback. Missing calibration must never authorize skipping.

---

## 20. Use Cost Distributions, Not Single Averages

Each qualified WorkChunk should record:

```text
median CPU
p95 CPU
maximum qualified CPU
median wall
p95 wall
RSS
native work
completion rate
```

Resource reservations use conservative qualified bounds, not medians.

---

## 21. Extend Regime Detection with Environment State

Add orchestration context:

```text
clock regime
host load regime
CPU pressure regime
memory pressure regime
engine availability
resource remaining
```

The same chess position may receive different compute on different hosts and clocks without changing the move-authority policy.

---

## 22. Add Pressure-Aware Degradation

If runtime conditions worsen:

```text
CPU pressure spikes
memory pressure spikes
worker disappears
GPU fails
```

the allocator may only move downward through a qualified profile lattice:

```text
large
  ↓
medium
  ↓
small
  ↓
current-v2 fallback
  ↓
anchor-only
```

Never promote into an unqualified regime mid-game.

---

## 23. Make Every Allocation Auditable

Add replay evidence such as:

`resource/allocation.jsonl`

Each row includes:

```text
allocation_id
move plan digest
host digest
game environment digest
context digest

candidate grants
estimated values
estimated costs

selected grant
reservation
actual consumption
settlement

effective engine profile
effective UCI options digest
OS resource state
```

Also seal `resource-plan.json` per move/run.

---

## 24. Bind Resource Provenance into DecisionAuthorization

Authorized HYBRID decisions must prove:

```text
the resource plan was valid
all evidence-producing grants were valid
effective engine profiles were qualified
no grant exceeded the move envelope
all reservations settled
proposal evidence came from this generation
```

Add to the authorization snapshot:

```text
resource_plan_id
resource_plan_digest
allocator_policy_id
allocation_trace_digest
profile_catalog_digest
host_profile_id
game_environment_id
```

Move authority remains separate.

---

## 25. Add an Anchor-Only Authority Control

Create a qualification/test mode where:

- resource allocation is identical
- engines are identical
- stages are identical
- router is identical
- proposal is still generated
- authorization is still evaluated
- emitted move is always the Stockfish anchor

Call it conceptually:

`ANCHOR_CONTROL`

This enables META-1:

```text
same machine
same compute
same allocator
same specialist searches
same timing
same evidence

ONLY DIFFERENCE:
    hybrid authorization can affect outward move
```

---

## 26. Start with a Deterministic Allocator

First policy:

`orchestrator_table_v1`

Conceptual behavior:

```text
if host_class == cpu4 and game_class == rapid:
    select composition cpu4-balanced-v1

initial probes = profile-defined

if disagreement high:
    buy verifier

if one specialist unstable:
    buy next qualified chunk from that specialist

if all agree and support sufficient:
    stop

if deadline close:
    stop / anchor
```

No reinforcement learning, online exploration, or arbitrary parameter mutation.

---

## 27. Learned Marginal-Value Models Come Later

Offline records:
```text
context before grant
grant taken
decision before
decision after
deeper/reference result
actual cost
```

Target:

> What did this additional computation buy?

Run learned allocation in shadow mode first.

Compare:

```text
learned proposal
vs
deterministic allocator decision
```

Only held-out qualification can promote a learned allocator.

---

## 28. Exact PR Sequence

| PR | Scope | Behavior change? | Promotion gate |
|---|---|---:|---|
| **J0** | Sync docs and freeze PR #44 fallback | No | Existing CI |
| **J1** | Resource/profile schemas and IDs | No | serialization/contracts |
| **J2** | HostCapabilities + environment detection | No | deterministic host tests |
| **J3** | ResourceProfile catalog + profile loader | No | current-v2 exact representation |
| **J4** | Runtime game/profile manager | Opt-in | profile transition tests |
| **J5** | Affinity/resource enforcement | Opt-in | process/resource tests |
| **J6** | Resource laboratory | No production | reproducibility/evidence |
| **J7** | Freeze qualified engine/composition profiles | No | exact-head profile qualification |
| **J8** | Adaptive clock / MoveResourcePlan | Opt-in | deadline/fallback tests |
| **J9** | WorkGrant + progressive scheduler | Opt-in | budget/concurrency tests |
| **J10** | Deterministic adaptive allocator | Opt-in | fixed-context replay tests |
| **J11** | Allocation evidence + authority binding | Opt-in | replay/integrity tests |
| **J12** | Full orchestrated composition | Yes, new config only | real-engine qualification |
| **J13** | META-1 anchor-control experiment | Test only | frozen comparative campaign |
| **J14** | ONLINE-PLAY-1 10+5 campaign | Test only | descriptive operational result |

---

## 29. J0 — Baseline Synchronization

Modify:

- `docs/CURRENT_STATUS.md`
- `docs/ROADMAP.md`
- `docs/ENGINE_OPTIMIZATION.md`

Record:

- PR #44 merge `7248f25fc64be4d04a78ec2b1f0c9de2986a11a5`
- exact-head aggregate success
- v2 is now the qualified reference/fallback
- M14-J becomes the next architecture program

No code changes.

---

## 30. J1 — Contracts

Add:

```text
controller/resource_profiles.py
controller/game_environment.py
controller/work_grant.py
controller/orchestration_evidence.py
```

Tests:

```text
tests/controller/test_resource_profiles.py
tests/controller/test_game_environment.py
tests/controller/test_work_grant.py
```

Everything immutable, canonical, and digestable.

---

## 31. J2 — Host Detection

Add:

```text
controller/host_capabilities.py
controller/host_pressure.py
```

No engine behavior change.

---

## 32. J3 — Profile Catalog

Add:

```text
qualification/resource-profile-catalog-v1.json
controller/resource_profile_catalog.py
tests/controller/test_resource_profile_catalog.py
```

First catalog must reproduce current v2 exactly:

- engine options
- LC0 warmup
- phase MultiPV
- current resource estimates

If it does not, J3 fails.

---

## 33. J4 — Runtime Profile Manager

Modify `controller/runtime.py`.

Add:

```text
apply_game_profile()
apply_phase_profile()
assert_effective_profile()
```

Keep the legacy path as a compatibility wrapper.

Modify `adapters/process/uci_process.py` only if needed for safer batched idle reconfiguration or option snapshots.

---

## 34. J5 — Resource Placement

Add:

`controller/resource_control.py`

Test:

- profile requesting too many CPUs
- affinity failure
- host cpuset shrinking
- child escape
- mismatched effective affinity

Fallback instead of overclaim.

---

## 35. J6 — Resource Laboratory — **MERGED / PR #52**

Implemented:

```text
tools/resource_lab/
qualification/resource-lab-v1.json
.github/workflows/resource-profile-lab.yml
```

The exact-head J6 artifact on commit
`6aecd0bae7848ca8a9893377fadffb049d336c3a` reported:

- `evidence_valid: true`;
- `lab_complete: true`;
- 57 candidate operating points;
- 1368/1368 Stage-A measurements with zero execution errors;
- 72/72 Stage-B composition batches with zero execution errors;
- high-resolution POSIX process CPU evidence on all nine current-v2 reference buckets;
- 24 retained affinity-observation faults without laundering those observer races into engine failures;
- `promotion_ready: false`, explicitly requiring J7.

Artifact: workflow `36754169712`, artifact `11117218402`,
SHA-256 `e8b153b1cb0eeafeb18796931e3cad116c09f8913a3eb61ac7d60806fc7ec0e7`.

No production behavior changes and no profile-selection authority were granted by J6.

---

## 36. J7 — Freeze Evidence-Backed Profile Selection — **MERGED / PR #54**

J7 does **not** replace or mutate the J3 runtime catalog. The existing
`qualification/resource-profile-catalog-v1.json` remains the frozen J3/J4 contract,
keeps `selection_enabled=false`, and continues to point at ENGINE-OPT-V2 fallback semantics.

Add:

```text
qualification/resource-profile-evidence-v1.json
qualification/resource-profile-selection-v1.json
scripts/qualify-resource-profile-selection.py
tests/controller/test_resource_profile_selection.py
```

The evidence snapshot binds the exact J6 head, source tree, workflow/artifact identity,
artifact member hashes, candidate bundle, laboratory specification, corpus and exact-host
execution domain. It freezes the 18 J6 Pareto candidates and nine family/work-budget groups
needed for deterministic selection without making the repository depend on the retained
Actions artifact at validation time.

Within each family/work-budget group, J7 admits only complete, repeatable, CPU-usable,
process-scope-complete, bestmove-reference-matching Pareto points, then selects
lexicographically by:

```text
median_wall_ms
p95_wall_ms
median_cpu_ms
p95_cpu_ms
p95_vm_hwm_bytes
candidate_id
```

The frozen selection is:

```text
Stockfish n16   -> v2-current
Stockfish n64   -> t1-h32      (Hash=32)
Stockfish n256  -> v2-current
Reckless  n16   -> v2-current
Reckless  n64   -> v2-current
Reckless  n256  -> v2-current
LC0       n16   -> v2-current
LC0       n32   -> prefetch0   (MaxPrefetch=0)
LC0       n64   -> v2-current
```

All nine rows are `isolated_resource_profile` selections. The new Stockfish n64 and
LC0 n32 points were not themselves exercised in Stage B, so J7 records
`composition_qualification=not_established` and does not promote any new whole-machine
composition. Runtime authority, resource authorization and outward move authority remain false.

The independent validator re-expands the frozen 57-candidate J6 matrix from the locked lab
specification and candidate bundle, checks the 9-group/18-Pareto evidence, recomputes every
winner, verifies the evidence/catalog hash chain, and rejects authority or composition overclaim.
The merge gate runs this validator and destructive mutation tests. No new engine search or
retry-until-green laboratory is part of J7.

---

## 37. J8 — Adaptive Outer Time / MoveResourcePlan — **PR #55**

J8 deliberately does **not** replace `clock_envelope_v1`. The historical ONLINE
`TimePlan` remains the clock/deadline safety object, the anchor request source, and the
timing identity consumed by existing G3 authority/replay checks.

Add:

```text
controller/move_resource_plan.py
controller/adaptive_time.py
config/allfather.m14-j-j8.validation.json
qualification/adaptive-clock-v1.json
scripts/qualify-adaptive-time.py
tests/controller/test_move_resource_plan.py
tests/controller/test_adaptive_time.py
```

The J8 relationship is:

```text
external UCI clock
      ↓
clock_envelope_v1 TimePlan             [unchanged safety ceiling]
      ↓
adaptive_clock_envelope_v1
      ↓
MoveResourcePlan                        [resource ceiling only]
      ↓
legacy fixed stages / conservative_v1   [J9 replaces with WorkGrants]
```

The first adaptive policy is intentionally **clamp-only**. It does not introduce an
uncalibrated chess time manager. Soft/hard deadlines, network reserve, output margin and
anchor `go movetime` remain those reconstructed by `clock_envelope_v1`. J8 derives only
a stricter CPU resource ceiling from the TimePlan, current fixed composition and live host
capacity:

```text
effective_parallelism =
    min(
        TimePlan.cpu_parallelism,
        composition.declared_cpu_slots,
        host allowed CPU count,
        cgroup quota equivalents when limited
    )

J8 CPU =
    min(TimePlan CPU, TimePlan wall × effective_parallelism)
```

GPU remains zero. Unknown CPU quota/cpuset/memory or insufficient memory produces explicit
fallback evidence and retains the existing TimePlan envelope rather than inventing capacity.

The validation runtime derives from the anchor-authoritative ENGINE-OPT-V2 profile and adds
only:

```json
"orchestration": {
  "enabled": true,
  "policy": "adaptive_clock_envelope_v1",
  "catalog": "qualification/resource-profile-catalog-v1.json",
  "composition_id": "composition/engine-opt-v2-exact-host",
  "fallback_clock_policy": "clock_envelope_v1",
  "fallback_profile": "engine-opt-v2",
  "allocator_policy_id": "legacy-fixed-stage-compat-v1",
  "concurrency": 1,
  "network_policy": "clock-envelope-v1"
}
```

J8 reads the J3 composition metadata but does not bind stored exact-host qualification as
proof of the current machine. Mechanical host-capacity planning and composition qualification
remain separate. It also does **not** consume the J7 isolated operating-point selections:
Stockfish stays at Hash 16 and LC0 stays at MaxPrefetch 8 in the J8 runtime.

`MoveResourcePlan` is content-addressed and replay-bound but grants neither a WorkGrant nor
outward move authority. J9 owns executable WorkGrant scheduling; J12 owns composition with
hybrid DecisionAuthorization.

Tests/qualification cover 30+1, 10+5, 3+2, movetime, movestogo, huge increment, 8→4 CPU
composition clamp, 2-CPU clamp, fractional quota, unknown-host fallback, insufficient memory,
J7 non-consumption and destructive plan/provenance/authority mutations.

---

## 38. J9 — WorkGrant Scheduler

Refactor opt-in dispatch paths in:

- `controller/shadow.py`
- VERIFY dispatch
- staged VERIFY dispatch

Legacy fixed limits become compatibility WorkGrants:

```text
n16 config
   ↓
WorkGrant(n16)
```

The old system becomes a special case of the new scheduler.

---

## 39. J10 — Adaptive Allocator

Add:

`controller/resource_allocator.py`

Inputs:

```text
BudgetLedger
HostCapabilities
MoveResourcePlan
current evidence
profile catalog
```

Output:

```text
next WorkGrant
or STOP
```

Still no move authority.

---

## 40. J11 — Evidence Hardening

Extend:

- replay manifest
- `resource.json`
- route evidence
- final authorization snapshot

Add:

`scripts/validate-resource-orchestration.py`

Independently recompute:

- profile admissibility
- budget availability
- grant legality
- CPU-slot feasibility
- effective engine options
- settlement arithmetic
- plan → evidence → final decision identity

Never trust producer-written `qualified: true`.

---

## 41. J12 — Full Orchestrated Composition

Add:

`config/allfather.orchestrated-v1.validation.json`

Add qualifier:

`scripts/qualify-resource-orchestration.py`

Required evidence:

1. exact v2 fallback
2. supported adaptive host path
3. low-clock degradation
4. host-pressure degradation
5. specialist crash
6. profile application failure
7. unknown environment
8. real non-anchor HYBRID authority
9. whole-game lifecycle
10. resource accounting

Re-run LOCAL-1 for:

```text
engine-opt-v2
orchestrated-v1
```

The old path must remain green.

---

## 42. J13 — META-1

Primary comparative experiment:

**orchestrated HYBRID vs orchestrated ANCHOR_CONTROL**

Use:

- 50 frozen openings
- colors reversed
- 100 games total

Both sides receive identical:

- host
- composition profile
- allocator
- move envelopes
- WorkGrants
- resource topology

Only final move authority differs.

This isolates the value of the meta-controller's decisions.

---

## 43. J14 — ONLINE-PLAY-1

Then run the intended operational 10+5 comparison:

- direct Stockfish
- direct Reckless
- direct LC0
- Allfather anchor control
- orchestrated G3

Report actual resource consumption.

This is a production-profile comparison, not equal-compute superiority evidence.

M15-B/C remains the formal equal-resource strength campaign.

---

## 44. Final Runtime Responsibility Model

The rebuilt controller should reduce conceptually to:

```text
GameEnvironment
      ↓
Host/Profile Selection
      ↓
MoveResourcePlan
      ↓
Progressive ResourceAllocator
      ↓
BudgetLedger / WorkGrant
      ↓
Engine Execution
      ↓
Evidence
      ↓
DecisionAuthorization
```

Today resource policy is distributed across:

- fixed config constants
- constituent time managers
- routing estimates
- phase limits
- `TimePlan`
- hidden engine defaults

M14-J consolidates resource policy under the meta-controller.

---

## 45. Explicit Non-Goals / Forbidden Shortcuts

During M14-J:

- no arbitrary runtime hyperparameter generation
- no changing Cpuct/FPU/search heuristics
- no learned allocator with outward authority
- no modifying Stockfish/Reckless/LC0 search code to make benchmarks look better
- no heavy engine-state resizing every ply
- no treating constituent nodes as comparable compute
- no using average runtime as a hard reservation
- no silent oversubscription
- no selecting a profile from an unknown host
- no strength claims from engineering qualification
- no removing the PR #44 fallback

---

## 46. Intended End Behavior

After the rebuild, Allfather should be able to reason operationally like:

```text
This is a 4-core CPU host.
This is a 10+5 game.
We have 588 seconds left.
The position is strategically ambiguous.
Stockfish and Reckless agree.
LC0 disagrees and remains unstable.
We have 6 CPU-seconds of solver budget remaining.
```

and issue a governed resource decision such as:

```text
Do not buy more Stockfish.
Do not buy more Reckless.
Give LC0 its next qualified work chunk.
Then spend one qualified VERIFY chunk.
If disagreement survives, anchor fallback unless
DecisionAuthorization is fully satisfied.
```

On another host or time control, it should select differently without changing the underlying move-authority safety model.

---

## Final Objective

M14-J turns Allfather from:

> **a fixed hybrid chess-engine configuration**

into:

> **a resource-aware metareasoning system that orchestrates three prequalified chess solvers under a governed compute budget.**

The rebuild preserves the project's strongest existing properties:

- explicit evidence
- immutable provenance
- hard resource budgets
- deadline safety
- fail-closed behavior
- replayability
- independent qualification
- separation between compute authority and move authority
