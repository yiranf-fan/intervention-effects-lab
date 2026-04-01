"""Simple latency benchmark for /compute_metrics with and without API cache.

Usage:
  python scripts/benchmark_compute_metrics.py
"""

from __future__ import annotations

import importlib
import os
import sys
import statistics
import time
from pathlib import Path
from typing import Dict, List

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PAYLOAD: Dict[str, object] = {
    "experiment_id": "exp_email",
    "metric": "conversion_rate",
}


def _run_rounds(cache_enabled: bool, rounds: int = 12) -> Dict[str, float]:
    os.environ["EXPERIMENT_CACHE_ENABLED"] = "true" if cache_enabled else "false"

    import experimentplatform.api.main as api_main  # delayed import for env-based settings

    api_main = importlib.reload(api_main)
    client = TestClient(api_main.app)

    latencies_ms: List[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        resp = client.post("/compute_metrics", json=PAYLOAD)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            raise RuntimeError(f"Benchmark request failed: {resp.status_code} - {resp.text}")
        latencies_ms.append(elapsed_ms)

    # Ignore first request as warmup/cold-start-ish sample.
    measured = latencies_ms[1:] if len(latencies_ms) > 1 else latencies_ms
    return {
        "rounds": float(len(measured)),
        "mean_ms": float(statistics.mean(measured)),
        "median_ms": float(statistics.median(measured)),
        "p95_ms": float(sorted(measured)[max(0, int(0.95 * len(measured)) - 1)]),
    }


def main() -> None:
    without_cache = _run_rounds(cache_enabled=False)
    with_cache = _run_rounds(cache_enabled=True)

    improvement = 0.0
    if without_cache["mean_ms"] > 0:
        improvement = (without_cache["mean_ms"] - with_cache["mean_ms"]) / without_cache["mean_ms"] * 100

    print("/compute_metrics benchmark")
    print(f"without_cache: mean={without_cache['mean_ms']:.2f}ms median={without_cache['median_ms']:.2f}ms p95={without_cache['p95_ms']:.2f}ms")
    print(f"with_cache:    mean={with_cache['mean_ms']:.2f}ms median={with_cache['median_ms']:.2f}ms p95={with_cache['p95_ms']:.2f}ms")
    print(f"mean_latency_improvement_pct={improvement:.2f}")


if __name__ == "__main__":
    main()