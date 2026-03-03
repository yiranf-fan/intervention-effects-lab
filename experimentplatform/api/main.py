from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from shared.schemas import MetricRequest
from pydantic import BaseModel
from experimentplatform.analytics.metrics import compute_ab_metric, required_sample_size
from experimentplatform.analytics.metrics import MetricsError
from .logging import get_logger, log_json
import time

logger = get_logger()
app = FastAPI(title="Experimentation Metrics Service")


@app.get("/health")
def health():
    log_json(logger, "info", "health_check", route="/health", status=200)
    return {"status": "ok"}

@app.exception_handler(MetricsError)
async def metrics_error_handler(request: Request, exc: MetricsError):
    log_json(logger, "warning", "metrics_error", 
        route=str(request.url), status=400, error=str(exc))
    return JSONResponse(status_code=400, content={"detail": str(exc)})

@app.post("/computemetrics")
def computemetrics(request: MetricRequest):
    start_time = time.time()
    try:
        result = compute_ab_metric(
            experiment_id=request.experiment_id,
            metric=request.metric,
        )
        latency_ms = round((time.time() - start_time) * 1000)
        log_json(
            logger,
            "info",
            "computemetrics_ok",
            route="/computemetrics",
            status=200,
            experiment_id=request.experiment_id,
            metric=request.metric,
            latency_ms=latency_ms,
        )
        return result
    except MetricsError as e:
        latency_ms = round((time.time() - start_time) * 1000)
        log_json(
            logger,
            "warning",
            "metrics_error",
            route="/computemetrics",
            status=400,
            latency_ms=round((time.time() - start_time) * 1000),
            error=str(e),
        )
        raise e
    except Exception as e:
        log_json(
            logger,
            "error",
            "computemetrics_unexpected",
            route="/computemetrics",
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