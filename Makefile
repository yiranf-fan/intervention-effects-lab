.PHONY: test-pipeline lint test lint-fix clean-data docker-config docker-build docker-up docker-logs docker-down docker-seed-hillstrom docker-seed-all-domains docker-test docker-smoke docker-verify

test-pipeline:
	rm -rf data/events.duckdb
	python -m experimentplatform.analytics.ingest_events
	pytest -v
	python -c "from experimentplatform.analytics.metrics import *; print(compute_ab_metric('exp_banner', 'conversion_rate'))"

lint:
	ruff check .

lint-fix:
	ruff check . --fix

test:
	pytest -v

clean-data:
	rm -rf data/events.duckdb

docker-config:
	docker compose -f infra/docker-compose.yml config

docker-build:
	docker compose -f infra/docker-compose.yml build

docker-up:
	docker compose -f infra/docker-compose.yml up -d

docker-logs:
	docker compose -f infra/docker-compose.yml logs -f api dashboard

docker-down:
	docker compose -f infra/docker-compose.yml down

docker-seed-hillstrom:
	docker compose -f infra/docker-compose.yml exec api python -m experimentplatform.analytics.ingest_events --hillstrom

docker-seed-all-domains:
	docker compose -f infra/docker-compose.yml exec -T api python -m experimentplatform.analytics.ingest_events --all-domains

docker-test:
	docker compose -f infra/docker-compose.yml exec -T api python -m pytest -q

docker-smoke:
	curl -sS http://localhost:8000/health && echo
	curl -sS -X POST http://localhost:8000/compute_metrics -H "Content-Type: application/json" -d '{"experiment_id":"exp_email","metric":"conversion_rate","use_cuped":true}' && echo
	curl -sS -X POST http://localhost:8000/compute_metrics -H "Content-Type: application/json" -d '{"experiment_id":"health_exp_reminder_30d","metric":"readmission_30d_rate","segment_by":["region"]}' && echo

docker-verify:
	@LOG_FILE="infra/docker_verify_latest.log"; \
	echo "[verify] writing logs to $$LOG_FILE"; \
	: > $$LOG_FILE; \
	LOG_FILE="$$LOG_FILE" /bin/bash -o pipefail -c ' \
		set -e; \
		cleanup() { docker compose -f infra/docker-compose.yml down; }; \
		trap cleanup EXIT; \
		{ \
			echo "[1/6] compose config"; \
			docker compose -f infra/docker-compose.yml config; \
			echo "[2/6] build images"; \
			docker compose -f infra/docker-compose.yml build; \
			echo "[3/6] bring up services"; \
			docker compose -f infra/docker-compose.yml up -d; \
			echo "[4/6] seed all-domain events"; \
			docker compose -f infra/docker-compose.yml exec -T api python -m experimentplatform.analytics.ingest_events --all-domains; \
			echo "[5/6] smoke tests"; \
			curl -sS http://localhost:8000/health; echo; \
			curl -sS -X POST http://localhost:8000/compute_metrics -H "Content-Type: application/json" -d "{\"experiment_id\":\"exp_email\",\"metric\":\"conversion_rate\",\"use_cuped\":true}"; echo; \
			curl -sS -X POST http://localhost:8000/compute_metrics -H "Content-Type: application/json" -d "{\"experiment_id\":\"health_exp_reminder_30d\",\"metric\":\"readmission_30d_rate\",\"segment_by\":[\"region\"]}"; echo; \
			echo "[6/6] container tests"; \
			docker compose -f infra/docker-compose.yml exec -T api python -m pytest -q; \
			echo "[verify] success"; \
		} 2>&1 | tee -a "$$LOG_FILE" \
	'
