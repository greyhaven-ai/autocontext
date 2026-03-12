from fastapi.testclient import TestClient

from autocontext.server.app import app


def test_server_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
