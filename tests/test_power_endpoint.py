from fastapi.testclient import TestClient
from experimentplatform.api.main import app


client = TestClient(app)


def test_power_endpoint():
    """Test /power endpoint returns valid sample size."""
    payload = {
        "baseline_rate": 0.1,
        "target_effect": 0.02,
    }
    response = client.post("/power", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "required_sample_size" in data
    assert isinstance(data["required_sample_size"], int)
    assert data["required_sample_size"] > 0
