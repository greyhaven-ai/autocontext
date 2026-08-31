"""Scoped, short-lived authentication for the local control plane.

The legacy ``AUTOCONTEXT_SERVER_TOKEN`` remains a convenient way to configure
one key, but its value is an HMAC secret.  It is never accepted directly as a
bearer token.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import unquote, urlsplit

from fastapi import Request, WebSocket

from autocontext.security.child_process_env import clear_control_plane_secrets_from_current_process
from autocontext.server._credential_registry import (
    SERVER_AUTH_TOKEN_ENV,
    SERVER_CREDENTIALS_FILE_ENV,
    load_credentials_registry,
)

ALLOW_TOKENLESS_LOOPBACK_ENV: Final = "AUTOCONTEXT_ALLOW_TOKENLESS_LOOPBACK"
SERVER_AUTH_SUBPROTOCOL_PREFIX: Final = "actx1."
CONTROL_PLANE_AUDIENCE: Final = "autocontext-control-plane"

CONTROL_READ: Final = "control:read"
CONTROL_OPERATE: Final = "control:operate"
CONTROL_ADMIN: Final = "control:admin"
CONTENT_READ: Final = "content:read"
HOST_EXECUTE: Final = "host:execute"
ALL_CAPABILITIES: Final = frozenset({CONTROL_READ, CONTROL_OPERATE, CONTROL_ADMIN, CONTENT_READ, HOST_EXECUTE})

_MIN_SERVER_AUTH_TOKEN_LENGTH = 32
_MAX_SERVER_AUTH_SECRET_BYTES = 4096
_MAX_PROOF_TTL_SECONDS = 60
_CLOCK_SKEW_SECONDS = 5
_MAX_REPLAY_ENTRIES = 8192
_IMPLICIT_KEY_ID = "env"
_KEY_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_JTI_RE = re.compile(r"[0-9a-f]{32}\Z")
_CLAIM_KEYS = frozenset({"v", "kid", "iat", "exp", "jti", "caps", "method", "target", "origin", "aud"})
_DUMMY_AUTH_SECRET = bytes([0xA5]) * 32


class ControlPlaneAuthenticationError(ValueError):
    """A control-plane proof could not be authenticated."""


class ControlPlaneAuthorizationError(PermissionError):
    """An authenticated principal lacks the requested authority."""


@dataclass(frozen=True)
class ServerCredential:
    """One server-side credential and its maximum capability ceiling."""

    kid: str
    principal: str
    secret: bytes
    capabilities: frozenset[str]
    not_before: int | None = None
    not_after: int | None = None
    disabled: bool = False


@dataclass(frozen=True)
class ControlPlanePrincipal:
    """Identity and deliberately requested capabilities bound to one proof."""

    name: str
    kid: str
    capabilities: frozenset[str]
    expires_at: int | None


@dataclass(frozen=True)
class WebSocketAuthentication:
    """Authenticated WebSocket identity and subprotocol to echo, if any."""

    principal: ControlPlanePrincipal
    subprotocol: str | None


class ReplayCache:
    """Bounded, process-local, atomic one-time-use cache for proof JTIs."""

    def __init__(self, max_entries: int = _MAX_REPLAY_ENTRIES) -> None:
        if max_entries <= 0:
            raise ValueError("replay cache must hold at least one entry")
        self._max_entries = max_entries
        self._entries: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def consume(self, kid: str, jti: str, *, retain_until: int, now: int) -> None:
        """Record a proof identifier once, failing closed on replay or exhaustion."""
        key = (kid, jti)
        with self._lock:
            expired = [stored for stored, expiry in self._entries.items() if expiry < now]
            for stored in expired:
                del self._entries[stored]
            if key in self._entries:
                raise ControlPlaneAuthenticationError("proof has already been used")
            if len(self._entries) >= self._max_entries:
                raise ControlPlaneAuthenticationError("replay protection is unavailable")
            self._entries[key] = retain_until


class ControlPlaneAuthenticator:
    """Validate scoped HMAC proofs against authoritative server credentials."""

    def __init__(
        self,
        credentials: Iterable[ServerCredential] = (),
        *,
        replay_cache: ReplayCache | None = None,
    ) -> None:
        by_id: dict[str, ServerCredential] = {}
        for credential in credentials:
            _validate_credential(credential)
            if credential.kid in by_id:
                raise RuntimeError(f"duplicate control-plane credential id {credential.kid!r}")
            by_id[credential.kid] = credential
        self._credentials = by_id
        self._replay_cache = replay_cache or ReplayCache()

    @classmethod
    def from_environment(
        cls,
        *,
        explicit_token: str | None = None,
        replay_cache: ReplayCache | None = None,
    ) -> ControlPlaneAuthenticator:
        credentials: list[ServerCredential] = []
        registry_path = os.environ.get(SERVER_CREDENTIALS_FILE_ENV)
        if registry_path:
            credentials.extend(_load_credentials_registry(registry_path))

        token = resolve_server_auth_token(explicit_token)
        if token is not None:
            credentials.append(
                ServerCredential(
                    kid=_IMPLICIT_KEY_ID,
                    principal="host-operator",
                    secret=token.encode("utf-8"),
                    capabilities=ALL_CAPABILITIES,
                )
            )
        return cls(credentials, replay_cache=replay_cache)

    @property
    def configured(self) -> bool:
        return bool(self._credentials)

    def authenticate(
        self,
        proof: str,
        *,
        method: str,
        target: str,
        origin: str,
        now: int | None = None,
    ) -> ControlPlanePrincipal:
        """Authenticate and atomically consume one request-bound proof."""
        checked_at = int(time.time()) if now is None else now
        claims_segment, signature = _split_proof(proof)
        claims = _decode_claims(claims_segment)
        _validate_claims(
            claims,
            method=method,
            target=target,
            origin=origin,
            now=checked_at,
        )

        kid = claims["kid"]
        credential = self._credentials.get(kid)
        signing_input = f"actx1.{claims_segment}".encode("ascii")
        expected = hmac.new(
            credential.secret if credential is not None else _DUMMY_AUTH_SECRET,
            signing_input,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected) or credential is None:
            raise ControlPlaneAuthenticationError("invalid proof signature")
        _validate_credential_at_time(credential, checked_at)

        requested = frozenset(claims["caps"])
        if not requested.issubset(_expand_capabilities(credential.capabilities)):
            raise ControlPlaneAuthorizationError("requested capabilities exceed credential ceiling")

        self._replay_cache.consume(
            kid,
            claims["jti"],
            retain_until=claims["exp"] + _CLOCK_SKEW_SECONDS,
            now=checked_at,
        )
        return ControlPlanePrincipal(
            name=credential.principal,
            kid=kid,
            capabilities=_expand_capabilities(requested),
            expires_at=min(
                claims["exp"],
                credential.not_after + 1 if credential.not_after is not None else claims["exp"],
            ),
        )


def consume_control_plane_authenticator_from_environment(
    *,
    explicit_token: str | None = None,
    replay_cache: ReplayCache | None = None,
) -> ControlPlaneAuthenticator:
    """Capture server credentials once, then remove all ambient copies.

    The cleanup also runs when credential parsing fails so a caller that catches
    the startup error cannot later load project code with the secret still in
    its environment.
    """

    try:
        return ControlPlaneAuthenticator.from_environment(
            explicit_token=explicit_token,
            replay_cache=replay_cache,
        )
    finally:
        clear_control_plane_secrets_from_current_process()


def build_control_plane_proof(
    *,
    kid: str,
    secret: str | bytes,
    caps: Collection[str],
    method: str,
    target: str,
    origin: str = "",
    issued_at: int | None = None,
    expires_at: int | None = None,
    jti: str | None = None,
) -> str:
    """Build a canonical, request-bound ``actx1`` proof for a trusted client."""
    now = int(time.time()) if issued_at is None else issued_at
    expiry = now + _MAX_PROOF_TTL_SECONDS if expires_at is None else expires_at
    claims: dict[str, Any] = {
        "v": 1,
        "kid": kid,
        "iat": now,
        "exp": expiry,
        "jti": secrets.token_hex(16) if jti is None else jti,
        "caps": sorted(set(caps)),
        "method": method,
        "target": target,
        "origin": origin,
        "aud": CONTROL_PLANE_AUDIENCE,
    }
    # Apply all structural checks here too, without imposing wall-clock freshness.
    _validate_claim_shape(claims)
    if expiry <= now or expiry - now > _MAX_PROOF_TTL_SECONDS:
        raise ValueError(f"proof lifetime must be between 1 and {_MAX_PROOF_TTL_SECONDS} seconds")
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    if len(secret_bytes) < _MIN_SERVER_AUTH_TOKEN_LENGTH:
        raise ValueError(f"proof secret must contain at least {_MIN_SERVER_AUTH_TOKEN_LENGTH} bytes")

    claims_segment = _encode_base64url(
        json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signing_input = f"actx1.{claims_segment}"
    signature = hmac.new(secret_bytes, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_encode_base64url(signature)}"


def authenticate_http_request(
    authenticator: ControlPlaneAuthenticator,
    request: Request,
    *,
    allow_insecure_test_principal: bool = False,
) -> ControlPlanePrincipal:
    """Authenticate an HTTP request or grant explicit local test compatibility."""
    authorization = request.headers.get("authorization")
    proof = _read_bearer_proof(authorization)
    if authorization is not None and proof is None:
        raise ControlPlaneAuthenticationError("invalid authorization header")
    if proof is None:
        if allow_insecure_test_principal and not authenticator.configured:
            return _local_compatibility_principal()
        return _tokenless_principal(authenticator, _request_client_host(request))
    return authenticator.authenticate(
        proof,
        method=request.method.upper(),
        target=asgi_raw_target(request.scope),
        origin=request.headers.get("origin", ""),
    )


def authenticate_websocket(
    authenticator: ControlPlaneAuthenticator,
    websocket: WebSocket,
    *,
    allow_insecure_test_principal: bool = False,
) -> WebSocketAuthentication:
    """Authenticate a WebSocket handshake from a header or browser subprotocol."""
    authorization = websocket.headers.get("authorization")
    proof = _read_bearer_proof(authorization)
    if authorization is not None and proof is None:
        raise ControlPlaneAuthenticationError("invalid authorization header")
    offered = websocket.headers.get("sec-websocket-protocol")
    proof_protocols = (
        []
        if offered is None
        else [item.strip() for item in offered.split(",") if item.strip().startswith(SERVER_AUTH_SUBPROTOCOL_PREFIX)]
    )
    if len(proof_protocols) > 1 or (proof is not None and proof_protocols):
        raise ControlPlaneAuthenticationError("ambiguous WebSocket credentials")
    selected_subprotocol: str | None = None
    if proof is None:
        if proof_protocols:
            proof = proof_protocols[0]
            selected_subprotocol = proof
    if proof is None:
        if allow_insecure_test_principal and not authenticator.configured:
            return WebSocketAuthentication(
                principal=_local_compatibility_principal(),
                subprotocol=None,
            )
        principal = _tokenless_principal(authenticator, _websocket_client_host(websocket))
        return WebSocketAuthentication(principal=principal, subprotocol=None)
    principal = authenticator.authenticate(
        proof,
        method="GET",
        target=asgi_raw_target(websocket.scope),
        origin=websocket.headers.get("origin", ""),
    )
    return WebSocketAuthentication(principal=principal, subprotocol=selected_subprotocol)


def require_capability(
    principal: ControlPlanePrincipal,
    capability: str,
    *,
    now: float | None = None,
) -> None:
    """Reject an expired principal or one whose proof omitted the capability."""
    if capability not in ALL_CAPABILITIES:
        raise ValueError(f"unknown control-plane capability {capability!r}")
    checked_at = time.time() if now is None else now
    if principal.expires_at is not None and checked_at >= principal.expires_at:
        raise ControlPlaneAuthorizationError("credential proof has expired")
    if capability not in principal.capabilities:
        raise ControlPlaneAuthorizationError(f"{capability} capability required")


def required_http_capabilities(
    method: str,
    target: str,
    *,
    routed_path: str | None = None,
) -> tuple[str, ...]:
    """Map one raw HTTP request target to its minimum server-side authority."""
    normalized_method = method.upper()
    if not normalized_method or not normalized_method.isalpha():
        raise ValueError("server request method is invalid")
    if not _raw_target_is_canonical(target):
        raise ValueError("server request target must be a bounded raw path and query string")

    pathname = target.partition("?")[0] if routed_path is None else routed_path
    if not pathname.startswith("/") or "?" in pathname or "#" in pathname:
        raise ValueError("server routing path is invalid")
    read_only = normalized_method in {"GET", "HEAD"}
    control_capability = (
        CONTROL_OPERATE
        if not read_only or _http_read_route_mutates_state(pathname)
        else CONTROL_READ
    )
    capabilities = [control_capability]
    if pathname.startswith("/api/"):
        capabilities.append(CONTENT_READ)
    if (
        not read_only and _http_route_executes_host(pathname)
    ) or _http_read_route_executes_host(pathname):
        capabilities.append(HOST_EXECUTE)
    return tuple(capabilities)


def asgi_raw_target(scope: Mapping[str, Any]) -> str:
    """Return the raw path and query bytes exactly as bound by a proof."""
    raw_path = scope.get("raw_path")
    if isinstance(raw_path, bytes):
        path = raw_path.decode("latin-1")
    else:
        path = str(scope.get("path", ""))
    raw_query = scope.get("query_string", b"")
    if isinstance(raw_query, bytes):
        query = raw_query.decode("latin-1")
    else:
        query = str(raw_query)
    return f"{path}?{query}" if query else path


def resolve_server_auth_token(explicit_token: str | None = None) -> str | None:
    """Return the configured legacy HMAC key, rejecting weak non-empty values."""
    token = explicit_token if explicit_token is not None else os.environ.get(SERVER_AUTH_TOKEN_ENV)
    if token is None or token == "":
        return None
    if len(token.encode("utf-8")) < _MIN_SERVER_AUTH_TOKEN_LENGTH:
        raise RuntimeError(f"{SERVER_AUTH_TOKEN_ENV} must contain at least {_MIN_SERVER_AUTH_TOKEN_LENGTH} bytes")
    if len(token.encode("utf-8")) > _MAX_SERVER_AUTH_SECRET_BYTES:
        raise RuntimeError(f"{SERVER_AUTH_TOKEN_ENV} is too large")
    return token


def assert_secure_server_bind(
    host: str,
    auth_token: str | None = None,
    *,
    authenticator: ControlPlaneAuthenticator | None = None,
) -> None:
    """Refuse a network-visible bind unless scoped authentication is configured."""
    if authenticator is not None and auth_token is not None:
        raise ValueError("pass either auth_token or authenticator, not both")
    configured_authenticator = authenticator or ControlPlaneAuthenticator.from_environment(explicit_token=auth_token)
    if configured_authenticator.configured:
        return
    if _is_loopback_host(host) and os.environ.get(ALLOW_TOKENLESS_LOOPBACK_ENV) == "1":
        return
    raise RuntimeError(
        f"Refusing to bind the unauthenticated control plane to host {host!r}. "
        f"Set {SERVER_CREDENTIALS_FILE_ENV}, or set {SERVER_AUTH_TOKEN_ENV} to a random "
        f"HMAC key of at least {_MIN_SERVER_AUTH_TOKEN_LENGTH} bytes. For an explicitly "
        f"tokenless loopback server, set {ALLOW_TOKENLESS_LOOPBACK_ENV}=1."
    )


def assert_tokenless_browser_origins_are_local(
    authenticator: ControlPlaneAuthenticator,
    origins: Iterable[str],
) -> None:
    """Reject reverse-proxy browser origins when the server has no credentials."""
    if authenticator.configured:
        return
    if any(not _is_loopback_origin(origin) for origin in origins):
        raise RuntimeError(
            "Configured non-loopback browser origins require control-plane credentials; "
            "tokenless loopback mode must not be placed behind a reverse proxy."
        )


def encode_server_auth_subprotocol(proof: str) -> str:
    """Validate and return a proof for use as a browser WebSocket subprotocol."""
    _split_proof(proof)
    return proof


def tokenless_client_is_local(client_host: str | None) -> bool:
    """Return whether a peer is on an actual loopback transport."""
    if client_host is None:
        return False
    return _is_loopback_host(client_host)


def request_is_authorized(auth_token: str | None, authorization_header: str | None) -> bool:
    """Deprecated raw-token helper retained fail-closed for source compatibility."""
    del auth_token, authorization_header
    # Raw bearer secrets are deliberately no longer authentication credentials.
    return False


def _tokenless_principal(
    authenticator: ControlPlaneAuthenticator,
    client_host: str | None,
) -> ControlPlanePrincipal:
    if authenticator.configured:
        raise ControlPlaneAuthenticationError("credential proof required")
    if os.environ.get(ALLOW_TOKENLESS_LOOPBACK_ENV) == "1" and client_host is not None and _is_loopback_host(client_host):
        return _local_compatibility_principal()
    raise ControlPlaneAuthorizationError("credential proof required for this peer")


def _local_compatibility_principal() -> ControlPlanePrincipal:
    return ControlPlanePrincipal(
        name="local-compatibility",
        kid="local-compatibility",
        capabilities=ALL_CAPABILITIES,
        expires_at=None,
    )


def _request_client_host(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _websocket_client_host(websocket: WebSocket) -> str | None:
    return websocket.client.host if websocket.client is not None else None


def _split_proof(proof: str) -> tuple[str, bytes]:
    if any(character.isspace() for character in proof):
        raise ControlPlaneAuthenticationError("invalid proof encoding")
    pieces = proof.split(".")
    if len(pieces) != 3 or pieces[0] != "actx1" or not pieces[1] or not pieces[2]:
        raise ControlPlaneAuthenticationError("invalid proof format")
    return pieces[1], _decode_base64url(pieces[2])


def _decode_claims(claims_segment: str) -> dict[str, Any]:
    raw = _decode_base64url(claims_segment)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ControlPlaneAuthenticationError("invalid proof claims") from None
    if not isinstance(decoded, dict):
        raise ControlPlaneAuthenticationError("invalid proof claims")
    try:
        canonical = _encode_base64url(
            json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
    except (TypeError, UnicodeEncodeError, ValueError):
        raise ControlPlaneAuthenticationError("invalid proof claims") from None
    if not hmac.compare_digest(canonical, claims_segment):
        raise ControlPlaneAuthenticationError("non-canonical proof claims")
    return decoded


def _decode_base64url(value: str) -> bytes:
    if not value or "=" in value:
        raise ControlPlaneAuthenticationError("invalid base64url encoding")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        raise ControlPlaneAuthenticationError("invalid base64url encoding") from None
    if _encode_base64url(decoded) != value:
        raise ControlPlaneAuthenticationError("non-canonical base64url encoding")
    return decoded


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _validate_claims(
    claims: dict[str, Any],
    *,
    method: str,
    target: str,
    origin: str,
    now: int,
) -> None:
    _validate_claim_shape(claims)
    if claims["method"] != method.upper():
        raise ControlPlaneAuthenticationError("proof method does not match request")
    if claims["target"] != target:
        raise ControlPlaneAuthenticationError("proof target does not match request")
    if claims["origin"] != origin:
        raise ControlPlaneAuthenticationError("proof origin does not match request")
    if claims["iat"] > now + _CLOCK_SKEW_SECONDS:
        raise ControlPlaneAuthenticationError("proof is not yet valid")
    if claims["exp"] < now - _CLOCK_SKEW_SECONDS:
        raise ControlPlaneAuthenticationError("proof has expired")
    if claims["exp"] <= claims["iat"]:
        raise ControlPlaneAuthenticationError("proof expiry must follow issuance")
    if claims["exp"] - claims["iat"] > _MAX_PROOF_TTL_SECONDS:
        raise ControlPlaneAuthenticationError("proof lifetime exceeds limit")


def _validate_claim_shape(claims: dict[str, Any]) -> None:
    if set(claims) != _CLAIM_KEYS:
        raise ControlPlaneAuthenticationError("proof contains unexpected claims")
    if type(claims["v"]) is not int or claims["v"] != 1:
        raise ControlPlaneAuthenticationError("unsupported proof version")
    if not isinstance(claims["kid"], str) or _KEY_ID_RE.fullmatch(claims["kid"]) is None:
        raise ControlPlaneAuthenticationError("invalid credential id")
    if type(claims["iat"]) is not int or type(claims["exp"]) is not int:
        raise ControlPlaneAuthenticationError("invalid proof timestamps")
    if not isinstance(claims["jti"], str) or _JTI_RE.fullmatch(claims["jti"]) is None:
        raise ControlPlaneAuthenticationError("invalid proof id")
    caps = claims["caps"]
    if (
        not isinstance(caps, list)
        or not caps
        or any(not isinstance(capability, str) for capability in caps)
        or caps != sorted(set(caps))
        or not set(caps).issubset(ALL_CAPABILITIES)
    ):
        raise ControlPlaneAuthenticationError("invalid proof capabilities")
    if not isinstance(claims["method"], str) or (not claims["method"] or claims["method"] != claims["method"].upper()):
        raise ControlPlaneAuthenticationError("invalid proof method")
    for field in ("target", "origin"):
        if not isinstance(claims[field], str) or len(claims[field]) > 8192:
            raise ControlPlaneAuthenticationError(f"invalid proof {field}")
    if not _raw_target_is_canonical(claims["target"]):
        raise ControlPlaneAuthenticationError("invalid proof target")
    if claims["aud"] != CONTROL_PLANE_AUDIENCE:
        raise ControlPlaneAuthenticationError("invalid proof audience")


def _expand_capabilities(capabilities: Collection[str]) -> frozenset[str]:
    expanded = frozenset(capabilities)
    if CONTROL_ADMIN in expanded:
        return expanded | {CONTROL_READ, CONTROL_OPERATE}
    return expanded


def _http_route_executes_host(pathname: str) -> bool:
    return (
        pathname == "/api/knowledge/solve"
        or pathname == "/api/knowledge/search"
        or pathname == "/api/knowledge/import"
        or re.fullmatch(r"/api/hub/packages/from-run/[^/]+", pathname) is not None
        or re.fullmatch(r"/api/hub/packages/[^/]+/adopt", pathname) is not None
        or re.fullmatch(r"/api/cockpit/runs/[^/]+/consult", pathname) is not None
        or re.fullmatch(r"/api/openclaw/artifacts/?", pathname) is not None
        or re.fullmatch(r"/api/openclaw/(?:evaluate|validate|distill)(?:/.*)?", pathname) is not None
        or re.fullmatch(r"/api/simulations(?:/.*)?", pathname) is not None
        or re.fullmatch(r"/api/missions/[^/]+/(?:run|resume)", pathname) is not None
        or re.fullmatch(r"/api/campaigns/[^/]+/resume", pathname) is not None
    )


def _http_read_route_executes_host(pathname: str) -> bool:
    """Identify nominal reads that invoke registered Python scenario code."""
    return (
        pathname == "/api/knowledge/scenarios"
        or re.fullmatch(r"/api/knowledge/export/[^/]+", pathname) is not None
        or re.fullmatch(r"/api/knowledge/solve/[^/]+", pathname) is not None
        or pathname == "/api/openclaw/discovery/capabilities"
        or pathname == "/api/openclaw/skill/manifest"
        or re.fullmatch(r"/api/openclaw/distill(?:/[^/]+)?", pathname) is not None
        or re.fullmatch(r"/api/openclaw/discovery/scenario/[^/]+", pathname) is not None
    )


def _http_read_route_mutates_state(pathname: str) -> bool:
    """Identify legacy GET routes that persist operator artifacts."""
    return (
        re.fullmatch(r"/api/cockpit/writeup/[^/]+", pathname) is not None
        or re.fullmatch(r"/api/cockpit/scenarios/[^/]+/curation", pathname) is not None
        or re.fullmatch(r"/api/cockpit/runs/[^/]+/status", pathname) is not None
        or re.fullmatch(r"/api/openclaw/distill(?:/[^/]+)?", pathname) is not None
    )


def _raw_target_is_canonical(target: str) -> bool:
    if not target.startswith("/") or target.startswith("//") or "#" in target or "\\" in target or len(target) > 8192:
        return False
    raw_path = target.partition("?")[0]
    if re.search(r"%(?![0-9A-Fa-f]{2})", raw_path) is not None:
        return False
    return all(unquote(segment) not in {".", ".."} for segment in raw_path.split("/"))


def _validate_credential(credential: ServerCredential) -> None:
    if _KEY_ID_RE.fullmatch(credential.kid) is None:
        raise RuntimeError(f"invalid control-plane credential id {credential.kid!r}")
    if (
        not credential.principal
        or len(credential.principal) > 128
        or any(ord(character) < 32 for character in credential.principal)
    ):
        raise RuntimeError(f"invalid principal for credential {credential.kid!r}")
    if not (_MIN_SERVER_AUTH_TOKEN_LENGTH <= len(credential.secret) <= _MAX_SERVER_AUTH_SECRET_BYTES):
        raise RuntimeError(
            f"credential {credential.kid!r} secret must contain between "
            f"{_MIN_SERVER_AUTH_TOKEN_LENGTH} and {_MAX_SERVER_AUTH_SECRET_BYTES} bytes"
        )
    if not credential.capabilities or not credential.capabilities.issubset(ALL_CAPABILITIES):
        raise RuntimeError(f"invalid capabilities for credential {credential.kid!r}")
    for value in (credential.not_before, credential.not_after):
        if value is not None and type(value) is not int:
            raise RuntimeError(f"invalid validity window for credential {credential.kid!r}")
    if credential.not_before is not None and credential.not_after is not None and credential.not_after <= credential.not_before:
        raise RuntimeError(f"invalid validity window for credential {credential.kid!r}")


def _validate_credential_at_time(credential: ServerCredential, now: int) -> None:
    if credential.disabled:
        raise ControlPlaneAuthenticationError("credential is disabled")
    if credential.not_before is not None and now < credential.not_before:
        raise ControlPlaneAuthenticationError("credential is not yet valid")
    if credential.not_after is not None and now > credential.not_after:
        raise ControlPlaneAuthenticationError("credential has expired")


def _load_credentials_registry(raw_path: str) -> list[ServerCredential]:
    return [
        ServerCredential(
            kid=loaded.kid,
            principal=loaded.principal,
            secret=loaded.secret,
            capabilities=loaded.capabilities,
            not_before=loaded.not_before,
            not_after=loaded.not_after,
            disabled=loaded.disabled,
        )
        for loaded in load_credentials_registry(raw_path)
    ]


def _read_bearer_proof(value: str | None) -> str | None:
    if value is None or not value.startswith("Bearer "):
        return None
    candidate = value.removeprefix("Bearer ")
    if not candidate or any(character.isspace() for character in candidate):
        return None
    if not candidate.startswith(SERVER_AUTH_SUBPROTOCOL_PREFIX):
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


def _is_loopback_origin(origin: str) -> bool:
    try:
        hostname = urlsplit(origin).hostname
    except ValueError:
        return False
    return hostname is not None and _is_loopback_host(hostname)
