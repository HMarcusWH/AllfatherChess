# Historical Codex Review Audit

Status: pre-hybrid-shell hardening audit.

This audit covers the user-requested review history for PRs #1, #2, #3, #5, #6, and #7. It records every actionable Codex finding that was available from those PR discussions and how the current tree handles it. PRs #2 and #3 contain Codex usage-limit notices rather than review findings.

## PR #1 — monorepo bootstrap

### Bot-generated vendor commits could bypass validation

**Finding:** the original write-enabled vendor workflow could push with `GITHUB_TOKEN`, while the pushed commit would not reliably trigger the normal validation workflow.

**Disposition:** already superseded by PR #2. CI is read-only and no longer auto-vendors or pushes source changes.

### Vendor workflow targeted a short-lived branch

**Finding:** the bootstrap automation was tied to the bootstrap branch and would stop being useful after merge.

**Disposition:** already superseded by PR #2. The write-enabled vendor workflow was removed; explicit destructive refresh is an operator action through `scripts/vendor-engines.sh`.

### Lockfile/import constants could drift

**Finding:** repository/commit constants were duplicated outside `vendor.lock.json`.

**Disposition:** already superseded by PR #2. `scripts/vendor-engines.sh` and `scripts/verify-vendor.sh` read engine identity through `scripts/vendor-lock.py`.

### Reckless could download an unverified NNUE from build.rs

**Finding:** a fresh direct Cargo build could fall back to an unverified network download.

**Repair:** the derived Reckless build script no longer performs its own unverified download. An explicit `EVALFILE` is accepted; otherwise normal engine-local Cargo/Make entry points invoke the repository's `scripts/fetch-reckless-network.sh`, which resolves the lockfile pin and verifies size and SHA-256 before returning the model path.

### Reckless Cargo configuration was skipped from repository-root builds

**Finding:** using `--manifest-path` from the monorepo root does not load `engines/reckless/.cargo/config.toml`, so `target-cpu=native` could be omitted.

**Repair:** build and test commands now execute from `engines/reckless`, preserving the crate-local target configuration.

### LC0 validation build had no real inference backend

**Finding:** the original validation build used `build_backends=false`, so it could not support a real-network LC0 strength/equal-compute claim.

**Disposition:** scope-qualified, not treated as a strength result. The current build is explicitly labeled a **backend-light deterministic regression configuration; NOT strength-qualified**. The random backend is intentionally used for deterministic contract/golden validation. A later strength campaign must separately qualify a real LC0 inference backend, pinned network, and declared CPU/GPU hardware before any strength/equal-compute claim.

This audit does not silently promote the current random-backend LC0 build into a production-strength baseline.

### LC0 defect telemetry arrived only when Search was destroyed

**Finding:** completed-search defect telemetry could remain buffered until a later search/new-game/destruction, risking attribution to the wrong search.

**Repair:** the derived LC0 classic search emits defect telemetry once on the normal completion path before `bestmove`. An atomic one-shot guard keeps the destructor as an abnormal-search fallback without duplicate normal emission. The live telemetry integration now requires the SUMMARY to arrive in the normal search stream before completion.

## PR #2 — foundation hardening

Codex did not return review findings because the review request hit the account's Codex review usage limit. No actionable Codex comments were available to repair.

## PR #3 — golden regression harness

Codex did not return review findings because the review request hit the account's Codex review usage limit. No actionable Codex comments were available to repair.

The separate bootstrap-golden issue discovered manually after merge was repaired by PR #4 and is outside this Codex-comment audit.

## PR #5 — Reckless restricted-root search

### Restricted-root contract regexes used incorrect escaping

**Finding:** initial raw-string patterns used literal backslash sequences and could fail to inspect PV/MultiPV output.

**Disposition:** fixed before PR #5 merged. Current patterns use real `\s` / `\d` regex escapes and the green restricted-root gate exercises them.

### Empty restricted roots could still enter Syzygy ranking

**Finding:** with Syzygy WDL available but no DTZ result, an empty restricted root vector could reach `root_moves[0]` and panic.

**Repair:** root tablebase ranking is skipped when the restricted root set is empty, and `tb::rank_rootmoves` also has a defensive empty-vector early return.

### Helper MultiPV state could remain zero after a zero-root search

**Finding:** `search::start` clamps `multi_pv` to root count. Helper threads could retain zero after an explicit zero-root search and crash on a later normal search in the same process.

**Repair:** every native and WASM helper receives the configured `multi_pv` before every search. The restricted-root integration now performs a Threads=2 zero-root search followed by an unrestricted search in the **same UCI process** and requires the second search to return a move.

## PR #6 — telemetry v1 contract

### Derived fields were rejected only at top level

**Finding:** nested common structures such as `request.ranking`, `candidate.residual`, or `controller.routing_decision` could bypass the raw/derived separation.

**Repair:** forbidden common field names are checked recursively through common payloads. `native.data` is explicitly exempt so engine-native extension schemas remain lossless.

### Non-finite numeric values were accepted

**Finding:** values such as JSON `1e309` decode to infinity and could pass non-negative numeric checks.

**Repair:** finite-number checks now cover common numeric observations, including timestamps, limits, work, engine time, WDL components, and scalar evaluations.

### Uppercase move strings were silently normalized

**Finding:** `E2E4` could validate while the stored raw record remained non-canonical.

**Repair:** telemetry validation now requires the original stored move string to match canonical lowercase UCI syntax.

### Boolean schema versions could equal integer 1

**Finding:** Python treats `True == 1`.

**Repair:** both contract metadata and event `schema_version` require a non-boolean integer equal to 1.

### Terminal-fact provenance was not independently qualified

**Finding:** any non-empty source string could claim a terminal fact, including backend-derived labels such as `stockfish.bestmove_none`.

**Repair:** v1 terminal sources must use the approved independent `rules.*` or `tablebase.*` namespaces.

### Known work semantics did not constrain units

**Finding:** contradictory pairs such as `reckless.uci_nodes/count` or `lc0.uci_nodes/nodes` could validate.

**Repair:** telemetry v1 defines canonical units for the three known UCI node semantics while retaining engine-native extension namespaces.

### Negative fixtures could fail for the wrong reason

**Finding:** several one-record invalid streams would remain invalid merely because they never completed, masking the intended validation check.

**Repair:** the targeted version, Chess960-encoding, and candidate-before-start fixtures now contain enough stream material that removing the intended validation would expose the regression. Additional focused fixtures cover booleans, infinities, uppercase moves, nested derived fields, terminal provenance, and work-unit mismatch.

### Stockfish WDL could be discarded

**Finding:** `UCI_ShowWDL` output needed an explicit portable mapping.

**Disposition:** already fixed in PR #7. It is emitted as the independent `stockfish.uci_wdl` evaluation channel and exercised by adapter tests/live integration.

## PR #7 — read-only telemetry adapters

### Non-finite adapter observation timestamps

**Finding:** `TelemetryStream` accepted NaN/Infinity.

**Repair:** adapter observation time requires `math.isfinite` before stream state changes.

### Event payload could overwrite immutable stream metadata

**Finding:** a payload could replace `event_type`, `search_id`, `sequence`, engine identity, etc., producing serialized state inconsistent with the lifecycle state machine.

**Repair:** `TelemetryStream.emit()` rejects payload collisions with all immutable common event metadata.

### Boolean LC0 defect versions

**Finding:** `{"v": true}` could pass an equality check against version 1.

**Repair:** LC0 defect payload version is a non-boolean integer and must equal 1.


## PR #8 — historical review hardening

The first Codex pass over this hardening PR found three additional issues in the proposed repairs; they are included here so the audit remains recursive rather than stopping at the historical PR boundary.

### LC0 summary snapshot could race final worker iterations

**Finding:** emitting the summary immediately after the stop decision could occur while another search worker was still completing its final iteration, producing an undercount that the one-shot guard would freeze permanently.

**Repair:** LC0 now tracks active search workers. Once stop is raised, the watchdog does not emit final info/defect summary/bestmove until all search workers (including each worker's task-thread teardown) have exited. The live defect integration runs with two LC0 search workers and requires the summary in the normal pre-bestmove stream.

### Native-data exemption could be spoofed in nested common payloads

**Finding:** a path check exempted any nested object named `native.data`, not only the event-level engine-native payload.

**Repair:** the recursive raw/derived guard exempts exactly the top-level event path `native.data`. A focused invalid fixture attempts the nested-request escape, while a valid event-level native fixture proves legitimate extension payloads remain lossless.

### Reckless source-build entry points were broken by mandatory EVALFILE

**Finding:** requiring `EVALFILE` unconditionally broke the documented engine-local Make/Cargo/PGO entry points.

**Repair:** when `EVALFILE` is absent, the derived build script invokes the repository's verified NNUE fetch helper rather than downloading anything itself. CI explicitly runs a Reckless Cargo check without `EVALFILE` to exercise this default verified path.


## Promotion rule before the hybrid shell

The historical-review hardening PR is complete only when:

- every actionable Codex finding above is either repaired or explicitly scope-qualified;
- telemetry contract/static adapter tests are green;
- live Stockfish/Reckless/LC0 telemetry integration is green;
- frozen golden behavior remains unchanged;
- restricted-root tests, including same-process reuse, are green;
- normal post-merge validation remains read-only;
- no strength claim is inferred from the backend-light random LC0 validation build.

After this gate is green and merged, work can proceed to the hybrid UCI/process shell.
