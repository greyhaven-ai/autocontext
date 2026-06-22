"""Debugger target discovery for Chrome CDP runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol, TypeAlias, cast, runtime_checkable

from autocontext.integrations.browser.contract.models import BrowserSessionConfig
from autocontext.integrations.browser.policy import evaluate_browser_action_policy

FetchJson: TypeAlias = Callable[[str], Awaitable[object]]


class ChromeCdpDiscoveryError(RuntimeError):
    """Raised when debugger target discovery fails or yields no safe target."""


@dataclass(frozen=True, slots=True)
class ChromeCdpTarget:
    target_id: str
    target_type: str
    title: str
    url: str
    websocket_debugger_url: str


@runtime_checkable
class ChromeCdpTargetDiscoveryPort(Protocol):
    async def resolve_websocket_url(
        self,
        config: BrowserSessionConfig,
        *,
        preferred_url: str | None = None,
    ) -> str: ...


class ChromeCdpTargetDiscovery(ChromeCdpTargetDiscoveryPort):
    """Fetch and select attachable CDP targets from a debugger endpoint."""

    def __init__(
        self,
        debugger_url: str,
        *,
        fetch_json: FetchJson | None = None,
    ) -> None:
        self.debugger_url = debugger_url.rstrip("/")
        self.fetch_json = fetch_json or _fetch_json

    async def list_targets(self) -> list[ChromeCdpTarget]:
        payload = await self.fetch_json(f"{self.debugger_url}/json/list")
        if not isinstance(payload, list):
            raise ChromeCdpDiscoveryError("Debugger target discovery expected a JSON array from /json/list")
        targets: list[ChromeCdpTarget] = []
        for item in payload:
            target = _parse_target(item)
            if target is not None:
                targets.append(target)
        return targets

    async def resolve_websocket_url(
        self,
        config: BrowserSessionConfig,
        *,
        preferred_url: str | None = None,
    ) -> str:
        target = select_chrome_cdp_target(
            await self.list_targets(),
            config,
            preferred_url=preferred_url,
        )
        return target.websocket_debugger_url


def select_chrome_cdp_target(
    targets: Sequence[ChromeCdpTarget],
    config: BrowserSessionConfig,
    *,
    preferred_url: str | None = None,
) -> ChromeCdpTarget:
    attachable_targets = [target for target in targets if target.target_type == "page" and target.websocket_debugger_url]
    if preferred_url:
        preferred_target = next((target for target in attachable_targets if target.url == preferred_url), None)
        if preferred_target is not None:
            if _is_target_allowed(config, preferred_target.url):
                return preferred_target
            raise ChromeCdpDiscoveryError(
                f"Preferred debugger target is not allowed by browser policy: {preferred_url}",
            )

    allowed_targets = [target for target in attachable_targets if _is_target_allowed(config, target.url)]
    if allowed_targets:
        return allowed_targets[0]
    if not attachable_targets:
        raise ChromeCdpDiscoveryError("No attachable page targets were advertised by the debugger")
    if preferred_url:
        raise ChromeCdpDiscoveryError(f"Preferred debugger target was not found: {preferred_url}")
    raise ChromeCdpDiscoveryError("No debugger targets matched the browser allowlist")


async def _fetch_json(url: str) -> object:
    try:
        httpx = import_module("httpx")
    except ImportError as exc:
        raise ChromeCdpDiscoveryError(
            "Chrome CDP target discovery requires the optional httpx dependency. "
            "Install it with `pip install 'autocontext[browser]'`."
        ) from exc
    async_client = cast(Any, httpx).AsyncClient
    async with async_client() as client:
        response = await client.get(url)
    response.raise_for_status()
    return response.json()


def _parse_target(payload: object) -> ChromeCdpTarget | None:
    if not isinstance(payload, dict):
        return None
    target_id = payload.get("id")
    target_type = payload.get("type")
    title = payload.get("title")
    url = payload.get("url")
    websocket_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(target_id, str) or not isinstance(target_type, str):
        return None
    return ChromeCdpTarget(
        target_id=target_id,
        target_type=target_type,
        title=title if isinstance(title, str) else "",
        url=url if isinstance(url, str) else "",
        websocket_debugger_url=websocket_url if isinstance(websocket_url, str) else "",
    )


def _is_target_allowed(config: BrowserSessionConfig, url: str) -> bool:
    decision = evaluate_browser_action_policy(
        config,
        {
            "schemaVersion": "1.0",
            "actionId": "act_discovery_probe",
            "sessionId": "session_discovery",
            "timestamp": datetime.now(UTC),
            "type": "navigate",
            "params": {"url": url},
        },
    )
    return decision.allowed


__all__ = [
    "ChromeCdpDiscoveryError",
    "ChromeCdpTarget",
    "ChromeCdpTargetDiscovery",
    "ChromeCdpTargetDiscoveryPort",
    "select_chrome_cdp_target",
]
