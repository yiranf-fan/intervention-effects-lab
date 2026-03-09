from collections import OrderedDict
import os
import time
from typing import Any, Dict, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from shared.schemas import MetricRequest
from pydantic import BaseModel
from experimentplatform.analytics.metrics import compute_ab_metric, list_experiments, required_sample_size
from experimentplatform.analytics.metrics import MetricsError
from .logging import get_logger, log_json

logger = get_logger()
app = FastAPI(title="Experimentation Metrics Service")

CACHE_ENABLED = os.getenv("EXPERIMENT_CACHE_ENABLED", "true").lower() == "true"
CACHE_MAX_ITEMS = int(os.getenv("EXPERIMENT_CACHE_MAX_ITEMS", "64"))
_METRICS_CACHE: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()


def _cache_key(request: MetricRequest) -> Tuple[Any, ...]:
    return (
        request.experiment_id,
        request.metric,
        request.start_time.isoformat() if request.start_time else None,
        request.end_time.isoformat() if request.end_time else None,
        request.use_cuped,
    )


def _cache_get(key: Tuple[Any, ...]) -> Dict[str, Any] | None:
    if not CACHE_ENABLED:
        return None
    if key not in _METRICS_CACHE:
        return None
    _METRICS_CACHE.move_to_end(key)
    return _METRICS_CACHE[key]


def _cache_set(key: Tuple[Any, ...], value: Dict[str, Any]) -> None:
    if not CACHE_ENABLED:
        return
    _METRICS_CACHE[key] = value
    _METRICS_CACHE.move_to_end(key)
    while len(_METRICS_CACHE) > CACHE_MAX_ITEMS:
        _METRICS_CACHE.popitem(last=False)


@app.get("/health")
def health():
    log_json(logger, "info", "health_check", route="/health", status=200)
    return {"status": "ok"}


@app.get("/experiments")
def experiments():
    start_time = time.time()
    try:
        items = list_experiments()
        latency_ms = round((time.time() - start_time) * 1000)
        log_json(
            logger,
            "info",
            "experiments_list_ok",
            route="/experiments",
            status=200,
            count=len(items),
            latency_ms=latency_ms,
        )
        return {"experiments": items}
    except Exception as e:
        log_json(
            logger,
            "error",
            "experiments_list_unexpected",
            route="/experiments",
            status=500,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Internal server error")

@app.exception_handler(MetricsError)
async def metrics_error_handler(request: Request, exc: MetricsError):
    log_json(
        logger,
        "warning",
        "metrics_error",
        route=request.url.path,
        status=400,
        error=str(exc),
    )
    return JSONResponse(status_code=400, content={"detail": str(exc)})

@app.post("/compute_metrics")
def compute_metrics(request: MetricRequest):
    start_time = time.time()
    cache_key = _cache_key(request)
    try:
        cached = _cache_get(cache_key)
        cache_hit = cached is not None

        if cache_hit:
            result = cached
        else:
            result = compute_ab_metric(
                experiment_id=request.experiment_id,
                metric=request.metric,
                start_time=request.start_time,
                end_time=request.end_time,
                use_cuped=request.use_cuped,
            )
            _cache_set(cache_key, result)

        latency_ms = round((time.time() - start_time) * 1000)
        log_json(
            logger,
            "info",
            "compute_metrics_ok",
            route="/compute_metrics",
            status=200,
            experiment_id=request.experiment_id,
            metric=request.metric,
            use_cuped=request.use_cuped,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
        )
        return result
    except MetricsError as e:
        latency_ms = round((time.time() - start_time) * 1000)
        log_json(
            logger,
            "warning",
            "metrics_error",
            route="/compute_metrics",
            status=400,
            latency_ms=latency_ms,
            error=str(e),
        )
        raise e
    except Exception as e:
        log_json(
            logger,
            "error",
            "compute_metrics_unexpected",
            route="/compute_metrics",
            status=500,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Internal server error")

class PowerRequest(BaseModel):
    baseline_rate: float
    target_effect: float
    alpha: float = 0.05
    power: float = 0.8

@app.post("/power")
def power(req: PowerRequest):
    n = required_sample_size(
        baseline_rate=req.baseline_rate,
        target_effect=req.target_effect,
        alpha=req.alpha,
        power=req.power,
    )
    return {"required_sample_size": n}