"""Authentication and network-boundary helpers for the local control plane."""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import os
from collections.abc import Collection

from fastapi import WebSocket

SERVER_AUTH_TOKEN_ENV = "AUTOCONTEXT_SERVER_TOKEN"
SERVER_AUTH_SUBPROTOCOL_PREFIX = "autocontext.bearer."
_MIN_SERVER_AUTH_TOKEN_LENGTH = 32


def resolve_server_auth_token(explicit_token: str | None = None) -> str | None:
    """Return the configured bearer token, rejecting weak non-empty values."""
    token = explicit_token if explicit_token is not None else os.environ.get(SERVER_AUTH_TOKEN_ENV)
    if token is None or token == "":
        return None
    if len(token) < _MIN_SERVER_AUTH_TOKEN_LENGTH:
        raise RuntimeError(f"{SERVER_AUTH_TOKEN_ENV} must contain at least {_MIN_SERVER_AUTH_TOKEN_LENGTH} characters")
    return token


def assert_secure_server_bind(host: str, auth_token: str | None = None) -> None:
    """Refuse a network-visible bind unless request authentication is configured."""
    token = resolve_server_auth_token(auth_token)
    if _is_loopback_host(host) or token is not None:
        return
    raise RuntimeError(
        f"Refusing to bind the unauthenticated control plane to non-loopback host {host!r}. "
        f"Set {SERVER_AUTH_TOKEN_ENV} to a random value of at least "
        f"{_MIN_SERVER_AUTH_TOKEN_LENGTH} characters."
    )


def request_is_authorized(auth_token: str | None, authorization_header: str | None) -> bool:
    """Validate an HTTP Authorization bearer value."""
    if auth_token is None:
        return True
    candidate = _read_bearer_token(authorization_header)
    return candidate is not None and hmac.compare_digest(candidate, auth_token)


def tokenless_client_is_local(client_host: str | None) -> bool:
    """Allow tokenless app access only from an actual loopback transport peer."""
    if client_host is None:
        # ASGI servers are allowed to omit peer metadata.  Absence is not
        # evidence of a loopback transport, so fail closed at that boundary.
        return False
    # Starlette's in-process TestClient uses this non-network sentinel.
    if client_host == "testclient":
        return True
    return _is_loopback_host(client_host)


def websocket_rejection_code(
    websocket: WebSocket,
    *,
    auth_token: str | None,
    allowed_origins: Collection[str],
) -> int | None:
    """Return a private close code when a WebSocket handshake must fail closed."""
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in allowed_origins:
        return 4403

    if auth_token is None:
        client_host = websocket.client.host if websocket.client is not None else None
        return None if tokenless_client_is_local(client_host) else 4403
    candidate = _read_bearer_token(websocket.headers.get("authorization"))
    if candidate is not None and hmac.compare_digest(candidate, auth_token):
        return None
    if websocket_auth_subprotocol(websocket, auth_token=auth_token) is None:
        return 4401
    return None


def encode_server_auth_subprotocol(token: str) -> str:
    """Encode a bearer token as a browser-compatible WebSocket subprotocol."""
    encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{SERVER_AUTH_SUBPROTOCOL_PREFIX}{encoded}"


def websocket_auth_subprotocol(websocket: WebSocket, *, auth_token: str | None) -> str | None:
    """Return the exact authenticated subprotocol to echo during acceptance."""
    if auth_token is None:
        return None
    offered = websocket.headers.get("sec-websocket-protocol")
    if offered is None:
        return None
    for raw_protocol in offered.split(","):
        protocol = raw_protocol.strip()
        candidate = _decode_server_auth_subprotocol(protocol)
        if candidate is not None and hmac.compare_digest(candidate, auth_token):
            return protocol
    return None


def _decode_server_auth_subprotocol(protocol: str) -> str | None:
    if not protocol.startswith(SERVER_AUTH_SUBPROTOCOL_PREFIX):
        return None
    encoded = protocol.removeprefix(SERVER_AUTH_SUBPROTOCOL_PREFIX)
    if not encoded:
        return None
    try:
        raw = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
        candidate = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if encode_server_auth_subprotocol(candidate) != protocol:
        return None
    return candidate


def _read_bearer_token(value: str | None) -> str | None:
    if value is None or not value.startswith("Bearer "):
        return None
    candidate = value.removeprefix("Bearer ")
    if not candidate or any(char.isspace() for char in candidate):
        return None
    return candidate


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().removeprefix("[").removesuffix("]")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
