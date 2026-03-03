from fastapi.testclient import TestClient
from experimentplatform.api.main import app

client = TestClient(app)

def test_computemetrics_happy_path():
    payload = {
        "experiment_id": "exp_email",
        "metric": "conversion_rate"
    }
    response = client.post("/computemetrics", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    # Week 2 assertions
    assert data["experiment_id"] == "exp_email"
    assert "control" in data
    assert "treatment" in data
    assert "diff_pct" in data
    assert data["diff_pct"] > 0
    assert isinstance(data["control"], dict)
    assert "n" in data["control"]
    assert data["control"]["n"] > 10000

def test_computemetrics_revenue():
    payload = {
        "experiment_id": "exp_email",
        "metric": "revenue_per_user"
    }
    response = client.post("/computemetrics", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "diff_pct" in data
    assert data["diff_pct"] > 0 

def test_bad_metric():
    payload = {"experiment_id": "exp_email", "metric": "fake_metric"}
    response = client.post("/computemetrics", json=payload)
    assert response.status_code == 400
