.PHONY: vendor verify-vendor build-baselines smoke-baselines golden-baselines record-golden-baselines telemetry-contract telemetry-adapters telemetry-adapter-integration controller-tests shard-ledger-tests shard-ledger-contract hybrid-shell-contract shadow-tests replay-tests residual-tests routing-tests shadow-execution-contract active-routing-contract shadow-evidence-sweep residual-calibration run-allfather run-allfather-shadow

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
	python3 tests/controller/test_shadow_runtime.py
	python3 tests/controller/test_replay.py
	python3 tests/controller/test_residuals.py
	python3 tests/controller/test_budget_routing.py

shard-ledger-tests:
	python3 tests/controller/test_shard_ledger.py

shadow-tests:
	python3 tests/controller/test_shadow_runtime.py

replay-tests:
	python3 tests/controller/test_replay.py

residual-tests:
	python3 tests/controller/test_residuals.py

routing-tests:
	python3 tests/controller/test_budget_routing.py

shard-ledger-contract:
	python3 scripts/shard-ledger-contract.py

hybrid-shell-contract:
	python3 scripts/hybrid-shell-contract.py

shadow-execution-contract:
	python3 scripts/shadow-execution-contract.py

active-routing-contract:
	python3 scripts/active-routing-contract.py

# Research data collection. Shadow mode deliberately overspends compute and
# establishes no strength claim; these targets are not gates.
shadow-evidence-sweep:
	python3 scripts/shadow-evidence-sweep.py

residual-calibration:
	python3 scripts/residual-calibration.py

run-allfather:
	python3 -m controller --config config/allfather.validation.json

run-allfather-shadow:
	python3 -m controller --config config/allfather.shadow.validation.json
