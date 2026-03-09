from fastapi.testclient import TestClient

from experimentplatform.api.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"


def test_experiments_list():
    resp = client.get("/experiments")
    assert resp.status_code == 200
    data = resp.json()
    assert "experiments" in data
    assert isinstance(data["experiments"], list)
    assert len(data["experiments"]) > 0
