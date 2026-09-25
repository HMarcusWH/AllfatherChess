.PHONY: online-time-tests online-clock-contract run-allfather-online-clock unified-value-router-tests unified-value-router-contract run-allfather-unified-value staged-verification-tests staged-value-of-compute-tests staged-decision-calibration-tests staged-verify-contract staged-value-of-compute-sweep staged-decision-calibration run-allfather-staged-verify vendor verify-vendor build-baselines smoke-baselines golden-baselines record-golden-baselines telemetry-contract telemetry-adapters telemetry-adapter-integration crossfeed-adapter-tests crossfeed-adapter-contract regime-tests regime-calibration-tests regime-contract controller-tests resource-measurement-tests resource-accounting-contract shard-ledger-tests prefix-shard-tests refinement-tests refinement-policy-tests active-specialist-tests crossfeed-tests decision-tests hybrid-authority-tests counterfactual-tests value-of-compute-tests decision-calibration-tests strength-profile-tests shard-ledger-contract prefix-shard-contract refinement-execution-contract recursive-refinement-contract active-specialist-contract crossfeed-contract counterfactual-decision-contract active-hybrid-decision-contract value-of-compute-contract lc0-strength-contract lc0-defect-telemetry-contract hybrid-shell-contract shadow-tests replay-tests residual-tests routing-tests verification-tests verification-analysis-tests shadow-execution-contract verification-execution-contract verification-analysis-contract active-routing-contract shadow-evidence-sweep verification-evidence-sweep refinement-evidence-sweep counterfactual-decision-sweep value-of-compute-sweep regime-sweep decision-calibration residual-calibration verification-analysis fetch-lc0-strength build-lc0-strength run-allfather run-allfather-shadow run-allfather-verify run-allfather-refine run-allfather-recursive-refine run-allfather-crossfeed run-allfather-counterfactual run-allfather-hybrid run-allfather-value run-allfather-strength run-allfather-active-specialist

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

controller-tests:
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
