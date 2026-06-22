"""WebSocket transport for Chrome DevTools Protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
from importlib import import_module
from typing import Any, cast


class ChromeCdpTransportError(RuntimeError):
    """Raised when the CDP websocket transport fails or returns an error."""


class ChromeCdpWebSocketTransport:
    """Thin CDP transport that connects to an existing debugger websocket URL."""

    def __init__(
        self,
        websocket_url: str,
        *,
        connect_timeout: float = 5.0,
    ) -> None:
        self.websocket_url = websocket_url
        self.connect_timeout = connect_timeout
        self._websocket: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._closing = False

    async def connect(self) -> None:
        if self._websocket is not None:
            return
        async with self._connect_lock:
            if self._websocket is not None:
                return
            self._closing = False
            self._websocket = await _connect_websocket(
                self.websocket_url,
                open_timeout=self.connect_timeout,
                max_size=None,
            )
            self._reader_task = asyncio.create_task(self._reader_loop())

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.connect()
        websocket = self._websocket
        if websocket is None:
            raise ChromeCdpTransportError("CDP websocket is not connected")

        async with self._send_lock:
            self._next_id += 1
            message_id = self._next_id
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[message_id] = future
            try:
                await websocket.send(json.dumps({
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                }))
            except Exception as exc:
                self._pending.pop(message_id, None)
                raise ChromeCdpTransportError(f"Failed to send CDP message {method}: {exc}") from exc

        return await future

    async def close(self) -> None:
        self._closing = True
        websocket = self._websocket
        reader_task = self._reader_task
        self._websocket = None
        self._reader_task = None
        if websocket is not None:
            await websocket.close()
        if reader_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

    async def _reader_loop(self) -> None:
        failure: ChromeCdpTransportError | None = None
        websocket = self._websocket
        if websocket is None:
            return
        try:
            async for raw_message in websocket:
                payload = self._decode_message(raw_message)
                if payload is None:
                    continue
                message_id = payload.get("id")
                if not isinstance(message_id, int):
                    continue
                future = self._pending.pop(message_id, None)
                if future is None or future.done():
                    continue
                error = payload.get("error")
                if isinstance(error, dict):
                    future.set_exception(ChromeCdpTransportError(_error_message(error)))
                    continue
                future.set_result(payload)
        except Exception as exc:
            failure = ChromeCdpTransportError(f"CDP websocket transport failed: {exc}")
        finally:
            if failure is None and not self._closing:
                failure = ChromeCdpTransportError("CDP websocket closed unexpectedly")
            elif failure is None:
                failure = ChromeCdpTransportError("CDP websocket closed")
            self._fail_pending(failure)
            self._websocket = None
            self._reader_task = None

    def _fail_pending(self, error: ChromeCdpTransportError) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _decode_message(self, raw_message: Any) -> dict[str, Any] | None:
        try:
            if isinstance(raw_message, bytes):
                return cast(dict[str, Any], json.loads(raw_message.decode("utf-8")))
            if isinstance(raw_message, str):
                return cast(dict[str, Any], json.loads(raw_message))
        except json.JSONDecodeError:
            return None
        return None


async def _connect_websocket(websocket_url: str, *, open_timeout: float, max_size: int | None) -> Any:
    try:
        websockets_client = import_module("websockets.asyncio.client")
    except ImportError as exc:
        raise ChromeCdpTransportError(
            "Chrome CDP websocket transport requires the optional websockets dependency. "
            "Install it with `pip install 'autocontext[browser]'`."
        ) from exc
    connect = cast(Any, websockets_client).connect
    return await connect(websocket_url, open_timeout=open_timeout, max_size=max_size)


def _error_message(error: dict[str, Any]) -> str:
    message = error.get("message")
    if isinstance(message, str) and message:
        return message
    return f"CDP error: {json.dumps(error, sort_keys=True)}"

__all__ = ["ChromeCdpTransportError", "ChromeCdpWebSocketTransport"]
