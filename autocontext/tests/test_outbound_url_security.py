"""Security contracts for centralized outbound HTTP requests."""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from autocontext.security import outbound_url
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
        "https://exa\tmple.com/data",
        "https://example.com/\x1fdata",
        "https://example.com/\x7fdata",
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
    with pytest.raises(OutboundUrlError, match="non-public|link-local"):
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


@pytest.mark.parametrize("address", ["169.254.10.20", "fe80::1", "::ffff:169.254.10.20"])
def test_private_network_opt_in_still_rejects_link_local_addresses(address: str) -> None:
    with pytest.raises(OutboundUrlError, match="link-local"):
        validate_outbound_url(
            "http://trusted-local.test/",
            policy=OutboundUrlPolicy(allow_private_networks=True),
            resolver=_resolve_to(address),
        )


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


def test_dns_resolution_is_included_in_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    resolver_started = threading.Event()
    pool = outbound_url._BoundedResolverPool(max_workers=1, max_pending=0)
    monkeypatch.setattr(outbound_url, "_DNS_RESOLVER_POOL", pool)
    monkeypatch.setattr(outbound_url, "_single_threaded_fork_resolver_available", lambda: False)

    def stuck_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        resolver_started.set()
        release.wait()
        return ("93.184.216.34",)

    started = time.monotonic()
    try:
        with pytest.raises(OutboundUrlError, match="DNS resolution timed out"):
            validate_outbound_url(
                "https://slow-dns.example/data",
                policy=OutboundUrlPolicy(timeout_seconds=0.01),
                resolver=stuck_resolver,
            )
        assert resolver_started.is_set()
    finally:
        release.set()
        pool.shutdown()

    assert time.monotonic() - started < 0.1


def test_single_threaded_resolution_leaves_later_python_isolation_available() -> None:
    from autocontext.execution.isolated_python import (
        local_isolation_available,
        run_isolated_json,
    )

    if not local_isolation_available():
        pytest.skip("local fork isolation is unavailable")
    threads_before = tuple(threading.enumerate())

    resolved = validate_outbound_url(
        "https://resolver-isolation.example/data",
        resolver=_resolve_to("93.184.216.34"),
    )

    assert resolved.addresses == ("93.184.216.34",)
    assert tuple(threading.enumerate()) == threads_before
    assert run_isolated_json(lambda: {"isolated": True}, timeout_seconds=1.0) == {"isolated": True}


def test_transient_fallback_worker_retires_before_later_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution.isolated_python import (
        local_isolation_available,
        run_isolated_json,
    )

    if not local_isolation_available():
        pytest.skip("local fork isolation is unavailable")
    release = threading.Event()
    temporary_thread = threading.Thread(target=release.wait, daemon=True)
    pool = outbound_url._BoundedResolverPool(max_workers=2, max_pending=0)
    monkeypatch.setattr(outbound_url, "_DNS_RESOLVER_POOL", pool)
    temporary_thread.start()
    try:
        resolved = validate_outbound_url(
            "https://fallback-resolver.example/data",
            resolver=_resolve_to("93.184.216.34"),
        )
        assert resolved.addresses == ("93.184.216.34",)
        assert not any(thread.name.startswith(pool.thread_name_prefix) for thread in threading.enumerate())
    finally:
        release.set()
        temporary_thread.join(timeout=1.0)
        pool.shutdown()

    # On Darwin the Python thread may be joined just before its Mach thread is
    # fully retired. Admission remains fail-closed during that brief kernel
    # teardown window, so require bounded eventual retirement rather than an
    # unsafe immediate fork.
    retirement_deadline = time.monotonic() + 0.1
    while not local_isolation_available() and time.monotonic() < retirement_deadline:
        time.sleep(0.001)
    assert local_isolation_available()
    assert run_isolated_json(lambda: True, timeout_seconds=1.0) is True


def test_post_start_isolation_ownership_loss_does_not_rerun_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution import isolated_python

    fallback_calls: list[tuple[str, int]] = []

    class RecordingFallback:
        def resolve(
            self,
            _resolver: Any,
            hostname: str,
            port: int,
            _timeout_seconds: float,
        ) -> tuple[str, ...]:
            fallback_calls.append((hostname, port))
            return ("93.184.216.34",)

    def lose_ownership(*_args: Any, **_kwargs: Any) -> Any:
        raise isolated_python._ChildOwnershipLost("ownership lost after execution")

    monkeypatch.setattr(isolated_python, "run_isolated_json", lose_ownership)
    monkeypatch.setattr(outbound_url, "_single_threaded_fork_resolver_available", lambda: True)
    monkeypatch.setattr(outbound_url, "_DNS_RESOLVER_POOL", RecordingFallback())

    with pytest.raises(OutboundUrlError, match="could not resolve"):
        validate_outbound_url(
            "https://ownership-loss.example/data",
            resolver=_resolve_to("93.184.216.34"),
        )

    assert fallback_calls == []


def test_stuck_dns_resolvers_have_bounded_workers_and_pending_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    entered_lock = threading.Lock()
    entered_count = 0
    all_workers_entered = threading.Event()
    pool = outbound_url._BoundedResolverPool(max_workers=2, max_pending=1)
    monkeypatch.setattr(outbound_url, "_DNS_RESOLVER_POOL", pool)
    monkeypatch.setattr(outbound_url, "_single_threaded_fork_resolver_available", lambda: False)

    def stuck_resolver(hostname: str, port: int) -> tuple[str, ...]:
        nonlocal entered_count
        del hostname, port
        with entered_lock:
            entered_count += 1
            if entered_count == 3:
                all_workers_entered.set()
        release.wait()
        return ("93.184.216.34",)

    started = time.monotonic()
    try:
        for index in range(20):
            with pytest.raises(OutboundUrlError, match="DNS (resolution timed out|resolver capacity is exhausted)"):
                validate_outbound_url(
                    f"https://slow-dns-{index}.example/data",
                    policy=OutboundUrlPolicy(timeout_seconds=0.005),
                    resolver=stuck_resolver,
                )

        assert all_workers_entered.wait(0.1)
        workers = [thread for thread in threading.enumerate() if thread.name.startswith(pool.thread_name_prefix)]
        assert len(workers) == 3
        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        pool.shutdown()

    assert not any(thread.name.startswith(pool.thread_name_prefix) for thread in threading.enumerate())


def test_saturated_dns_pool_shutdown_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    entered = threading.Event()
    pool = outbound_url._BoundedResolverPool(max_workers=1, max_pending=2)
    monkeypatch.setattr(outbound_url, "_DNS_RESOLVER_POOL", pool)
    monkeypatch.setattr(outbound_url, "_single_threaded_fork_resolver_available", lambda: False)

    def stuck_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        entered.set()
        release.wait()
        return ("93.184.216.34",)

    try:
        for index in range(3):
            with pytest.raises(OutboundUrlError, match="DNS resolution timed out"):
                validate_outbound_url(
                    f"https://shutdown-dns-{index}.example/data",
                    policy=OutboundUrlPolicy(timeout_seconds=0.005),
                    resolver=stuck_resolver,
                )
        assert entered.wait(0.1)

        started = time.monotonic()
        pool.shutdown(timeout_seconds=0.01)
        assert time.monotonic() - started < 0.1
    finally:
        release.set()
        pool.shutdown(timeout_seconds=1.0)

    assert not any(thread.name.startswith(pool.thread_name_prefix) for thread in threading.enumerate())


def test_dns_pool_shutdown_wakes_concurrent_resolve_waiter() -> None:
    release = threading.Event()
    entered = threading.Event()
    pool = outbound_url._BoundedResolverPool(max_workers=1, max_pending=0)
    failures: list[BaseException] = []

    def stuck_resolver(hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        entered.set()
        release.wait(timeout=5.0)
        return ("93.184.216.34",)

    def resolve() -> None:
        try:
            pool.resolve(stuck_resolver, "shutdown-waiter.example", 443, 5.0)
        except BaseException as exc:
            failures.append(exc)

    caller = threading.Thread(target=resolve)
    caller.start()
    assert entered.wait(timeout=1.0)
    try:
        pool.shutdown(timeout_seconds=0.01)
        caller.join(timeout=0.2)
        assert not caller.is_alive()
        assert len(failures) == 1
        assert isinstance(failures[0], OutboundUrlError)
        assert "pool is unavailable" in str(failures[0])
    finally:
        release.set()
        caller.join(timeout=1.0)
        pool.shutdown(timeout_seconds=1.0)


class _FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


def test_pinned_http_connect_uses_one_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class SlowSocket(_FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def setblocking(self, _enabled: bool) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    connected_socket = SlowSocket()

    def slow_connect(_address: tuple[str, int], timeout: float) -> Any:
        assert timeout == pytest.approx(0.1)
        clock[0] = 0.11
        return connected_socket

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(outbound_url.socket, "create_connection", slow_connect)
    connection = outbound_url._PinnedHTTPConnection(
        "example.test",
        80,
        "93.184.216.34",
        0.1,
        deadline=0.1,
    )

    with pytest.raises(OutboundUrlError, match="timed out"):
        connection.connect()

    assert connected_socket.closed


def test_pinned_https_handshake_refreshes_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class SlowSocket(_FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def setblocking(self, _enabled: bool) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    raw_socket = SlowSocket()
    wrapped_socket = SlowSocket()

    def connect(_address: tuple[str, int], _timeout: float) -> Any:
        clock[0] = 0.04
        return raw_socket

    class SlowTlsContext:
        @staticmethod
        def wrap_socket(
            _socket: Any,
            *,
            server_hostname: str,
            do_handshake_on_connect: bool,
        ) -> Any:
            assert server_hostname == "example.test"
            assert do_handshake_on_connect is False
            clock[0] = 0.11
            return wrapped_socket

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(outbound_url.socket, "create_connection", connect)
    connection = outbound_url._PinnedHTTPSConnection(
        "example.test",
        443,
        "93.184.216.34",
        0.1,
        deadline=0.1,
    )
    connection._pinned_ssl_context = SlowTlsContext()  # type: ignore[assignment]

    with pytest.raises(OutboundUrlError, match="timed out"):
        connection.connect()

    assert raw_socket.closed
    assert wrapped_socket.closed


def test_pinned_https_handshake_cannot_drip_past_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class SlowSocket(_FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def setblocking(self, _enabled: bool) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    class DripHandshakeSocket(SlowSocket):
        def do_handshake(self) -> None:
            raise outbound_url.ssl.SSLWantReadError()

    raw_socket = SlowSocket()
    wrapped_socket = DripHandshakeSocket()

    class DripTlsContext:
        @staticmethod
        def wrap_socket(
            _socket: Any,
            *,
            server_hostname: str,
            do_handshake_on_connect: bool,
        ) -> Any:
            assert server_hostname == "example.test"
            assert do_handshake_on_connect is False
            return wrapped_socket

    def drip_select(
        read_wait: list[Any],
        _write_wait: list[Any],
        _error_wait: list[Any],
        timeout: float,
    ) -> tuple[list[Any], list[Any], list[Any]]:
        assert timeout <= 0.1
        clock[0] += 0.04
        return read_wait, [], []

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(outbound_url.socket, "create_connection", lambda *_args, **_kwargs: raw_socket)
    monkeypatch.setattr(outbound_url._outbound_deadline.select, "select", drip_select)
    connection = outbound_url._PinnedHTTPSConnection(
        "example.test",
        443,
        "93.184.216.34",
        0.1,
        deadline=0.1,
    )
    connection._pinned_ssl_context = DripTlsContext()  # type: ignore[assignment]

    with pytest.raises(OutboundUrlError, match="timed out"):
        connection.connect()

    assert clock[0] == pytest.approx(0.12)
    assert raw_socket.closed
    assert wrapped_socket.closed


def test_pinned_request_body_send_refreshes_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class SlowSendSocket(_FakeSocket):
        def sendall(self, _payload: Any) -> None:
            clock[0] += 0.06

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    connection = outbound_url._PinnedHTTPConnection(
        "example.test",
        80,
        "93.184.216.34",
        0.05,
        deadline=0.05,
    )
    connection.sock = SlowSendSocket()  # type: ignore[assignment]

    connection.send(b"headers")
    with pytest.raises(OutboundUrlError, match="timed out"):
        connection.send(b"body")


def test_pinned_https_send_loop_cannot_drip_past_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class DripSendSocket(_FakeSocket):
        def send(self, _payload: Any, _flags: int = 0) -> int:
            clock[0] += 0.04
            return 1

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    connection = outbound_url._PinnedHTTPSConnection(
        "example.test",
        443,
        "93.184.216.34",
        0.1,
        deadline=0.1,
    )
    connection.sock = outbound_url._outbound_deadline._DeadlineSocket(
        DripSendSocket(),
        0.1,
        outbound_url._remaining_seconds,
    )  # type: ignore[assignment]

    with pytest.raises(OutboundUrlError, match="timed out"):
        connection.send(b"request-body")

    assert clock[0] == pytest.approx(0.12)


def test_response_status_line_cannot_drip_past_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class DripRaw(io.RawIOBase):
        def __init__(self) -> None:
            super().__init__()
            self.payload = bytearray(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: Any) -> int:
            if not self.payload:
                return 0
            clock[0] += 0.04
            buffer[0] = self.payload.pop(0)
            return 1

    class DripSocket(_FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.raw = DripRaw()

        def send(self, payload: Any, _flags: int = 0) -> int:
            return len(payload)

        def makefile(self, *_args: Any, **_kwargs: Any) -> Any:
            return self.raw

        def close(self) -> None:
            pass

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    connection = outbound_url._PinnedHTTPConnection(
        "example.test",
        80,
        "93.184.216.34",
        0.1,
        deadline=0.1,
    )
    connection.sock = outbound_url._outbound_deadline._DeadlineSocket(
        DripSocket(),
        0.1,
        outbound_url._remaining_seconds,
    )  # type: ignore[assignment]
    connection.request("GET", "/")

    with pytest.raises(OutboundUrlError, match="timed out"):
        connection.getresponse()

    assert clock[0] == pytest.approx(0.12)
    connection.close()


def test_chunk_trailer_cannot_drip_past_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class TrailerDripRaw(io.RawIOBase):
        def __init__(self) -> None:
            super().__init__()
            self.headers = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            self.trailer = bytearray(b"0\r\nX-Test: value\r\n\r\n")

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: Any) -> int:
            if self.headers:
                payload = self.headers
                self.headers = b""
                buffer[: len(payload)] = payload
                return len(payload)
            if not self.trailer:
                return 0
            clock[0] += 0.04
            buffer[0] = self.trailer.pop(0)
            return 1

    class TrailerDripSocket(_FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.raw = TrailerDripRaw()

        def send(self, payload: Any, _flags: int = 0) -> int:
            return len(payload)

        def makefile(self, *_args: Any, **_kwargs: Any) -> Any:
            return self.raw

        def close(self) -> None:
            pass

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    connection = outbound_url._PinnedHTTPConnection(
        "example.test",
        80,
        "93.184.216.34",
        0.15,
        deadline=0.15,
    )
    connection.sock = outbound_url._outbound_deadline._DeadlineSocket(
        TrailerDripSocket(),
        0.15,
        outbound_url._remaining_seconds,
    )  # type: ignore[assignment]
    connection.request("GET", "/")
    response = connection.getresponse()
    try:
        with pytest.raises(OutboundUrlError, match="timed out"):
            response.read()
    finally:
        response.close()
        connection.close()

    assert clock[0] == pytest.approx(0.16)


def test_request_rejects_expired_deadline_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    connection = _FakeConnection(_FakeResponse(b"ok"))
    resolved = ResolvedOutboundUrl(
        parsed=outbound_url.urlsplit("https://service.example/data"),
        hostname="service.example",
        port=443,
        addresses=("93.184.216.34",),
    )

    def slow_factory(
        _resolved: ResolvedOutboundUrl,
        _timeout: float,
    ) -> _FakeConnection:
        clock[0] = 0.11
        return connection

    monkeypatch.setattr(outbound_url.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        outbound_url,
        "validate_outbound_url",
        lambda *_args, **_kwargs: resolved,
    )

    with pytest.raises(OutboundUrlError, match="timed out"):
        request_outbound_bytes(
            "https://service.example/data",
            policy=OutboundUrlPolicy(timeout_seconds=0.1),
            connection_factory=slow_factory,
        )

    assert connection.requests == []
    assert connection.closed is True


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


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"Host": "127.0.0.1"}, "Host header"),
        ({"host": "metadata.internal"}, "Host header"),
        ({"Bad\rName": "value"}, "header name"),
        ({"X-Test": "value\nInjected: yes"}, "header value"),
        ({"X-Test": "value\twith-tab"}, "header value"),
        ({"X-Test": "snowman-☃"}, "header value"),
    ],
)
def test_request_rejects_host_override_and_invalid_headers_before_dns(
    headers: Mapping[str, str],
    message: str,
) -> None:
    resolver_called = False

    def resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        nonlocal resolver_called
        resolver_called = True
        return ("93.184.216.34",)

    with pytest.raises(OutboundUrlError, match=message):
        request_outbound_bytes(
            "https://service.example/data",
            headers=headers,
            resolver=resolver,
        )

    assert resolver_called is False


def test_request_wraps_connection_factory_creation_failure() -> None:
    def failed_factory(
        _resolved: ResolvedOutboundUrl,
        _timeout: float,
    ) -> _FakeConnection:
        raise RuntimeError("connection setup failed")

    with pytest.raises(OutboundUrlError, match="outbound HTTP request failed"):
        request_outbound_bytes(
            "https://service.example/data",
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=failed_factory,
        )


def test_response_close_failure_does_not_mask_request_error_or_skip_connection_close() -> None:
    class RaisingCloseResponse(_FakeResponse):
        def close(self) -> None:
            self.closed = True
            raise OSError("response close failed")

    response = RaisingCloseResponse(b"", status=302)
    connection = _FakeConnection(response)

    with pytest.raises(OutboundUrlError, match="redirect"):
        request_outbound_bytes(
            "https://service.example/data",
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=_fake_connection_factory(connection, {}),
        )

    assert response.closed is True
    assert connection.closed is True


def test_response_close_failure_after_success_is_wrapped_and_connection_still_closes() -> None:
    class RaisingCloseResponse(_FakeResponse):
        def close(self) -> None:
            self.closed = True
            raise OSError("response close failed")

    response = RaisingCloseResponse(b"ok")
    connection = _FakeConnection(response)

    with pytest.raises(OutboundUrlError, match="cleanup failed"):
        request_outbound_bytes(
            "https://service.example/data",
            resolver=_resolve_to("93.184.216.34"),
            connection_factory=_fake_connection_factory(connection, {}),
        )

    assert response.closed is True
    assert connection.closed is True


def test_successful_request_does_not_inherit_callers_active_exception_for_cleanup() -> None:
    class RaisingCloseResponse(_FakeResponse):
        def close(self) -> None:
            self.closed = True
            raise OSError("response close failed")

    response = RaisingCloseResponse(b"ok")
    connection = _FakeConnection(response)

    try:
        raise RuntimeError("outer handled exception")
    except RuntimeError:
        with pytest.raises(OutboundUrlError, match="cleanup failed"):
            request_outbound_bytes(
                "https://service.example/data",
                resolver=_resolve_to("93.184.216.34"),
                connection_factory=_fake_connection_factory(connection, {}),
            )

    assert response.closed is True
    assert connection.closed is True


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
