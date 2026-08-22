PYTHON ?= runtime/service-python/.venv/bin/python
GITLEAKS ?= .tools/gitleaks

.PHONY: prepare-public-artifacts verify-public-release verify-public-dependencies verify-public-backend verify-public-frontend verify-public-reproducibility
.NOTPARALLEL: verify-public-release

verify-public-release: prepare-public-artifacts verify-public-dependencies verify-public-backend verify-public-frontend verify-public-reproducibility

verify-public-dependencies:
	npm --prefix runtime/tiangong-node audit --audit-level=low
	pnpm --dir apps/web audit --audit-level low

verify-public-reproducibility:
	"$(PYTHON)" scripts/verify_clean_public_release.py \
		--staging . \
		--manifest public-release-manifest.json \
		--gitleaks "$(GITLEAKS)"

prepare-public-artifacts:
	@test -x "$(PYTHON)" || { printf '%s\n' 'Run scripts/bootstrap_service_runtime.py first.'; exit 2; }
	pnpm --dir apps/web build
	"$(PYTHON)" -m scripts.build_d18_service_integration outputs/d18-service/integration >/dev/null

verify-public-backend:
	@test -x "$(PYTHON)" || { printf '%s\n' 'Run scripts/bootstrap_service_runtime.py first.'; exit 2; }
	"$(PYTHON)" -m unittest \
		tests.test_d18_architecture_contract \
		tests.test_assurance_boundary \
		tests.test_capability_gateway \
		tests.test_context_assembler \
		tests.test_material_source_contracts \
		tests.test_model_gateway \
		tests.test_model_runtime_policy \
		tests.test_project_record_port \
		tests.test_service_release_manifest

verify-public-frontend:
	cd apps/web && pnpm test
	cd apps/web && pnpm contract:check
	cd apps/web && pnpm lint
