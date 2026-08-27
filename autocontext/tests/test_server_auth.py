from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from autocontext.server.auth import (
    SERVER_AUTH_TOKEN_ENV,
    assert_secure_server_bind,
    encode_server_auth_subprotocol,
    request_is_authorized,
    resolve_server_auth_token,
    tokenless_client_is_local,
)
from autocontext.server.resource_limits import MAX_INTERACTIVE_FRAME_BYTES, MAX_PENDING_WEBSOCKET_MESSAGES

TOKEN = "0123456789abcdef0123456789abcdef"


def _configure_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.sqlite3"))
    monkeypatch.setenv("AUTOCONTEXT_RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_SKILLS_ROOT", str(tmp_path / "skills"))


def test_non_loopback_bind_requires_strong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        assert_secure_server_bind("0.0.0.0")
    assert_secure_server_bind("127.0.0.1")

    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, "short")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        resolve_server_auth_token()

    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    assert_secure_server_bind("0.0.0.0")


def test_cli_applies_bind_guard_before_starting_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.cli import _run_http_serve

    calls: list[tuple[str, int, dict[str, int]]] = []
    monkeypatch.setattr(
        "autocontext.cli.uvicorn.run",
        lambda _app, *, host, port, reload, **limits: calls.append((host, port, limits)),
    )
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        _run_http_serve("0.0.0.0", 8000)
    assert calls == []

    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    _run_http_serve("0.0.0.0", 8000)
    assert calls == [
        (
            "0.0.0.0",
            8000,
            {"ws_max_size": MAX_INTERACTIVE_FRAME_BYTES, "ws_max_queue": MAX_PENDING_WEBSOCKET_MESSAGES},
        )
    ]


def test_bearer_token_is_exact() -> None:
    assert request_is_authorized(TOKEN, f"Bearer {TOKEN}")
    assert not request_is_authorized(TOKEN, "Bearer wrong")
    assert not request_is_authorized(TOKEN, None)


def test_tokenless_client_peer_must_be_loopback() -> None:
    assert tokenless_client_is_local("127.0.0.1")
    assert tokenless_client_is_local("::1")
    assert not tokenless_client_is_local("203.0.113.8")


def test_direct_asgi_launch_rejects_tokenless_non_loopback_peers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    from autocontext.server.app import create_app

    with TestClient(create_app(), client=("203.0.113.8", 51_000)) as client:
        assert client.get("/health").status_code == 200
        denied = client.get("/api/runs")
        assert denied.status_code == 403
        assert denied.json() == {"detail": "Token required for non-loopback clients"}
        with pytest.raises(WebSocketDisconnect) as websocket_denied:
            with client.websocket_connect("/ws/events"):
                pass
        assert websocket_denied.value.code == 4403


def test_configured_token_protects_http_and_websockets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    from autocontext.server.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        denied = client.get("/api/runs")
        assert denied.status_code == 401
        assert denied.json() == {"detail": "Unauthorized"}
        allowed = client.get("/api/runs", headers={"Authorization": f"Bearer {TOKEN}"})
        assert allowed.status_code == 200

        with pytest.raises(WebSocketDisconnect) as unauthenticated:
            with client.websocket_connect("/ws/events"):
                pass
        assert unauthenticated.value.code == 4401

        with pytest.raises(WebSocketDisconnect) as query_token:
            with client.websocket_connect(f"/ws/events?token={TOKEN}"):
                pass
        assert query_token.value.code == 4401

        subprotocol = encode_server_auth_subprotocol(TOKEN)
        with client.websocket_connect(
            "/ws/interactive",
            subprotocols=[subprotocol],
        ) as websocket:
            assert websocket.accepted_subprotocol == subprotocol
            assert websocket.receive_json()["type"] == "hello"

        with client.websocket_connect(
            "/ws/interactive",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "hello"


def test_websocket_rejects_untrusted_browser_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    from autocontext.server.app import create_app

    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/ws/events",
                headers={"Origin": "https://evil.example"},
            ):
                pass
        assert rejected.value.code == 4403


def test_http_mutation_rejects_untrusted_browser_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    from autocontext.server.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/openclaw/evaluate",
            headers={"Origin": "https://evil.example"},
            json={
                "scenario_name": "grid_ctf",
                "strategy": {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
            },
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "Forbidden origin"}
