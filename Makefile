.PHONY: test-pipeline lint test lint-fix clean-data

test-pipeline:
	rm -rf data/events.duckdb
	python -m experimentplatform.analytics.ingest_clickstream
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
