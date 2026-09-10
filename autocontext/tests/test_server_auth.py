from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.server.auth import (
    ALLOW_TOKENLESS_LOOPBACK_ENV,
    CONTENT_READ,
    CONTROL_ADMIN,
    CONTROL_OPERATE,
    CONTROL_READ,
    HOST_EXECUTE,
    SERVER_AUTH_TOKEN_ENV,
    SERVER_CREDENTIALS_FILE_ENV,
    ControlPlaneAuthenticationError,
    ControlPlaneAuthenticator,
    ControlPlaneAuthorizationError,
    ServerCredential,
    assert_secure_server_bind,
    assert_tokenless_browser_origins_are_local,
    build_control_plane_proof,
    consume_control_plane_authenticator_from_environment,
    encode_server_auth_subprotocol,
    request_is_authorized,
    require_capability,
    required_http_capabilities,
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
    monkeypatch.delenv(SERVER_CREDENTIALS_FILE_ENV, raising=False)
    monkeypatch.delenv(ALLOW_TOKENLESS_LOOPBACK_ENV, raising=False)


def _proof(
    *,
    caps: list[str],
    target: str,
    method: str = "GET",
    origin: str = "",
    issued_at: int | None = None,
    expires_at: int | None = None,
    jti: str | None = None,
) -> str:
    return build_control_plane_proof(
        kid="env",
        secret=TOKEN,
        caps=caps,
        method=method,
        target=target,
        origin=origin,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
    )


def _authenticator(*caps: str) -> ControlPlaneAuthenticator:
    return ControlPlaneAuthenticator(
        [
            ServerCredential(
                kid="operator-1",
                principal="test-operator",
                secret=TOKEN.encode(),
                capabilities=frozenset(caps),
            )
        ]
    )


def _direct_proof(
    *,
    caps: list[str],
    target: str = "/api/runs",
    issued_at: int = 1_000,
    expires_at: int = 1_060,
    jti: str,
) -> str:
    return build_control_plane_proof(
        kid="operator-1",
        secret=TOKEN,
        caps=caps,
        method="GET",
        target=target,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
    )


def test_non_loopback_bind_requires_strong_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(SERVER_CREDENTIALS_FILE_ENV, raising=False)
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        assert_secure_server_bind("0.0.0.0")
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        assert_secure_server_bind("127.0.0.1")
    monkeypatch.setenv(ALLOW_TOKENLESS_LOOPBACK_ENV, "1")
    assert_secure_server_bind("127.0.0.1")
    monkeypatch.delenv(ALLOW_TOKENLESS_LOOPBACK_ENV)

    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, "short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        resolve_server_auth_token()

    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    assert_secure_server_bind("0.0.0.0")


def test_authenticator_consumption_clears_ambient_secrets_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    authenticator = consume_control_plane_authenticator_from_environment()
    assert authenticator.configured
    assert SERVER_AUTH_TOKEN_ENV not in os.environ

    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, "short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        consume_control_plane_authenticator_from_environment()
    assert SERVER_AUTH_TOKEN_ENV not in os.environ


def test_environment_metadata_never_executes_registered_scenario_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.config import AppSettings
    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.server._run_environment import build_run_environment_info

    class _HostInstalledScenario:
        def __init__(self) -> None:
            raise AssertionError("metadata must not instantiate registered scenarios")

    monkeypatch.setitem(SCENARIO_REGISTRY, "host_installed", _HostInstalledScenario)

    environment = build_run_environment_info(AppSettings(agent_provider="deterministic"))

    assert {scenario["name"] for scenario in environment["scenarios"]} >= {
        "grid_ctf",
        "host_installed",
    }


def test_run_manager_clears_ambient_auth_before_resolving_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autocontext.server.run_manager import RunManager

    manager = MagicMock()
    manager.settings.knowledge_root = tmp_path / "knowledge"
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)

    def assert_secret_absent(*_args: object, **_kwargs: object) -> None:
        assert SERVER_AUTH_TOKEN_ENV not in os.environ
        return None

    monkeypatch.setattr(
        "autocontext.server.run_manager.resolve_scenario_class",
        assert_secret_absent,
    )
    with pytest.raises(ValueError, match="Unknown scenario"):
        RunManager.start_run(manager, "untrusted-custom", 1)


def test_external_browser_origins_require_control_plane_credentials() -> None:
    tokenless = ControlPlaneAuthenticator()
    assert_tokenless_browser_origins_are_local(
        tokenless,
        ["http://localhost:1420", "tauri://localhost"],
    )
    with pytest.raises(RuntimeError, match="require control-plane credentials"):
        assert_tokenless_browser_origins_are_local(
            tokenless,
            ["https://operator.example"],
        )
    assert_tokenless_browser_origins_are_local(
        _authenticator(CONTROL_READ),
        ["https://operator.example"],
    )


def test_cli_applies_bind_guard_before_starting_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.cli import _run_http_serve

    calls: list[tuple[str, int, dict[str, int]]] = []
    monkeypatch.setattr(
        "autocontext.cli.uvicorn.run",
        lambda _app, *, host, port, reload, **limits: calls.append((host, port, limits)),
    )
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(SERVER_CREDENTIALS_FILE_ENV, raising=False)
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


def test_raw_bearer_secret_is_never_accepted() -> None:
    assert not request_is_authorized(None, None)
    assert not request_is_authorized(TOKEN, f"Bearer {TOKEN}")
    assert not request_is_authorized(TOKEN, "Bearer wrong")
    assert not request_is_authorized(TOKEN, None)


def test_scoped_proof_is_valid_once_and_binds_request() -> None:
    authenticator = _authenticator(CONTROL_READ)
    proof = _direct_proof(
        caps=[CONTROL_READ],
        jti="00000000000000000000000000000001",
    )
    principal = authenticator.authenticate(
        proof,
        method="GET",
        target="/api/runs",
        origin="",
        now=1_030,
    )
    assert principal.name == "test-operator"
    assert principal.capabilities == {CONTROL_READ}
    assert principal.expires_at == 1_060
    require_capability(principal, CONTROL_READ, now=1_059)
    with pytest.raises(ControlPlaneAuthorizationError, match="expired"):
        require_capability(principal, CONTROL_READ, now=1_060)
    with pytest.raises(ControlPlaneAuthenticationError, match="already been used"):
        authenticator.authenticate(
            proof,
            method="GET",
            target="/api/runs",
            origin="",
            now=1_030,
        )


def test_proof_encoding_matches_cross_runtime_vector() -> None:
    proof = build_control_plane_proof(
        kid="env",
        secret=TOKEN,
        caps=[HOST_EXECUTE, CONTROL_OPERATE],
        method="POST",
        target="/api/runs?limit=5",
        origin="http://localhost:1420",
        issued_at=1_700_000_000,
        expires_at=1_700_000_060,
        jti="00112233445566778899aabbccddeeff",
    )
    assert proof == (
        "actx1.eyJhdWQiOiJhdXRvY29udGV4dC1jb250cm9sLXBsYW5lIiwiY2FwcyI6WyJjb250cm9sOm9wZXJhdGUi"
        "LCJob3N0OmV4ZWN1dGUiXSwiZXhwIjoxNzAwMDAwMDYwLCJpYXQiOjE3MDAwMDAwMDAsImp0aSI6IjAwMTEyMjMz"
        "NDQ1NTY2Nzc4ODk5YWFiYmNjZGRlZWZmIiwia2lkIjoiZW52IiwibWV0aG9kIjoiUE9TVCIsIm9yaWdpbiI6Imh0"
        "dHA6Ly9sb2NhbGhvc3Q6MTQyMCIsInRhcmdldCI6Ii9hcGkvcnVucz9saW1pdD01IiwidiI6MX0.IMuu4_lqAKVm"
        "JeV-sV6YlBeFwGW4j-c7Wl71kMESD8c"
    )


def test_credential_not_after_is_inclusive_for_an_established_principal() -> None:
    authenticator = ControlPlaneAuthenticator(
        [
            ServerCredential(
                kid="operator-1",
                principal="test-operator",
                secret=TOKEN.encode(),
                capabilities=frozenset({CONTROL_READ}),
                not_after=1_030,
            )
        ]
    )
    principal = authenticator.authenticate(
        _direct_proof(
            caps=[CONTROL_READ],
            expires_at=1_060,
            jti="00000000000000000000000000000008",
        ),
        method="GET",
        target="/api/runs",
        origin="",
        now=1_030,
    )
    assert principal.expires_at == 1_031
    require_capability(principal, CONTROL_READ, now=1_030)
    with pytest.raises(ControlPlaneAuthorizationError, match="expired"):
        require_capability(principal, CONTROL_READ, now=1_031)


def test_proof_expiry_path_and_capability_ceiling_are_enforced() -> None:
    authenticator = _authenticator(CONTROL_READ)
    expired = _direct_proof(
        caps=[CONTROL_READ],
        jti="00000000000000000000000000000002",
    )
    with pytest.raises(ControlPlaneAuthenticationError, match="expired"):
        authenticator.authenticate(
            expired,
            method="GET",
            target="/api/runs",
            origin="",
            now=1_066,
        )

    wrong_path = _direct_proof(
        caps=[CONTROL_READ],
        jti="00000000000000000000000000000003",
    )
    with pytest.raises(ControlPlaneAuthenticationError, match="target"):
        authenticator.authenticate(
            wrong_path,
            method="GET",
            target="/api/other",
            origin="",
            now=1_030,
        )

    excessive = _direct_proof(
        caps=[HOST_EXECUTE],
        jti="00000000000000000000000000000004",
    )
    with pytest.raises(ControlPlaneAuthorizationError, match="ceiling"):
        authenticator.authenticate(
            excessive,
            method="GET",
            target="/api/runs",
            origin="",
            now=1_030,
        )


def test_route_capability_must_be_requested_by_proof() -> None:
    authenticator = _authenticator(CONTROL_READ, CONTROL_OPERATE)
    proof = _direct_proof(
        caps=[CONTROL_READ],
        jti="00000000000000000000000000000005",
    )
    principal = authenticator.authenticate(
        proof,
        method="GET",
        target="/api/runs",
        origin="",
        now=1_030,
    )
    with pytest.raises(ControlPlaneAuthorizationError, match=CONTROL_OPERATE):
        require_capability(principal, CONTROL_OPERATE, now=1_030)


def test_admin_implies_control_but_not_content_or_host_execution() -> None:
    authenticator = _authenticator(CONTROL_ADMIN)
    principal = authenticator.authenticate(
        _direct_proof(
            caps=[CONTROL_ADMIN],
            jti="00000000000000000000000000000007",
        ),
        method="GET",
        target="/api/runs",
        origin="",
        now=1_030,
    )
    require_capability(principal, CONTROL_READ, now=1_030)
    require_capability(principal, CONTROL_OPERATE, now=1_030)
    with pytest.raises(ControlPlaneAuthorizationError, match=CONTENT_READ):
        require_capability(principal, CONTENT_READ, now=1_030)
    with pytest.raises(ControlPlaneAuthorizationError, match=HOST_EXECUTE):
        require_capability(principal, HOST_EXECUTE, now=1_030)


def test_http_routes_require_content_and_host_execution_capabilities() -> None:
    assert required_http_capabilities("GET", "/") == (CONTROL_READ,)
    assert required_http_capabilities("GET", "/api/runs?limit=5") == (
        CONTROL_READ,
        CONTENT_READ,
    )
    assert required_http_capabilities(
        "GET",
        "/%61pi/runs",
        routed_path="/api/runs",
    ) == (CONTROL_READ, CONTENT_READ)
    assert required_http_capabilities("POST", "/api/knowledge/solve") == (
        CONTROL_OPERATE,
        CONTENT_READ,
        HOST_EXECUTE,
    )
    assert required_http_capabilities("POST", "/api/cockpit/runs/run-1/consult") == (
        CONTROL_OPERATE,
        CONTENT_READ,
        HOST_EXECUTE,
    )
    assert required_http_capabilities("PUT", "/api/knowledge/grid_ctf") == (
        CONTROL_OPERATE,
        CONTENT_READ,
    )
    for metadata_target in (
        "/api/knowledge/scenarios",
        "/api/knowledge/export/grid_ctf",
        "/api/knowledge/solve/job-1",
        "/api/openclaw/discovery/capabilities",
        "/api/openclaw/discovery/scenario/grid_ctf",
        "/api/openclaw/skill/manifest",
    ):
        assert required_http_capabilities("GET", metadata_target) == (
            CONTROL_READ,
            CONTENT_READ,
            HOST_EXECUTE,
        )
    assert required_http_capabilities(
        "GET",
        "/api/openclaw/discovery/scenario/grid_ctf/artifacts",
    ) == (CONTROL_READ, CONTENT_READ)
    assert required_http_capabilities("POST", "/api/knowledge/search") == (
        CONTROL_OPERATE,
        CONTENT_READ,
        HOST_EXECUTE,
    )
    assert required_http_capabilities("POST", "/api/knowledge/import") == (
        CONTROL_OPERATE,
        CONTENT_READ,
        HOST_EXECUTE,
    )
    assert required_http_capabilities("POST", "/api/hub/packages/from-run/run-1") == (
        CONTROL_OPERATE,
        CONTENT_READ,
        HOST_EXECUTE,
    )
    assert required_http_capabilities("POST", "/api/hub/packages/pkg-1/adopt") == (
        CONTROL_OPERATE,
        CONTENT_READ,
        HOST_EXECUTE,
    )
    for artifact_publish_target in (
        "/api/openclaw/artifacts",
        "/api/openclaw/artifacts/",
    ):
        assert required_http_capabilities("POST", artifact_publish_target) == (
            CONTROL_OPERATE,
            CONTENT_READ,
            HOST_EXECUTE,
        )
    for writing_get_target in (
        "/api/cockpit/writeup/run-1",
        "/api/cockpit/scenarios/grid_ctf/curation",
        "/api/cockpit/runs/run-1/status",
    ):
        assert required_http_capabilities("GET", writing_get_target) == (
            CONTROL_OPERATE,
            CONTENT_READ,
        )
    for distill_get_target in (
        "/api/openclaw/distill",
        "/api/openclaw/distill/job-1",
    ):
        assert required_http_capabilities("GET", distill_get_target) == (
            CONTROL_OPERATE,
            CONTENT_READ,
            HOST_EXECUTE,
        )


@pytest.mark.parametrize(
    "target",
    [
        "/x/../api/runs",
        "/api/x/../knowledge/solve",
        "/api/%2e%2e/knowledge/solve",
        "//attacker.example/api/runs",
        "/api\\runs",
    ],
)
def test_normalized_or_ambiguous_request_targets_are_rejected(target: str) -> None:
    with pytest.raises(ValueError, match="bounded raw path"):
        required_http_capabilities("GET", target)
    with pytest.raises(ControlPlaneAuthenticationError, match="invalid proof target"):
        build_control_plane_proof(
            kid="env",
            secret=TOKEN,
            caps=[CONTROL_READ],
            method="GET",
            target=target,
        )


def test_lone_surrogate_claim_is_rejected_as_an_authentication_failure() -> None:
    claims_segment = base64.urlsafe_b64encode(b'{"origin":"\\ud800"}').decode().rstrip("=")
    signature = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
    with pytest.raises(ControlPlaneAuthenticationError, match="invalid proof claims"):
        _authenticator(CONTROL_READ).authenticate(
            f"actx1.{claims_segment}.{signature}",
            method="GET",
            target="/api/runs",
            origin="",
            now=1_030,
        )


def test_secure_credentials_registry_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    registry = tmp_path / "credentials.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": [
                    {
                        "kid": "operator-1",
                        "principal": "alice",
                        "secret": TOKEN,
                        "capabilities": [CONTROL_OPERATE, CONTROL_READ],
                        "not_before": 900,
                        "not_after": 2_000,
                    }
                ],
            }
        )
    )
    registry.chmod(0o600)
    monkeypatch.setenv(SERVER_CREDENTIALS_FILE_ENV, str(registry))
    authenticator = ControlPlaneAuthenticator.from_environment()
    principal = authenticator.authenticate(
        _direct_proof(
            caps=[CONTROL_OPERATE],
            jti="00000000000000000000000000000006",
        ),
        method="GET",
        target="/api/runs",
        origin="",
        now=1_030,
    )
    assert principal.name == "alice"

    registry.chmod(0o644)
    with pytest.raises(RuntimeError, match="permissions"):
        ControlPlaneAuthenticator.from_environment()

    writable_parent = tmp_path / "writable"
    writable_parent.mkdir(mode=0o700)
    exposed_registry = writable_parent / "credentials.json"
    exposed_registry.write_text(json.dumps({"version": 1, "credentials": []}))
    exposed_registry.chmod(0o600)
    writable_parent.chmod(0o777)
    monkeypatch.setenv(SERVER_CREDENTIALS_FILE_ENV, str(exposed_registry))
    with pytest.raises(RuntimeError, match="parent is group/world writable"):
        ControlPlaneAuthenticator.from_environment()


def test_credentials_registry_fails_closed_without_windows_dacl_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    monkeypatch.setenv(SERVER_CREDENTIALS_FILE_ENV, "C:\\secure\\credentials.json")
    monkeypatch.setattr("autocontext.server.auth.os.name", "nt")

    with pytest.raises(RuntimeError, match="unsupported on Windows.*DACL.*SERVER_TOKEN"):
        ControlPlaneAuthenticator.from_environment()


def test_tokenless_client_peer_must_be_explicitly_local() -> None:
    assert tokenless_client_is_local("127.0.0.1")
    assert tokenless_client_is_local("::1")
    assert not tokenless_client_is_local("testclient")
    assert not tokenless_client_is_local(None)
    assert not tokenless_client_is_local("203.0.113.8")


def test_testclient_principal_requires_explicit_app_fixture_option(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    from autocontext.server.app import create_app

    application = create_app()
    assert SERVER_AUTH_TOKEN_ENV not in os.environ
    with TestClient(application) as client:
        assert client.get("/api/runs", headers={"X-Forwarded-For": "testclient"}).status_code == 403
    with TestClient(create_app(allow_insecure_test_principal=True)) as client:
        assert client.get("/api/runs").status_code == 200


async def test_direct_asgi_launch_rejects_tokenless_missing_peer_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    from autocontext.server.app import create_app

    application = create_app()
    transport = httpx.ASGITransport(app=application, client=None)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/health")).status_code == 200
        denied = await client.get("/api/runs")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "Credential proof required"}

    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await application(
        {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("testserver", 80),
            "client": None,
            "root_path": "",
            "path": "/ws/events",
            "raw_path": b"/ws/events",
            "query_string": b"",
            "headers": [],
            "subprotocols": [],
            "state": {},
        },
        receive,
        send,
    )
    assert sent == [{"type": "websocket.close", "code": 4403, "reason": ""}]


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
        with pytest.raises(WebSocketDisconnect) as websocket_denied:
            with client.websocket_connect("/ws/events"):
                pass
        assert websocket_denied.value.code == 4403


def test_real_loopback_tokenless_access_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv(SERVER_AUTH_TOKEN_ENV, raising=False)
    from autocontext.server.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 51_000)) as client:
        assert client.get("/api/runs").status_code == 403
    monkeypatch.setenv(ALLOW_TOKENLESS_LOOPBACK_ENV, "1")
    with TestClient(create_app(), client=("127.0.0.1", 51_000)) as client:
        assert client.get("/api/runs").status_code == 200


def test_configured_key_requires_scoped_http_and_websocket_proofs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    from autocontext.server.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/runs").status_code == 401
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401

        content_missing = _proof(caps=[CONTROL_READ], target="/api/runs")
        assert (
            client.get(
                "/api/runs",
                headers={"Authorization": f"Bearer {content_missing}"},
            ).status_code
            == 403
        )

        encoded_content_missing = _proof(caps=[CONTROL_READ], target="/%61pi/runs")
        assert (
            client.get(
                "/%61pi/runs",
                headers={"Authorization": f"Bearer {encoded_content_missing}"},
            ).status_code
            == 403
        )

        read_proof = _proof(caps=[CONTENT_READ, CONTROL_READ], target="/api/runs")
        allowed = client.get("/api/runs", headers={"Authorization": f"Bearer {read_proof}"})
        assert allowed.status_code == 200
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {read_proof}"}).status_code == 401

        wrong_path = _proof(caps=[CONTENT_READ, CONTROL_READ], target="/api/not-runs")
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {wrong_path}"}).status_code == 401

        wrong_cap = _proof(caps=[CONTROL_OPERATE], target="/api/runs")
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {wrong_cap}"}).status_code == 403

        with pytest.raises(WebSocketDisconnect) as unauthenticated:
            with client.websocket_connect("/ws/events"):
                pass
        assert unauthenticated.value.code == 4401

        with pytest.raises(WebSocketDisconnect) as query_token:
            with client.websocket_connect(f"/ws/events?token={TOKEN}"):
                pass
        assert query_token.value.code == 4401

        event_content_missing = _proof(caps=[CONTROL_READ], target="/ws/events")
        with pytest.raises(WebSocketDisconnect) as missing_content:
            with client.websocket_connect("/ws/events", subprotocols=[event_content_missing]):
                pass
        assert missing_content.value.code == 4403

        event_proof = _proof(caps=[CONTENT_READ, CONTROL_READ], target="/ws/events")
        subprotocol = encode_server_auth_subprotocol(event_proof)
        with client.websocket_connect("/ws/events", subprotocols=[subprotocol]) as websocket:
            assert websocket.accepted_subprotocol == subprotocol
        with pytest.raises(WebSocketDisconnect) as replayed:
            with client.websocket_connect("/ws/events", subprotocols=[subprotocol]):
                pass
        assert replayed.value.code == 4401

        read_only_interactive = _proof(
            caps=[CONTENT_READ, CONTROL_READ],
            target="/ws/interactive",
        )
        with pytest.raises(WebSocketDisconnect) as insufficient:
            with client.websocket_connect("/ws/interactive", subprotocols=[read_only_interactive]):
                pass
        assert insufficient.value.code == 4403

        interactive = _proof(caps=[CONTENT_READ, CONTROL_OPERATE], target="/ws/interactive")
        with client.websocket_connect(
            "/ws/interactive",
            headers={"Authorization": f"Bearer {interactive}"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "hello"

        header_proof = _proof(caps=[CONTENT_READ, CONTROL_OPERATE], target="/ws/interactive")
        protocol_proof = _proof(caps=[CONTENT_READ, CONTROL_OPERATE], target="/ws/interactive")
        with pytest.raises(WebSocketDisconnect) as ambiguous:
            with client.websocket_connect(
                "/ws/interactive",
                headers={"Authorization": f"Bearer {header_proof}"},
                subprotocols=[protocol_proof],
            ):
                pass
        assert ambiguous.value.code == 4401


def test_host_execution_commands_require_host_execute_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    from autocontext.server.app import create_app

    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(tmp_path / "events.ndjson")
    application = create_app(controller=controller, events=events)
    interactive = _proof(caps=[CONTENT_READ, CONTROL_OPERATE], target="/ws/interactive")
    with TestClient(application) as client:
        with client.websocket_connect("/ws/interactive", subprotocols=[interactive]) as websocket:
            assert websocket.receive_json()["type"] == "hello"
            websocket.send_json({"type": "chat_agent", "role": "analyst", "message": "hello"})
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Forbidden: host:execute capability required.",
            }
            websocket.send_json({"type": "start_run", "scenario": "grid_ctf", "generations": 1})
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Forbidden: host:execute capability required.",
            }
            websocket.send_json({"type": "resume"})
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Forbidden: host:execute capability required.",
            }
            websocket.send_json({"type": "override_gate", "decision": "retry"})
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Forbidden: host:execute capability required.",
            }
    controller.submit_chat.assert_not_called()
    controller.resume.assert_not_called()
    controller.set_gate_override.assert_not_called()


def test_untrusted_origin_is_rejected_before_proof_consumption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    from autocontext.server.app import create_app

    origin = "https://evil.example"
    proof = _proof(caps=[CONTENT_READ, CONTROL_READ], target="/api/runs", origin=origin)
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/runs",
            headers={"Origin": origin, "Authorization": f"Bearer {proof}"},
        )
        assert response.status_code == 403

        with pytest.raises(WebSocketDisconnect) as rejected:
            ws_proof = _proof(
                caps=[CONTENT_READ, CONTROL_READ],
                target="/ws/events",
                origin=origin,
            )
            with client.websocket_connect(
                "/ws/events",
                headers={"Origin": origin},
                subprotocols=[ws_proof],
            ):
                pass
        assert rejected.value.code == 4403


def test_expired_http_proof_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv(SERVER_AUTH_TOKEN_ENV, TOKEN)
    from autocontext.server.app import create_app

    issued_at = int(time.time()) - 70
    expired = _proof(
        caps=[CONTENT_READ, CONTROL_READ],
        target="/api/runs",
        issued_at=issued_at,
        expires_at=issued_at + 60,
    )
    with TestClient(create_app()) as client:
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
