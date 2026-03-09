from fastapi.testclient import TestClient
from experimentplatform.api.main import app

client = TestClient(app)


def test_compute_metrics_happy_path():
    payload = {
        "experiment_id": "exp_email",
        "metric": "conversion_rate"
    }
    response = client.post("/compute_metrics", json=payload)
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
    assert "data_as_of" in data
    assert "cuped" in data
    assert "standard_error" in data
    assert data["standard_error"] >= 0
    assert "ci_95" in data
    assert "low" in data["ci_95"]
    assert "high" in data["ci_95"]
    assert data["ci_95"]["low"] <= data["ci_95"]["high"]


def test_compute_metrics_revenue():
    payload = {
        "experiment_id": "exp_email",
        "metric": "revenue_per_user"
    }
    response = client.post("/compute_metrics", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "diff_pct" in data
    assert data["diff_pct"] > 0 


def test_compute_metrics_cuped_flag():
    payload = {
        "experiment_id": "exp_email",
        "metric": "conversion_rate",
        "use_cuped": True,
    }
    response = client.post("/compute_metrics", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["cuped"]["requested"] is True
    assert "data_as_of" in data

def test_bad_metric():
    payload = {"experiment_id": "exp_email", "metric": "fake_metric"}
    response = client.post("/compute_metrics", json=payload)
    assert response.status_code == 400
