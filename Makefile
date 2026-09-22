.PHONY: vendor verify-vendor build-baselines smoke-baselines golden-baselines record-golden-baselines telemetry-contract telemetry-adapters telemetry-adapter-integration controller-tests shard-ledger-tests prefix-shard-tests refinement-tests active-specialist-tests crossfeed-tests decision-tests counterfactual-tests value-of-compute-tests decision-calibration-tests shard-ledger-contract prefix-shard-contract refinement-execution-contract active-specialist-contract crossfeed-contract counterfactual-decision-contract value-of-compute-contract hybrid-shell-contract shadow-tests replay-tests residual-tests routing-tests verification-tests verification-analysis-tests shadow-execution-contract verification-execution-contract verification-analysis-contract active-routing-contract shadow-evidence-sweep verification-evidence-sweep refinement-evidence-sweep counterfactual-decision-sweep value-of-compute-sweep decision-calibration residual-calibration verification-analysis run-allfather run-allfather-shadow run-allfather-verify run-allfather-refine run-allfather-crossfeed run-allfather-counterfactual run-allfather-value run-allfather-active-specialist

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

controller-tests:
	python3 tests/adapters/test_uci_process.py
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
	python3 tests/controller/test_active_specialist_budget.py
	python3 tests/controller/test_crossfeed.py
	python3 tests/controller/test_decision.py
	python3 tests/controller/test_counterfactual.py
	python3 tests/controller/test_value_of_compute.py
	python3 tests/controller/test_decision_calibration.py

shard-ledger-tests:
	python3 tests/controller/test_shard_ledger.py

prefix-shard-tests:
	python3 tests/controller/test_prefix_shards.py

refinement-tests:
	python3 tests/controller/test_refinement.py

active-specialist-tests:
	python3 tests/controller/test_active_specialist_budget.py

crossfeed-tests:
	python3 tests/controller/test_crossfeed.py

decision-tests:
	python3 tests/controller/test_decision.py

counterfactual-tests:
	python3 tests/controller/test_counterfactual.py

value-of-compute-tests:
	python3 tests/controller/test_value_of_compute.py

decision-calibration-tests:
	python3 tests/controller/test_decision_calibration.py

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

active-specialist-contract:
	python3 scripts/active-specialist-contract.py

crossfeed-contract:
	python3 scripts/crossfeed-contract.py

counterfactual-decision-contract:
	python3 scripts/counterfactual-decision-contract.py

value-of-compute-contract:
	python3 scripts/value-of-compute-contract.py

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

decision-calibration:
	@echo "Usage: python3 scripts/decision-calibration.py <dataset.json>"

verification-analysis:
	python3 scripts/verification-analysis.py

residual-calibration:
	python3 scripts/residual-calibration.py

run-allfather:
	python3 -m controller --config config/allfather.validation.json

run-allfather-shadow:
	python3 -m controller --config config/allfather.shadow.validation.json

run-allfather-verify:
	python3 -m controller --config config/allfather.verify.validation.json

run-allfather-refine:
	python3 -m controller --config config/allfather.refine.validation.json

run-allfather-crossfeed:
	python3 -m controller --config config/allfather.crossfeed.validation.json

run-allfather-counterfactual:
	python3 -m controller --config config/allfather.counterfactual.validation.json

run-allfather-value:
	python3 -m controller --config config/allfather.value.validation.json

run-allfather-active-specialist:
	python3 -m controller --config config/allfather.active.specialist.validation.json
