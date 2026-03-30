"""Generalized OpenClaw agent adapters for external runtimes (AC-318).

Canonical request/response/trace schema for OpenClaw-compatible agents.
Supports local factories, CLI stdin/stdout, and HTTP sidecar adapters.

Key types:
- OpenClawRequest / OpenClawResponse: canonical schema
- OpenClawAdapter: ABC for runtime adapters
- CLIOpenClawAdapter: stdin/stdout JSON for external processes
- HTTPOpenClawAdapter: HTTP sidecar endpoint
- AdapterCapability: compatibility metadata
"""

from __future__ import annotations

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

OPENCLAW_COMPATIBILITY_VERSION = "1.0"


@dataclass(slots=True)
class OpenClawRequest:
    """Canonical request to an OpenClaw-compatible agent."""

    task_prompt: str
    system_prompt: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] | None = None
    timeout: float = 120.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "task_prompt": self.task_prompt,
            "system_prompt": self.system_prompt,
            "context": self.context,
            "schema": self.schema,
            "timeout": self.timeout,
            "metadata": self.metadata,
        })


@dataclass(slots=True)
class OpenClawResponse:
    """Canonical response from an OpenClaw-compatible agent."""

    output: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None
    model: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: str) -> OpenClawResponse:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return cls(output=raw.strip())
        if not isinstance(data, dict):
            return cls(output=str(data))
        tool_calls = data.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            tool_calls = []
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(
            output=data.get("output", ""),
            tool_calls=tool_calls,
            cost_usd=data.get("cost_usd"),
            model=data.get("model"),
            session_id=data.get("session_id"),
            metadata=metadata,
        )


class OpenClawAdapter(ABC):
    """Abstract adapter for OpenClaw-compatible agent runtimes."""

    @property
    @abstractmethod
    def runtime_kind(self) -> str:
        """Adapter type: 'factory', 'cli', 'http'."""

    @abstractmethod
    def execute(self, request: OpenClawRequest) -> OpenClawResponse:
        """Execute a request and return the response."""


@dataclass(slots=True)
class AdapterCapability:
    """Compatibility metadata for an adapter."""

    runtime_kind: str
    compatibility_version: str
    supports_tools: bool = False
    supports_streaming: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_kind": self.runtime_kind,
            "compatibility_version": self.compatibility_version,
            "supports_tools": self.supports_tools,
            "supports_streaming": self.supports_streaming,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdapterCapability:
        return cls(
            runtime_kind=data.get("runtime_kind", ""),
            compatibility_version=data.get("compatibility_version", ""),
            supports_tools=data.get("supports_tools", False),
            supports_streaming=data.get("supports_streaming", False),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class AdapterBackedOpenClawAgent:
    """Expose an OpenClawAdapter through the legacy execute(...) contract."""

    adapter: OpenClawAdapter
    capability: AdapterCapability

    def execute(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request = OpenClawRequest(
            task_prompt=prompt,
            metadata={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "tools": tools or [],
                "runtime_kind": self.capability.runtime_kind,
                "compatibility_version": self.capability.compatibility_version,
            },
        )
        response = self.adapter.execute(request)
        error = response.metadata.get("error")
        if error:
            raise RuntimeError(str(error))
        usage = response.metadata.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        steps = response.metadata.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        tool_calls = response.tool_calls if isinstance(response.tool_calls, list) else []
        return {
            "output": response.output,
            "model": response.model or model,
            "steps": steps,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": int(usage.get("input_tokens", 0)),
                "output_tokens": int(usage.get("output_tokens", 0)),
            },
            "total_duration_ms": int(response.metadata.get("total_duration_ms", 0)),
            "metadata": {
                **response.metadata,
                "runtime_kind": self.capability.runtime_kind,
                "compatibility_version": self.capability.compatibility_version,
            },
        }


class CLIOpenClawAdapter(OpenClawAdapter):
    """Adapter that wraps an external CLI agent via stdin/stdout JSON."""

    def __init__(
        self,
        command: str,
        timeout: float = 120.0,
        extra_args: list[str] | None = None,
    ) -> None:
        self.command = command
        self.timeout = timeout
        self.extra_args = extra_args or []

    @property
    def runtime_kind(self) -> str:
        return "cli"

    def execute(self, request: OpenClawRequest) -> OpenClawResponse:
        args = [self.command, *self.extra_args]
        try:
            result = subprocess.run(
                args,
                input=request.to_json(),
                capture_output=True,
                text=True,
                timeout=request.timeout or self.timeout,
            )
        except subprocess.TimeoutExpired:
            return OpenClawResponse(output="", metadata={"error": "timeout"})
        except FileNotFoundError:
            return OpenClawResponse(output="", metadata={"error": "command_not_found"})

        if result.returncode != 0 and not result.stdout.strip():
            return OpenClawResponse(
                output="",
                metadata={"error": "nonzero_exit", "stderr": result.stderr[:500]},
            )

        return OpenClawResponse.from_json(result.stdout)


def _http_post(
    endpoint: str,
    payload: str,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> Any:
    """HTTP POST helper — thin wrapper for testability."""
    import urllib.request

    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        endpoint,
        data=payload.encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return type("Response", (), {
            "status_code": resp.status,
            "json": lambda: json.loads(body),
        })()


class HTTPOpenClawAdapter(OpenClawAdapter):
    """Adapter that wraps an external HTTP sidecar agent."""

    def __init__(
        self,
        endpoint: str,
        timeout: float = 120.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.headers = headers or {}

    @property
    def runtime_kind(self) -> str:
        return "http"

    def execute(self, request: OpenClawRequest) -> OpenClawResponse:
        try:
            resp = _http_post(
                self.endpoint,
                request.to_json(),
                timeout=request.timeout or self.timeout,
                headers=self.headers,
            )
        except Exception as exc:
            logger.debug("openclaw.adapters: caught Exception", exc_info=True)
            return OpenClawResponse(output="", metadata={"error": str(exc)})

        data = resp.json()
        return OpenClawResponse(
            output=data.get("output", ""),
            tool_calls=data.get("tool_calls", []),
            cost_usd=data.get("cost_usd"),
            model=data.get("model"),
            metadata=data.get("metadata", {}),
        )


def capability_from_settings(
    runtime_kind: str,
    *,
    compatibility_version: str = OPENCLAW_COMPATIBILITY_VERSION,
    metadata: dict[str, Any] | None = None,
) -> AdapterCapability:
    """Build compatibility metadata for the configured OpenClaw runtime."""

    return AdapterCapability(
        runtime_kind=runtime_kind,
        compatibility_version=compatibility_version,
        supports_tools=True,
        supports_streaming=False,
        metadata=metadata or {},
    )
