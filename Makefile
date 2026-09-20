.PHONY: vendor verify-vendor build-baselines smoke-baselines

vendor:
	./scripts/vendor-engines.sh

verify-vendor:
	./scripts/verify-vendor.sh

build-baselines:
	./scripts/build-baselines.sh

smoke-baselines:
	./scripts/smoke-baselines.sh
