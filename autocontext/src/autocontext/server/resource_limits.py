"""Resource limits for the interactive control-plane boundary.

These limits are deliberately conservative: interactive commands can trigger
provider calls and long-running generation work, so accepting an unbounded
request or work backlog is both a memory and spend risk.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

from fastapi import WebSocket, WebSocketDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_INTERACTIVE_FRAME_BYTES = 64 * 1024
MAX_INTERACTIVE_TEXT_CHARS = 16 * 1024
MAX_INTERACTIVE_ROLE_CHARS = 128
MAX_INTERACTIVE_SCENARIO_CHARS = 128
MAX_INTERACTIVE_ID_CHARS = 200
MAX_START_RUN_GENERATIONS = 100

MAX_PENDING_EVENT_MESSAGES = 256
MAX_PENDING_WEBSOCKET_MESSAGES = 16
MAX_WEBSOCKET_CONNECTIONS = 32
MAX_CONCURRENT_INTERACTIVE_WORK = 4
MAX_PENDING_INTERACTIVE_WORK = 16

MAX_HTTP_REQUEST_BODY_BYTES = 4 * 1024 * 1024
MAX_HTTP_REQUEST_BODY_CHUNKS = 1024
MAX_REPLAY_FILE_BYTES = 16 * 1024 * 1024
MAX_KNOWLEDGE_FILE_BYTES = 1024 * 1024
MAX_EVENT_STREAM_READ_BYTES = 256 * 1024
MAX_EVENT_STREAM_LINE_BYTES = 64 * 1024


class InteractivePayloadTooLarge(ValueError):
    """Raised after an oversized WebSocket message has been closed."""


class InvalidInteractivePayload(ValueError):
    """Raised when an in-limit WebSocket message is not a JSON object."""


class InteractiveWorkLimitExceeded(RuntimeError):
    """Raised when the bounded interactive work backlog is full."""


class ReplayFileTooLarge(ValueError):
    """Raised before parsing a replay that exceeds the configured byte cap."""


class HttpRequestBodyLimitMiddleware:
    """Buffer at most one bounded HTTP request body before route parsing."""

    def __init__(self, app: ASGIApp, *, max_bytes: int, max_chunks: int = MAX_HTTP_REQUEST_BODY_CHUNKS) -> None:
        if max_bytes < 1 or max_chunks < 1:
            raise ValueError("HTTP request-body limits must be positive")
        self._app = app
        self._max_bytes = max_bytes
        self._max_chunks = max_chunks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_lengths = {value for key, value in scope.get("headers", []) if key.lower() == b"content-length"}
        if len(content_lengths) > 1:
            await _send_http_error(send, 400, "Conflicting Content-Length headers")
            return
        if content_lengths:
            try:
                declared_length = int(next(iter(content_lengths)))
            except ValueError:
                await _send_http_error(send, 400, "Invalid Content-Length header")
                return
            if declared_length < 0:
                await _send_http_error(send, 400, "Invalid Content-Length header")
                return
            if declared_length > self._max_bytes:
                await _send_http_error(send, 413, "Request body exceeds the size limit")
                return

        buffered: list[Message] = []
        received_bytes = 0
        received_chunks = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received_chunks += 1
            received_bytes += len(message.get("body", b""))
            if received_bytes > self._max_bytes or received_chunks > self._max_chunks:
                await _send_http_error(send, 413, "Request body exceeds the size limit")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self._app(scope, replay_receive, send)


class WebSocketConnectionLimitMiddleware:
    """Apply one per-process connection cap across every WebSocket route."""

    def __init__(self, app: ASGIApp, *, max_connections: int) -> None:
        if max_connections < 1:
            raise ValueError("WebSocket connection limit must be positive")
        self._app = app
        self._max_connections = max_connections
        self._active_connections = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket":
            await self._app(scope, receive, send)
            return
        if self._active_connections >= self._max_connections:
            await send({"type": "websocket.close", "code": 1013, "reason": "Connection limit exceeded"})
            return
        self._active_connections += 1
        try:
            await self._app(scope, receive, send)
        finally:
            self._active_connections -= 1


async def _send_http_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


@dataclass(slots=True)
class EventStreamTailState:
    """Cursor state for bounded incremental NDJSON event-log reads."""

    offset: int = 0
    file_identity: tuple[int, int] | None = None
    pending: bytes = b""
    discarding_oversized_line: bool = False


def read_event_stream_lines(
    path: os.PathLike[str],
    state: EventStreamTailState,
    *,
    max_read_bytes: int = MAX_EVENT_STREAM_READ_BYTES,
    max_line_bytes: int = MAX_EVENT_STREAM_LINE_BYTES,
) -> list[str]:
    """Tail bounded UTF-8 lines without rereading the complete event log."""
    if max_read_bytes < 1 or max_line_bytes < 1:
        raise ValueError("event stream limits must be positive")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return []
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("event stream is not a regular file")
        identity = (file_stat.st_dev, file_stat.st_ino)
        if state.file_identity != identity or file_stat.st_size < state.offset:
            state.offset = 0
            state.file_identity = identity
            state.pending = b""
            state.discarding_oversized_line = False
        os.lseek(descriptor, state.offset, os.SEEK_SET)
        chunk = os.read(descriptor, max_read_bytes)
        state.offset += len(chunk)
    finally:
        os.close(descriptor)

    buffer = state.pending + chunk
    state.pending = b""
    lines: list[str] = []
    while buffer:
        newline_index = buffer.find(b"\n")
        if newline_index < 0:
            if not state.discarding_oversized_line:
                if len(buffer) <= max_line_bytes:
                    state.pending = buffer
                else:
                    state.discarding_oversized_line = True
            break
        raw_line = buffer[:newline_index]
        buffer = buffer[newline_index + 1 :]
        if state.discarding_oversized_line:
            state.discarding_oversized_line = False
            continue
        if len(raw_line) > max_line_bytes:
            continue
        try:
            line = raw_line.rstrip(b"\r").decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if line:
            lines.append(line)
    return lines


async def receive_limited_json_object(websocket: WebSocket) -> dict[str, Any]:
    """Receive one JSON object, enforcing the wire byte cap before parsing."""
    message = await websocket.receive()
    message_type = message.get("type")
    if message_type == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=int(message.get("code", 1000)),
            reason=message.get("reason"),
        )
    if message_type != "websocket.receive":
        raise InvalidInteractivePayload("expected a WebSocket data message")

    text = message.get("text")
    binary = message.get("bytes")
    if text is not None:
        # Every Unicode code point needs at least one UTF-8 byte. This cheap
        # check avoids allocating another copy of an already-oversized frame.
        size = len(text) if len(text) > MAX_INTERACTIVE_FRAME_BYTES else len(text.encode("utf-8"))
        raw: str | bytes = text
    elif binary is not None:
        size = len(binary)
        raw = binary
    else:
        raise InvalidInteractivePayload("message has no payload")

    if size > MAX_INTERACTIVE_FRAME_BYTES:
        await websocket.close(code=1009, reason="Message exceeds the interactive frame limit")
        raise InteractivePayloadTooLarge

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InvalidInteractivePayload("message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidInteractivePayload("message must be a JSON object")
    return payload


P = ParamSpec("P")
R = TypeVar("R")


class InteractiveWorkLimiter:
    """Bound active and waiting blocking work across interactive clients.

    Cancellation does not release an active slot early: ``asyncio.to_thread``
    cannot stop its worker thread, so the slot is held until that thread really
    exits.
    """

    def __init__(self, *, max_concurrent: int, max_pending: int) -> None:
        if max_concurrent < 1 or max_pending < 0:
            raise ValueError("interactive work limits must be non-negative")
        self._max_outstanding = max_concurrent + max_pending
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._outstanding = 0

    async def run(self, func: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run blocking work when an active or pending slot is available."""
        if self._outstanding >= self._max_outstanding:
            raise InteractiveWorkLimitExceeded
        self._outstanding += 1

        try:
            await self._semaphore.acquire()
        except BaseException:
            self._outstanding -= 1
            raise

        worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))

        def _release(_worker: asyncio.Task[R]) -> None:
            self._outstanding -= 1
            self._semaphore.release()

        worker.add_done_callback(_release)
        return await asyncio.shield(worker)


def read_limited_json_object(path: os.PathLike[str], *, max_bytes: int = MAX_REPLAY_FILE_BYTES) -> dict[str, Any]:
    """Read a regular, non-symlink JSON file without crossing the byte cap."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("replay is not a regular file")
        if file_stat.st_size > max_bytes:
            raise ReplayFileTooLarge
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)

    if len(raw) > max_bytes:
        raise ReplayFileTooLarge
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("replay payload must be a JSON object")
    return payload
