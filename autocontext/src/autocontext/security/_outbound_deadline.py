"""Absolute-deadline socket I/O for the pinned outbound HTTP transport."""

from __future__ import annotations

import io
import select
import ssl
from collections.abc import Callable
from typing import Any, cast

_RemainingSeconds = Callable[[float], float]


class _DeadlineRawReader(io.RawIOBase):
    """Refresh the absolute deadline before every underlying socket read."""

    def __init__(
        self,
        raw: Any,
        socket_like: Any,
        deadline: float,
        remaining_seconds: _RemainingSeconds,
    ) -> None:
        super().__init__()
        self._raw = raw
        self._socket = socket_like
        self._deadline = deadline
        self._remaining_seconds = remaining_seconds

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        self._socket.settimeout(self._remaining_seconds(self._deadline))
        read = cast(int | None, self._raw.readinto(buffer))
        self._remaining_seconds(self._deadline)
        return read

    def fileno(self) -> int:
        return cast(int, self._raw.fileno())

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    """Delegate socket operations while enforcing one shared absolute deadline."""

    def __init__(
        self,
        socket_like: Any,
        deadline: float,
        remaining_seconds: _RemainingSeconds,
    ) -> None:
        self._socket = socket_like
        self._deadline = deadline
        self._remaining_seconds = remaining_seconds

    def settimeout(self, timeout: float | None) -> None:
        self._socket.settimeout(timeout)

    def gettimeout(self) -> float | None:
        return cast(float | None, self._socket.gettimeout())

    def fileno(self) -> int:
        return cast(int, self._socket.fileno())

    def close(self) -> None:
        self._socket.close()

    def sendall(self, data: Any, flags: int = 0) -> None:
        view = memoryview(data)
        while view:
            self._socket.settimeout(self._remaining_seconds(self._deadline))
            sent = self._socket.send(view, flags)
            if sent == 0:
                raise OSError("outbound socket closed during request send")
            self._remaining_seconds(self._deadline)
            view = view[sent:]

    def makefile(
        self,
        mode: str = "r",
        buffering: int | None = None,
        *,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if mode != "rb" or encoding is not None or errors is not None or newline is not None:
            raise ValueError("deadline socket only supports binary response readers")
        raw = self._socket.makefile("rb", buffering=0)
        deadline_raw = _DeadlineRawReader(
            raw,
            self._socket,
            self._deadline,
            self._remaining_seconds,
        )
        if buffering == 0:
            return deadline_raw
        buffer_size = io.DEFAULT_BUFFER_SIZE if buffering is None or buffering < 0 else buffering
        return io.BufferedReader(deadline_raw, buffer_size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._socket, name)


def _perform_tls_handshake(
    socket_like: Any,
    *,
    deadline: float,
    remaining_seconds: _RemainingSeconds,
) -> None:
    """Drive a nonblocking TLS handshake within one absolute deadline."""
    while True:
        remaining_seconds(deadline)
        try:
            socket_like.do_handshake()
            remaining_seconds(deadline)
            return
        except ssl.SSLWantReadError:
            _wait_for_socket(
                socket_like,
                readable=True,
                deadline=deadline,
                remaining_seconds=remaining_seconds,
            )
        except ssl.SSLWantWriteError:
            _wait_for_socket(
                socket_like,
                readable=False,
                deadline=deadline,
                remaining_seconds=remaining_seconds,
            )


def _wait_for_socket(
    socket_like: Any,
    *,
    readable: bool,
    deadline: float,
    remaining_seconds: _RemainingSeconds,
) -> None:
    timeout = remaining_seconds(deadline)
    read_wait = [socket_like] if readable else []
    write_wait = [] if readable else [socket_like]
    ready_read, ready_write, _ = select.select(read_wait, write_wait, [], timeout)
    if not ready_read and not ready_write:
        raise TimeoutError("outbound HTTP request timed out")
    remaining_seconds(deadline)
