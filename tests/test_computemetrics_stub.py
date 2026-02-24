from fastapi.testclient import TestClient

from experimentplatform.api.main import app

client = TestClient(app)


def test_compute_metrics_stub():
    payload = {"experiment_id": "exp_123", "metric": "conversion_rate"}
    resp = client.post("/computemetrics", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["experiment_id"] == "exp_123"
    assert data["metric"] == "conversion_rate"
    assert "groups" in data
