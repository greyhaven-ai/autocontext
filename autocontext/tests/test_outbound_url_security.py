"""Security contracts for centralized outbound HTTP requests."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from autocontext.security.outbound_url import (
    OutboundUrlError,
    OutboundUrlPolicy,
    ResolvedOutboundUrl,
    request_outbound_bytes,
    validate_outbound_url,
)


def _resolve_to(*addresses: str):
    def resolve(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return addresses

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/resource",
        "https://user:password@example.com/",
        "https://example.com/path#fragment",
        "https://example.com/\r\nInjected: yes",
    ],
)
def test_rejects_unsafe_url_shapes(url: str) -> None:
    with pytest.raises(OutboundUrlError):
        validate_outbound_url(url, resolver=_resolve_to("93.184.216.34"))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "169.254.10.20",
        "::1",
        "fe80::1",
    ],
)
def test_rejects_non_public_dns_answers_by_default(address: str) -> None:
    with pytest.raises(OutboundUrlError, match="non-public"):
        validate_outbound_url("https://example.test/data", resolver=_resolve_to(address))


def test_rejects_mixed_public_and_private_dns_answers() -> None:
    with pytest.raises(OutboundUrlError, match="non-public"):
        validate_outbound_url(
            "https://example.test/data",
            resolver=_resolve_to("93.184.216.34", "127.0.0.1"),
        )


def test_explicit_private_network_opt_in_allows_loopback() -> None:
    resolved = validate_outbound_url(
        "http://localhost:8080/execute",
        policy=OutboundUrlPolicy(allow_private_networks=True),
        resolver=_resolve_to("127.0.0.1"),
    )

    assert resolved.addresses == ("127.0.0.1",)


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("http://metadata.google.internal/computeMetadata/v1", "93.184.216.34"),
        ("http://metadata/", "127.0.0.1"),
        ("http://169.254.169.254/latest/meta-data", "169.254.169.254"),
        ("http://100.100.100.200/latest/meta-data", "100.100.100.200"),
        ("http://169.254.170.2/credentials", "169.254.170.2"),
        ("http://[::ffff:169.254.169.254]/latest/meta-data", "::ffff:169.254.169.254"),
    ],
)
def test_metadata_targets_stay_blocked_with_private_opt_in(url: str, address: str) -> None:
    with pytest.raises(OutboundUrlError, match="metadata"):
        validate_outbound_url(
            url,
            policy=OutboundUrlPolicy(allow_private_networks=True),
            resolver=_resolve_to(address),
        )


@pytest.mark.parametrize("address", ["0.0.0.0", "224.0.0.1", "240.0.0.1", "::"])
def test_private_opt_in_does_not_allow_unspecified_multicast_or_reserved(address: str) -> None:
    with pytest.raises(OutboundUrlError, match="forbidden"):
        validate_outbound_url(
            "http://trusted-local.test/",
            policy=OutboundUrlPolicy(allow_private_networks=True),
            resolver=_resolve_to(address),
        )


def test_dns_resolution_is_included_in_request_timeout() -> None:
    def stuck_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        time.sleep(0.2)
        return ("93.184.216.34",)

    started = time.monotonic()
    with pytest.raises(OutboundUrlError, match="DNS resolution timed out"):
        validate_outbound_url(
            "https://slow-dns.example/data",
            policy=OutboundUrlPolicy(timeout_seconds=0.01),
            resolver=stuck_resolver,
        )

    assert time.monotonic() - started < 0.1


class _FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: Mapping[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self._offset = 0
        self._headers = dict(headers or {})
        self.closed = False

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers.items())

    def read1(self, amount: int) -> bytes:
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def read(self, amount: int | None = None) -> bytes:
        requested = len(self._body) if amount is None else amount
        return self.read1(requested)

    def close(self) -> None:
        self.closed = True


@dataclass
class _FakeConnection:
    response: _FakeResponse
    sock: _FakeSocket | None = field(default_factory=_FakeSocket)
    requests: list[tuple[str, str, bytes | None, Mapping[str, str] | None]] = field(default_factory=list)
    closed: bool = False

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.requests.append((method, url, body, headers))

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def _fake_connection_factory(connection: _FakeConnection, captured: dict[str, Any]):
    def create(resolved: ResolvedOutboundUrl, timeout: float) -> _FakeConnection:
        captured["resolved"] = resolved
        captured["timeout"] = timeout
        return connection

    return create


def test_request_connects_to_the_validated_dns_answer_and_preserves_target() -> None:
    captured: dict[str, Any] = {}
    connection = _FakeConnection(_FakeResponse(b'{"ok":true}', headers={"Content-Length": "11"}))

    result = request_outbound_bytes(
        "https://service.example/api/run?mode=safe",
        method="POST",
        body=b"{}",
        headers={"Content-Type": "application/json"},
        resolver=_resolve_to("93.184.216.34"),
        connection_factory=_fake_connection_factory(connection, captured),
    )

    assert captured["resolved"].addresses == ("93.184.216.34",)
    assert connection.requests == [
        ("POST", "/api/run?mode=safe", b"{}", {"Content-Type": "application/json"}),
    ]
    assert result.body == b'{"ok":true}'
    assert connection.closed is True


def test_request_rejects_redirects_without_following_location() -> None:
    connection = _FakeConnection(
        _FakeResponse(b"", status=302, headers={"Location": "http://127.0.0.1/admin"}),
    )

    with pytest.raises(OutboundUrlError, match="redirect"):
        request_outbound_bytes(
            "https://service.example/start",
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=_fake_connection_factory(connection, {}),
        )

    assert len(connection.requests) == 1


def test_request_rejects_declared_oversized_response_before_reading() -> None:
    response = _FakeResponse(b"not-read", headers={"Content-Length": "999"})
    connection = _FakeConnection(response)

    with pytest.raises(OutboundUrlError, match="byte limit"):
        request_outbound_bytes(
            "https://service.example/data",
            policy=OutboundUrlPolicy(max_response_bytes=8),
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=_fake_connection_factory(connection, {}),
        )

    assert response._offset == 0


def test_request_rejects_streamed_response_over_limit() -> None:
    connection = _FakeConnection(_FakeResponse(b"123456789"))

    with pytest.raises(OutboundUrlError, match="byte limit"):
        request_outbound_bytes(
            "https://service.example/data",
            policy=OutboundUrlPolicy(max_response_bytes=8),
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=_fake_connection_factory(connection, {}),
        )


@pytest.mark.parametrize("content_type", [None, "text/html; charset=utf-8"])
def test_request_rejects_missing_or_unexpected_content_type(content_type: str | None) -> None:
    headers = {} if content_type is None else {"Content-Type": content_type}
    connection = _FakeConnection(_FakeResponse(b"{}", headers=headers))

    with pytest.raises(OutboundUrlError, match="Content-Type"):
        request_outbound_bytes(
            "https://service.example/data",
            policy=OutboundUrlPolicy(allowed_content_types=("application/json", "application/*+json")),
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=_fake_connection_factory(connection, {}),
        )


def test_request_accepts_structured_json_content_type_with_parameters() -> None:
    connection = _FakeConnection(
        _FakeResponse(b"{}", headers={"Content-Type": "application/vnd.openclaw+json; charset=utf-8"}),
    )

    result = request_outbound_bytes(
        "https://service.example/data",
        policy=OutboundUrlPolicy(allowed_content_types=("application/json", "application/*+json")),
        resolver=_resolve_to("93.184.216.34"),
        connection_factory=_fake_connection_factory(connection, {}),
    )

    assert result.body == b"{}"
