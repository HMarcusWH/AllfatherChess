.PHONY: resource-lab-tests resource-lab-run resource-lab-quick resource-lab-preflight resource-lab-v2-run resource-lab-v2-quick resource-lab-v2-preflight resource-placement-tests resource-profile-catalog-tests runtime-profile-tests orchestration-host-tests orchestration-contract-tests engine-opt-tests engine-opt-matrix build-online-engine-opt-v2 engine-opt-v2-contract run-allfather-engine-opt-v2 run-allfather-online-hybrid-v2 local-full-game-v2 local-full-game-tests build-fastchess local-full-game-qualification local-full-game-soak online-hybrid-tests online-hybrid-contract run-allfather-online-hybrid online-profile-tests build-online-cpu-reference online-profile-contract run-allfather-online-cpu-reference online-time-tests online-clock-contract run-allfather-online-clock unified-value-router-tests unified-value-router-contract run-allfather-unified-value staged-verification-tests staged-value-of-compute-tests staged-decision-calibration-tests staged-verify-contract staged-value-of-compute-sweep staged-decision-calibration run-allfather-staged-verify vendor verify-vendor build-baselines smoke-baselines golden-baselines record-golden-baselines telemetry-contract telemetry-adapters telemetry-adapter-integration crossfeed-adapter-tests crossfeed-adapter-contract regime-tests regime-calibration-tests regime-contract controller-tests resource-measurement-tests resource-accounting-contract shard-ledger-tests prefix-shard-tests refinement-tests refinement-policy-tests active-specialist-tests crossfeed-tests decision-tests hybrid-authority-tests counterfactual-tests value-of-compute-tests decision-calibration-tests strength-profile-tests shard-ledger-contract prefix-shard-contract refinement-execution-contract recursive-refinement-contract active-specialist-contract crossfeed-contract counterfactual-decision-contract active-hybrid-decision-contract value-of-compute-contract lc0-strength-contract lc0-defect-telemetry-contract hybrid-shell-contract shadow-tests replay-tests residual-tests routing-tests verification-tests verification-analysis-tests shadow-execution-contract verification-execution-contract verification-analysis-contract active-routing-contract shadow-evidence-sweep verification-evidence-sweep refinement-evidence-sweep counterfactual-decision-sweep value-of-compute-sweep regime-sweep decision-calibration residual-calibration verification-analysis fetch-lc0-strength build-lc0-strength run-allfather run-allfather-shadow run-allfather-verify run-allfather-refine run-allfather-recursive-refine run-allfather-crossfeed run-allfather-counterfactual run-allfather-hybrid run-allfather-value run-allfather-strength run-allfather-active-specialist

vendor:
	@echo "Refusing implicit destructive vendor refresh." >&2
	@echo "Use: ./scripts/vendor-engines.sh --engine <stockfish|reckless|lc0> --overwrite" >&2
	@exit 2

verify-vendor:
	./scripts/verify-vendor.sh

build-baselines:
	./scripts/build-baselines.sh

smoke-baselines:
	./scripts/smoke-baselines.sh

golden-baselines:
	python3 scripts/golden-baselines.py --verify

record-golden-baselines:
	python3 scripts/golden-baselines.py --record

telemetry-contract:
	python3 scripts/validate-telemetry-contract.py

telemetry-adapters:
	python3 tests/telemetry/test_adapters.py

telemetry-adapter-integration:
	python3 scripts/telemetry-adapter-integration.py

crossfeed-adapter-tests:
	python3 tests/adapters/test_crossfeed_adapters.py

crossfeed-adapter-contract:
	python3 scripts/crossfeed-adapter-contract.py

regime-tests:
	python3 tests/controller/test_regimes.py

regime-calibration-tests:
	python3 tests/controller/test_regime_calibration.py

regime-contract:
	python3 scripts/regime-contract.py

engine-opt-tests:
	python3 tests/engine_opt/test_contracts.py
	python3 tests/engine_opt/test_execution_domain.py
	python3 tests/controller/test_engine_opt_runtime.py

engine-opt-matrix:
	@test -n "$(LC0_BIN)" -a -n "$(LC0_WEIGHTS)" -a -n "$(EXECUTION_DOMAIN)" || (echo "Set LC0_BIN=<path> LC0_WEIGHTS=<path> EXECUTION_DOMAIN=<path>" >&2; exit 2)
	python3 scripts/engine-opt-matrix.py --binary "$(LC0_BIN)" --weights "$(LC0_WEIGHTS)" --execution-domain "$(EXECUTION_DOMAIN)" --output build/test-results/engine-opt-matrix/lc0.json

build-online-engine-opt-v2:
	bash scripts/build-online-engine-opt-v2.sh

engine-opt-v2-contract:
	python3 scripts/qualify-engine-opt-v2.py
	@test -n "$(EXECUTION_DOMAIN)" || (echo "Set EXECUTION_DOMAIN=<path>" >&2; exit 2)
	ALLFATHER_EXECUTION_DOMAIN_PATH="$(EXECUTION_DOMAIN)" python3 scripts/qualify-online-hybrid-v2.py

run-allfather-engine-opt-v2:
	python3 -m controller --config config/allfather.online-engine-opt-v2.json

run-allfather-online-hybrid-v2:
	python3 -m controller --config config/allfather.online-hybrid-v2.validation.json

resource-lab-tests:
	python3 tests/resource_lab/test_candidate_matrix.py
	python3 tests/resource_lab/test_measure.py
	python3 tests/resource_lab/test_pareto.py
	python3 tests/resource_lab/test_compose.py
	python3 tests/resource_lab/test_qualify.py
	python3 tests/resource_lab/test_process_cpu.py
	python3 tests/resource_lab/test_observe.py
	python3 tests/resource_lab/test_uci_environment.py

resource-lab-run:
	@test -n "$(EXECUTION_DOMAIN)" || (echo "Set EXECUTION_DOMAIN=<path>" >&2; exit 2)
	python3 -m tools.resource_lab.runner --execution-domain "$(EXECUTION_DOMAIN)" --output build/test-results/resource-lab-v1
	python3 -m tools.resource_lab.qualify --root build/test-results/resource-lab-v1 --output build/test-results/resource-lab-v1/report.json

resource-lab-quick:
	@test -n "$(EXECUTION_DOMAIN)" || (echo "Set EXECUTION_DOMAIN=<path>" >&2; exit 2)
	python3 -m tools.resource_lab.runner --quick --execution-domain "$(EXECUTION_DOMAIN)" --output build/test-results/resource-lab-v1-quick
	python3 -m tools.resource_lab.preflight --root build/test-results/resource-lab-v1-quick --output build/test-results/resource-lab-v1-quick/preflight-report.json

resource-lab-preflight: resource-lab-quick

resource-lab-v2-run:
	@test -n "$(EXECUTION_DOMAIN)" || (echo "Set EXECUTION_DOMAIN=<path>" >&2; exit 2)
	python3 -m tools.resource_lab.runner --spec qualification/resource-lab-v2.json --execution-domain "$(EXECUTION_DOMAIN)" --output build/test-results/resource-lab-v2
	python3 -m tools.resource_lab.qualify --spec qualification/resource-lab-v2.json --root build/test-results/resource-lab-v2 --output build/test-results/resource-lab-v2/report.json

resource-lab-v2-quick:
	@test -n "$(EXECUTION_DOMAIN)" || (echo "Set EXECUTION_DOMAIN=<path>" >&2; exit 2)
	python3 -m tools.resource_lab.runner --quick --spec qualification/resource-lab-v2.json --execution-domain "$(EXECUTION_DOMAIN)" --output build/test-results/resource-lab-v2-quick
	python3 -m tools.resource_lab.preflight --spec qualification/resource-lab-v2.json --root build/test-results/resource-lab-v2-quick --output build/test-results/resource-lab-v2-quick/preflight-report.json

resource-lab-v2-preflight: resource-lab-v2-quick

resource-placement-tests:
	python3 tests/adapters/test_linux_affinity.py
	python3 tests/controller/test_resource_control.py
	python3 tests/controller/test_runtime_profiles.py

resource-profile-catalog-tests:
	python3 tests/controller/test_resource_profile_catalog.py

runtime-profile-tests:
	python3 tests/controller/test_runtime_profiles.py

orchestration-contract-tests:
	python3 tests/controller/test_resource_profiles.py
	python3 tests/controller/test_game_environment.py
	python3 tests/controller/test_work_grant.py
	python3 tests/controller/test_orchestration_evidence.py
	python3 tests/controller/test_orchestration_integrity.py
	python3 tests/controller/test_orchestrated_authority.py
	python3 tests/controller/test_resource_profile_catalog.py
	python3 tests/controller/test_runtime_profiles.py
	python3 tests/adapters/test_linux_affinity.py
	python3 tests/controller/test_resource_control.py

orchestration-host-tests:
	python3 tests/adapters/test_linux_host.py
	python3 tests/controller/test_host_capabilities.py
	python3 tests/controller/test_host_pressure.py
	python3 tests/controller/test_runtime_substrate.py
	python3 tests/test_hardware_probe_j2.py
	python3 tests/test_qualification_impact.py

controller-tests:
	python3 tests/adapters/test_linux_host.py
	python3 tests/controller/test_host_capabilities.py
	python3 tests/controller/test_host_pressure.py
	python3 tests/controller/test_runtime_substrate.py
	python3 tests/test_hardware_probe_j2.py
	python3 tests/test_qualification_impact.py
	python3 tests/controller/test_resource_profiles.py
	python3 tests/controller/test_game_environment.py
	python3 tests/controller/test_work_grant.py
	python3 tests/controller/test_orchestration_evidence.py
	python3 tests/controller/test_orchestration_integrity.py
	python3 tests/controller/test_orchestrated_authority.py
	python3 tests/controller/test_resource_profile_catalog.py
	python3 tests/controller/test_runtime_profiles.py
	python3 tests/adapters/test_linux_affinity.py
	python3 tests/controller/test_resource_control.py
	python3 tests/controller/test_online_time.py
	python3 tests/controller/test_online_deadlines.py
	python3 tests/adapters/test_uci_process.py
	python3 tests/adapters/test_linux_proc_resource.py
	python3 tests/adapters/test_crossfeed_adapters.py
	python3 tests/controller/test_resource_measurement.py
	python3 tests/controller/test_runtime.py
	python3 tests/controller/test_uci_frontend.py
	python3 tests/controller/test_shard_ledger.py
	python3 tests/controller/test_prefix_shards.py
	python3 tests/controller/test_shadow_runtime.py
	python3 tests/controller/test_replay.py
	python3 tests/controller/test_residuals.py
	python3 tests/controller/test_budget_routing.py
	python3 tests/controller/test_verification.py
	python3 tests/controller/test_verification_analysis.py
	python3 tests/controller/test_refinement.py
	python3 tests/controller/test_refinement_policy.py
	python3 tests/controller/test_active_specialist_budget.py
	python3 tests/controller/test_crossfeed.py
	python3 tests/controller/test_regimes.py
	python3 tests/controller/test_regime_calibration.py
	python3 tests/controller/test_decision.py
	python3 tests/controller/test_hybrid_authority.py
	python3 tests/controller/test_counterfactual.py
	python3 tests/controller/test_value_of_compute.py
	python3 tests/controller/test_decision_calibration.py
	python3 tests/controller/test_staged_verification.py
	python3 tests/controller/test_staged_value_of_compute.py
	python3 tests/controller/test_staged_decision_calibration.py
	python3 tests/controller/test_unified_value_router.py
	python3 tests/controller/test_strength_profile.py
	python3 tests/controller/test_online_profile.py
	python3 tests/controller/test_engine_opt_runtime.py

online-profile-tests:
	python3 tests/controller/test_online_profile.py
	python3 tests/adapters/test_uci_process.py

build-online-cpu-reference:
	bash scripts/build-online-cpu-reference.sh

online-profile-contract:
	python3 scripts/qualify-online-profile.py --mode reference

shard-ledger-tests:
	python3 tests/controller/test_shard_ledger.py

prefix-shard-tests:
	python3 tests/controller/test_prefix_shards.py

refinement-tests:
	python3 tests/controller/test_refinement.py

refinement-policy-tests:
	python3 tests/controller/test_refinement_policy.py

active-specialist-tests:
	python3 tests/controller/test_active_specialist_budget.py

crossfeed-tests:
	python3 tests/controller/test_crossfeed.py

decision-tests:
	python3 tests/controller/test_decision.py

hybrid-authority-tests:
	python3 tests/controller/test_hybrid_authority.py

counterfactual-tests:
	python3 tests/controller/test_counterfactual.py

value-of-compute-tests:
	python3 tests/controller/test_value_of_compute.py

decision-calibration-tests:
	python3 tests/controller/test_decision_calibration.py

staged-verification-tests:
	python3 tests/controller/test_staged_verification.py

staged-value-of-compute-tests:
	python3 tests/controller/test_staged_value_of_compute.py

staged-decision-calibration-tests:
	python3 tests/controller/test_staged_decision_calibration.py

unified-value-router-tests:
	python3 tests/controller/test_unified_value_router.py

strength-profile-tests:
	python3 tests/controller/test_strength_profile.py

resource-measurement-tests:
	python3 tests/adapters/test_linux_proc_resource.py
	python3 tests/controller/test_resource_measurement.py

resource-accounting-contract:
	python3 scripts/resource-accounting-contract.py

shadow-tests:
	python3 tests/controller/test_shadow_runtime.py

replay-tests:
	python3 tests/controller/test_replay.py

residual-tests:
	python3 tests/controller/test_residuals.py

routing-tests:
	python3 tests/controller/test_budget_routing.py

verification-tests:
	python3 tests/controller/test_verification.py

verification-analysis-tests:
	python3 tests/controller/test_verification_analysis.py

shard-ledger-contract:
	python3 scripts/shard-ledger-contract.py

prefix-shard-contract:
	python3 scripts/prefix-shard-contract.py

refinement-execution-contract:
	python3 scripts/refinement-execution-contract.py

recursive-refinement-contract:
	python3 scripts/recursive-refinement-contract.py

active-specialist-contract:
	python3 scripts/active-specialist-contract.py

crossfeed-contract:
	python3 scripts/crossfeed-contract.py

counterfactual-decision-contract:
	python3 scripts/counterfactual-decision-contract.py

active-hybrid-decision-contract:
	python3 scripts/active-hybrid-decision-contract.py

value-of-compute-contract:
	python3 scripts/value-of-compute-contract.py

staged-verify-contract:
	python3 scripts/staged-verify-contract.py

unified-value-router-contract:
	python3 scripts/unified-value-router-contract.py

lc0-strength-contract:
	python3 scripts/lc0-strength-profile-contract.py

lc0-defect-telemetry-contract:
	python3 scripts/lc0-defect-telemetry-contract.py

hybrid-shell-contract:
	python3 scripts/hybrid-shell-contract.py

shadow-execution-contract:
	python3 scripts/shadow-execution-contract.py

verification-execution-contract:
	python3 scripts/verification-execution-contract.py

verification-analysis-contract:
	python3 scripts/verification-analysis-contract.py

active-routing-contract:
	python3 scripts/active-routing-contract.py

# Research data collection. Shadow mode deliberately overspends compute and
# establishes no strength claim; these targets are not gates.
shadow-evidence-sweep:
	python3 scripts/shadow-evidence-sweep.py

verification-evidence-sweep:
	python3 scripts/verification-evidence-sweep.py

refinement-evidence-sweep:
	python3 scripts/refinement-evidence-sweep.py

counterfactual-decision-sweep:
	python3 scripts/counterfactual-decision-sweep.py

value-of-compute-sweep:
	python3 scripts/value-of-compute-sweep.py

staged-value-of-compute-sweep:
	@test -n "$(RUNS)" || (echo "Usage: make staged-value-of-compute-sweep RUNS='<replay-dir> [...]'" >&2; exit 2)
	python3 scripts/staged-value-of-compute-sweep.py $(RUNS)

regime-sweep:
	@test -n "$(RUNS)" || (echo "Usage: make regime-sweep RUNS='<replay-dir> [...]'" >&2; exit 2)
	python3 scripts/regime-sweep.py $(RUNS)

decision-calibration:
	@test -n "$(DATASET)" || (echo "Usage: make decision-calibration DATASET=<dataset.json>" >&2; exit 2)
	python3 scripts/decision-calibration.py "$(DATASET)"

staged-decision-calibration:
	@test -n "$(DATASET)" || (echo "Usage: make staged-decision-calibration DATASET=<dataset.json>" >&2; exit 2)
	python3 scripts/staged-decision-calibration.py "$(DATASET)"

verification-analysis:
	python3 scripts/verification-analysis.py

residual-calibration:
	python3 scripts/residual-calibration.py

fetch-lc0-strength:
	python3 scripts/fetch-lc0-network.py

build-lc0-strength:
	bash scripts/build-lc0-strength.sh

run-allfather:
	python3 -m controller --config config/allfather.validation.json

run-allfather-shadow:
	python3 -m controller --config config/allfather.shadow.validation.json

run-allfather-verify:
	python3 -m controller --config config/allfather.verify.validation.json

run-allfather-refine:
	python3 -m controller --config config/allfather.refine.validation.json

run-allfather-recursive-refine:
	python3 -m controller --config config/allfather.recursive-refine.validation.json

run-allfather-crossfeed:
	python3 -m controller --config config/allfather.crossfeed.validation.json

run-allfather-counterfactual:
	python3 -m controller --config config/allfather.counterfactual.validation.json

run-allfather-hybrid:
	python3 -m controller --config config/allfather.hybrid.validation.json

run-allfather-value:
	python3 -m controller --config config/allfather.value.validation.json

run-allfather-staged-verify:
	python3 -m controller --config config/allfather.staged-verify.validation.json

run-allfather-unified-value:
	python3 -m controller --config config/allfather.unified-value.validation.json

run-allfather-strength:
	python3 -m controller --config config/allfather.strength.validation.json

run-allfather-active-specialist:
	python3 -m controller --config config/allfather.active.specialist.validation.json

online-time-tests:
	python3 tests/controller/test_online_time.py
	python3 tests/controller/test_online_deadlines.py

online-clock-contract:
	python3 scripts/online-clock-contract.py

run-allfather-online-clock:
	python3 -m controller --config config/allfather.online-clock.validation.json

run-allfather-online-cpu-reference:
	python3 -m controller --config config/allfather.online.cpu-reference.json

online-hybrid-tests:
	python3 tests/controller/test_online_hybrid_authority.py

online-hybrid-contract:
	python3 scripts/qualify-online-hybrid-authority.py

run-allfather-online-hybrid:
	python3 -m controller --config config/allfather.online-hybrid.validation.json


# LOCAL-1 convenience wrappers. The qualification implementation remains
# isolated in Makefile.local-game so production build targets do not inherit
# test-only dependencies.
local-full-game-tests:
	$(MAKE) -f Makefile.local-game test

build-fastchess:
	$(MAKE) -f Makefile.local-game build-fastchess

local-full-game-qualification:
	$(MAKE) -f Makefile.local-game qualification

local-full-game-soak:
	$(MAKE) -f Makefile.local-game soak

local-full-game-v2:
	$(MAKE) -f Makefile.local-game qualification-v2
