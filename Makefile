.PHONY: vendor verify-vendor build-baselines smoke-baselines golden-baselines record-golden-baselines telemetry-contract telemetry-adapters telemetry-adapter-integration controller-tests hybrid-shell-contract run-allfather

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

hybrid-shell-contract:
	python3 scripts/hybrid-shell-contract.py

run-allfather:
	python3 -m controller --config config/allfather.validation.json
