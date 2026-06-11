"""CORS for local GUI clients (cowork desktop webview, browser dev servers)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocontext.server.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    return TestClient(create_app())


def test_get_allows_local_gui_origin(client: TestClient) -> None:
    response = client.get("/api/runs", headers={"Origin": "http://localhost:1420"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:1420"


def test_put_preflight_allows_tauri_origin(client: TestClient) -> None:
    response = client.options(
        "/api/knowledge/grid_ctf",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_unknown_origin_gets_no_cors_headers(client: TestClient) -> None:
    response = client.get("/api/runs", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_origins_overridable_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    monkeypatch.setenv("AUTOCONTEXT_CORS_ORIGINS", "http://localhost:9999")
    client = TestClient(create_app())
    allowed = client.get("/api/runs", headers={"Origin": "http://localhost:9999"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:9999"
    default_gone = client.get("/api/runs", headers={"Origin": "http://localhost:1420"})
    assert "access-control-allow-origin" not in default_gone.headers
