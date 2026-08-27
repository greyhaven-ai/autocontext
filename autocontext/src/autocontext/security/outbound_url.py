"""Fail-closed outbound HTTP policy for untrusted or remotely supplied URLs.

The standard-library URL openers transparently follow redirects and resolve a
hostname again when they connect.  For SSRF-sensitive call sites that creates
two gaps: a validated public URL can redirect to an internal service, and DNS
can return a different address after validation.  This module resolves once,
validates every returned address, and connects directly to one of those pinned
addresses while retaining the original hostname for HTTP Host and TLS SNI.
"""

from __future__ import annotations

import http.client
import ipaddress
import math
import queue
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import SplitResult, urlsplit

DEFAULT_OUTBOUND_TIMEOUT_SECONDS = 10.0
DEFAULT_FIXTURE_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_JSON_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_FIXTURE_CONTENT_TYPES = (
    "application/gzip",
    "application/json",
    "application/*+json",
    "application/octet-stream",
    "application/pdf",
    "application/x-yaml",
    "application/xml",
    "application/yaml",
    "application/zip",
    "binary/octet-stream",
    "text/csv",
    "text/markdown",
    "text/plain",
    "text/xml",
    "text/yaml",
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_METADATA_HOSTNAMES = frozenset(
    {
        "instance-data",
        "instance-data.ec2.internal",
        "metadata",
        "metadata.aws.internal",
        "metadata.azure.internal",
        "metadata.google.internal",
    }
)
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
        ipaddress.ip_address("147.75.207.207"),  # Equinix Metal metadata
        ipaddress.ip_address("168.63.129.16"),  # Azure platform virtual IP
        ipaddress.ip_address("169.254.0.23"),  # Tencent Cloud metadata
        ipaddress.ip_address("169.254.169.253"),
        ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure metadata
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS task credentials
        ipaddress.ip_address("169.254.170.23"),  # AWS EKS Pod Identity
        ipaddress.ip_address("192.0.0.192"),  # Oracle Cloud metadata
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS IPv6
    }
)


class OutboundUrlError(ValueError):
    """An outbound URL or response violated the configured network policy."""


class OutboundHttpError(OutboundUrlError):
    """The remote server returned an unsuccessful HTTP status."""


@dataclass(frozen=True, slots=True)
class OutboundUrlPolicy:
    """Limits for one outbound request.

    ``allow_private_networks`` is deliberately an explicit compatibility
    escape hatch for trusted local sidecars.  Metadata endpoints stay blocked
    even when it is enabled.
    """

    timeout_seconds: float = DEFAULT_OUTBOUND_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_JSON_MAX_RESPONSE_BYTES
    allow_private_networks: bool = False
    allowed_content_types: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("outbound timeout_seconds must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("outbound max_response_bytes must be positive")
        if self.allowed_content_types is not None and not self.allowed_content_types:
            raise ValueError("allowed_content_types must be non-empty when content-type enforcement is enabled")


@dataclass(frozen=True, slots=True)
class ResolvedOutboundUrl:
    """A parsed URL whose addresses have passed policy validation."""

    parsed: SplitResult
    hostname: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OutboundResponse:
    """Bounded response data returned by :func:`request_outbound_bytes`."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class _ResponseLike(Protocol):
    status: int

    def getheader(self, name: str, default: str | None = None) -> str | None: ...

    def getheaders(self) -> list[tuple[str, str]]: ...

    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _ConnectionLike(Protocol):
    sock: socket.socket | None

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> _ResponseLike: ...

    def close(self) -> None: ...


Resolver = Callable[[str, int], tuple[str, ...]]
ConnectionFactory = Callable[[ResolvedOutboundUrl, float], _ConnectionLike]


def resolve_host_addresses(hostname: str, port: int) -> tuple[str, ...]:
    """Resolve all TCP addresses for ``hostname`` without initiating a connection."""

    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise OutboundUrlError(f"could not resolve outbound URL host {hostname!r}") from exc

    addresses: list[str] = []
    for result in results:
        address = str(result[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise OutboundUrlError(f"outbound URL host {hostname!r} resolved to no addresses")
    return tuple(addresses)


def validate_outbound_url(
    url: str,
    *,
    policy: OutboundUrlPolicy | None = None,
    resolver: Resolver | None = None,
) -> ResolvedOutboundUrl:
    """Parse, resolve, and validate an outbound HTTP URL.

    Every DNS answer must satisfy the policy.  Rejecting a mixed public/private
    answer avoids selecting a safe address during validation and a private one
    during connection setup.
    """

    effective_policy = policy or OutboundUrlPolicy()
    if not isinstance(url, str) or not url or any(character in url for character in ("\x00", "\r", "\n")):
        raise OutboundUrlError("outbound URL must be a non-empty string without control characters")
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError as exc:
        raise OutboundUrlError("outbound URL is malformed") from exc

    if scheme not in _ALLOWED_SCHEMES:
        raise OutboundUrlError("outbound URL scheme must be http or https")
    if not hostname:
        raise OutboundUrlError("outbound URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise OutboundUrlError("outbound URL must not contain inline credentials")
    if parsed.fragment:
        raise OutboundUrlError("outbound URL must not contain a fragment")

    normalized_hostname = hostname.rstrip(".").lower()
    try:
        normalized_hostname = normalized_hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundUrlError("outbound URL hostname is invalid") from exc
    if not normalized_hostname:
        raise OutboundUrlError("outbound URL hostname is invalid")
    if _is_metadata_hostname(normalized_hostname):
        raise OutboundUrlError("cloud metadata endpoints are not allowed")

    port = parsed_port if parsed_port is not None else (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise OutboundUrlError("outbound URL port must be between 1 and 65535")

    literal_address = _parse_ip_address(normalized_hostname)
    addresses = (
        (normalized_hostname,)
        if literal_address is not None
        else _resolve_with_timeout(
            resolver or resolve_host_addresses,
            normalized_hostname,
            port,
            effective_policy.timeout_seconds,
        )
    )
    if not addresses:
        raise OutboundUrlError(f"outbound URL host {normalized_hostname!r} resolved to no addresses")

    for address in addresses:
        parsed_address = _parse_ip_address(address)
        if parsed_address is None:
            raise OutboundUrlError(f"resolver returned an invalid IP address for {normalized_hostname!r}")
        policy_address = parsed_address
        if isinstance(parsed_address, ipaddress.IPv6Address) and parsed_address.ipv4_mapped is not None:
            policy_address = parsed_address.ipv4_mapped
        if policy_address in _METADATA_ADDRESSES:
            raise OutboundUrlError("cloud metadata endpoints are not allowed")
        if (
            policy_address.is_unspecified
            or policy_address.is_multicast
            or (policy_address.is_reserved and not policy_address.is_loopback)
        ):
            raise OutboundUrlError(
                f"outbound URL host {normalized_hostname!r} resolves to a forbidden address",
            )
        if not effective_policy.allow_private_networks and not policy_address.is_global:
            raise OutboundUrlError(
                f"outbound URL host {normalized_hostname!r} resolves to a non-public address",
            )

    return ResolvedOutboundUrl(
        parsed=parsed._replace(scheme=scheme),
        hostname=normalized_hostname,
        port=port,
        addresses=tuple(addresses),
    )


def request_outbound_bytes(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    policy: OutboundUrlPolicy | None = None,
    resolver: Resolver | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> OutboundResponse:
    """Perform one direct, DNS-pinned, non-redirecting bounded HTTP request."""

    effective_policy = policy or OutboundUrlPolicy()
    deadline = time.monotonic() + effective_policy.timeout_seconds
    resolved = validate_outbound_url(url, policy=effective_policy, resolver=resolver)
    connection = (connection_factory or _create_pinned_connection)(resolved, _remaining_seconds(deadline))
    response: _ResponseLike | None = None
    try:
        connection.request(
            method.upper(),
            _request_target(resolved.parsed),
            body=body,
            headers=dict(headers or {}),
        )
        _set_socket_deadline(connection, deadline)
        response = connection.getresponse()
        _reject_redirect_or_error(response)
        _validate_content_type(response, effective_policy.allowed_content_types)
        _reject_oversized_content_length(response, effective_policy.max_response_bytes)
        response_body = _read_response_with_limits(
            response,
            connection=connection,
            max_bytes=effective_policy.max_response_bytes,
            deadline=deadline,
        )
        return OutboundResponse(
            status=response.status,
            headers={key.lower(): value for key, value in response.getheaders()},
            body=response_body,
        )
    except (OutboundUrlError, OSError, http.client.HTTPException, TimeoutError) as exc:
        if isinstance(exc, OutboundUrlError):
            raise
        raise OutboundUrlError(f"outbound HTTP request failed: {exc}") from exc
    finally:
        if response is not None:
            response.close()
        connection.close()


def _parse_ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    address_without_scope = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(address_without_scope)
    except ValueError:
        return None


def _resolve_with_timeout(
    resolver: Resolver,
    hostname: str,
    port: int,
    timeout_seconds: float,
) -> tuple[str, ...]:
    """Bound potentially blocking platform DNS resolution with a daemon worker."""

    result_queue: queue.Queue[tuple[bool, tuple[str, ...] | BaseException]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put((True, resolver(hostname, port)))
        except BaseException as exc:  # propagate resolver failures on the requesting thread
            result_queue.put((False, exc))

    worker = threading.Thread(target=resolve, name="autocontext-outbound-dns", daemon=True)
    worker.start()
    try:
        succeeded, result = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise OutboundUrlError(f"DNS resolution timed out for outbound URL host {hostname!r}") from exc
    if not succeeded:
        if isinstance(result, OutboundUrlError):
            raise result
        if isinstance(result, BaseException):
            raise OutboundUrlError(f"could not resolve outbound URL host {hostname!r}") from result
        raise OutboundUrlError(f"could not resolve outbound URL host {hostname!r}")
    if isinstance(result, BaseException):  # defensive type narrowing; unreachable when succeeded is true
        raise OutboundUrlError(f"could not resolve outbound URL host {hostname!r}") from result
    return result


def _is_metadata_hostname(hostname: str) -> bool:
    return (
        hostname in _METADATA_HOSTNAMES
        or hostname.endswith(".metadata.google.internal")
        or hostname.endswith(".instance-data.ec2.internal")
    )


def _request_target(parsed: SplitResult) -> str:
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return target


def _reject_redirect_or_error(response: _ResponseLike) -> None:
    if 300 <= response.status < 400:
        raise OutboundUrlError("outbound HTTP redirects are not allowed")
    if response.status >= 400:
        raise OutboundHttpError(f"outbound HTTP request returned status {response.status}")


def _reject_oversized_content_length(response: _ResponseLike, max_bytes: int) -> None:
    content_length = response.getheader("Content-Length")
    if content_length is None:
        return
    try:
        declared_size = int(content_length)
    except ValueError as exc:
        raise OutboundUrlError("outbound response has an invalid Content-Length") from exc
    if declared_size < 0 or declared_size > max_bytes:
        raise OutboundUrlError(f"outbound response exceeds the {max_bytes}-byte limit")


def _validate_content_type(response: _ResponseLike, allowed_content_types: tuple[str, ...] | None) -> None:
    if allowed_content_types is None:
        return
    raw_content_type = response.getheader("Content-Type")
    if raw_content_type is None:
        raise OutboundUrlError("outbound response is missing a required Content-Type")
    media_type = raw_content_type.partition(";")[0].strip().lower()
    if not media_type or not any(_content_type_matches(media_type, allowed) for allowed in allowed_content_types):
        raise OutboundUrlError(f"outbound response Content-Type {media_type or raw_content_type!r} is not allowed")


def _content_type_matches(media_type: str, allowed: str) -> bool:
    normalized_allowed = allowed.strip().lower()
    if normalized_allowed.startswith("application/*+"):
        suffix = normalized_allowed.removeprefix("application/*")
        return media_type.startswith("application/") and media_type.endswith(suffix)
    return media_type == normalized_allowed


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OutboundUrlError("outbound HTTP request timed out")
    return remaining


def _set_socket_deadline(connection: _ConnectionLike, deadline: float) -> None:
    remaining = _remaining_seconds(deadline)
    if connection.sock is not None:
        connection.sock.settimeout(remaining)


def _read_response_with_limits(
    response: _ResponseLike,
    *,
    connection: _ConnectionLike,
    max_bytes: int,
    deadline: float,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    read_once = getattr(response, "read1", response.read)
    while True:
        _set_socket_deadline(connection, deadline)
        chunk = read_once(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise OutboundUrlError(f"outbound response exceeds the {max_bytes}-byte limit")
    return b"".join(chunks)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, port: int, address: str, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, address: str, timeout: float) -> None:
        ssl_context = ssl.create_default_context()
        super().__init__(hostname, port=port, timeout=timeout, context=ssl_context)
        self._pinned_address = address
        self._pinned_ssl_context = ssl_context

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
        )
        try:
            self.sock = self._pinned_ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _create_pinned_connection(resolved: ResolvedOutboundUrl, timeout: float) -> _ConnectionLike:
    address = resolved.addresses[0]
    if resolved.parsed.scheme == "https":
        return cast(_ConnectionLike, _PinnedHTTPSConnection(resolved.hostname, resolved.port, address, timeout))
    return cast(_ConnectionLike, _PinnedHTTPConnection(resolved.hostname, resolved.port, address, timeout))


__all__ = [
    "DEFAULT_FIXTURE_MAX_RESPONSE_BYTES",
    "DEFAULT_FIXTURE_CONTENT_TYPES",
    "DEFAULT_JSON_MAX_RESPONSE_BYTES",
    "DEFAULT_OUTBOUND_TIMEOUT_SECONDS",
    "OutboundHttpError",
    "OutboundResponse",
    "OutboundUrlError",
    "OutboundUrlPolicy",
    "ResolvedOutboundUrl",
    "request_outbound_bytes",
    "resolve_host_addresses",
    "validate_outbound_url",
]
