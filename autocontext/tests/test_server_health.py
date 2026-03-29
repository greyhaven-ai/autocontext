from fastapi.testclient import TestClient

from autocontext.server.app import app


def test_server_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_server_root_endpoint_returns_api_info() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "autocontext"
    assert body["endpoints"]["runs"] == "/api/runs"


def test_dashboard_path_returns_api_info_placeholder() -> None:
    client = TestClient(app)
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "autocontext"
