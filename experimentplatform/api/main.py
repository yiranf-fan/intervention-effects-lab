from fastapi import FastAPI

from shared.schemas import MetricRequest

app = FastAPI(title="Experimentation Metrics Service")


@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/computemetrics")
def compute_metrics(request: MetricRequest):
    # Stubbed response for Week 1
    return {
        "experiment_id": request.experiment_id,
        "metric": request.metric,
        "groups": {
            "control": {"value": 0.10, "n": 1000},
            "treatment": {"value": 0.12, "n": 1000},
        },
        "diff": 0.02,
        "notes": "Stubbed metrics for Week 1",
    }
