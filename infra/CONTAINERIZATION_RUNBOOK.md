# Containerization Runbook

This runbook captures a practical local-to-production-like workflow for the experimentation platform.


## Prerequisites

1. Docker Desktop is installed and running.
2. Verify tools:
   - `docker --version`
   - `docker compose version`

## Step-by-step commands

### 1) Validate compose definition

```bash
make docker-config
```

### 2) Build images

```bash
make docker-build
```

### 3) Start services (API + Dashboard)

```bash
make docker-up
```

### 4) Tail logs

```bash
make docker-logs
```

### 5) Seed experiment data into DuckDB

```bash
make docker-seed-hillstrom
```

### 6) Smoke test endpoints

```bash
make docker-smoke
```

### 7) Run tests inside API container

```bash
make docker-test
```

(`docker-test` runs `python -m pytest -q` to ensure execution from the container Python module context.)

### 8) Stop services

```bash
make docker-down
```

## Service endpoints

- API: `http://localhost:8000`
  - `GET /health`
  - `POST /compute_metrics`
  - `POST /power`
- Dashboard: `http://localhost:8501`

