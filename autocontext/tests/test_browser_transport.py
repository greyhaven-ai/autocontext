from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve

from autocontext.integrations.browser import chrome_cdp_transport as transport_module
from autocontext.integrations.browser.chrome_cdp_transport import (
    ChromeCdpTransportError,
    ChromeCdpWebSocketTransport,
)


@pytest.mark.asyncio
async def test_websocket_transport_disables_proxy_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    websocket = object()

    async def connect(url: str, **kwargs: object) -> object:
        captured.update({"url": url, **kwargs})
        return websocket

    monkeypatch.setattr(
        transport_module,
        "import_module",
        lambda _name: SimpleNamespace(connect=connect),
    )

    result = await transport_module._connect_websocket(
        "ws://127.0.0.1:9222/devtools/page/1",
        open_timeout=2.0,
        max_size=None,
    )

    assert result is websocket
    assert captured == {
        "url": "ws://127.0.0.1:9222/devtools/page/1",
        "open_timeout": 2.0,
        "max_size": None,
        "proxy": None,
    }


@pytest.mark.asyncio
async def test_websocket_transport_round_trips_cdp_commands() -> None:
    received: list[dict[str, object]] = []

    async def handler(websocket) -> None:  # type: ignore[no-untyped-def]
        message = json.loads(await websocket.recv())
        received.append(message)
        await websocket.send(
            json.dumps({
                "id": message["id"],
                "result": {
                    "product": "Chrome",
                    "echoMethod": message["method"],
                    "echoParams": message["params"],
                },
            }),
        )
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = int(server.sockets[0].getsockname()[1])
        transport = ChromeCdpWebSocketTransport(f"ws://127.0.0.1:{port}/devtools/page/1")

        response = await transport.send("Browser.getVersion", {"verbose": True})
        await transport.close()

    assert received == [
        {
            "id": 1,
            "method": "Browser.getVersion",
            "params": {"verbose": True},
        }
    ]
    assert response["result"] == {
        "product": "Chrome",
        "echoMethod": "Browser.getVersion",
        "echoParams": {"verbose": True},
    }


@pytest.mark.asyncio
async def test_websocket_transport_raises_on_cdp_error() -> None:
    async def handler(websocket) -> None:  # type: ignore[no-untyped-def]
        message = json.loads(await websocket.recv())
        await websocket.send(
            json.dumps({
                "id": message["id"],
                "error": {
                    "message": "domain blocked",
                },
            }),
        )
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = int(server.sockets[0].getsockname()[1])
        transport = ChromeCdpWebSocketTransport(f"ws://127.0.0.1:{port}/devtools/page/1")
        with pytest.raises(ChromeCdpTransportError, match="domain blocked"):
            await transport.send("Page.navigate", {"url": "https://blocked.example"})
        await transport.close()
