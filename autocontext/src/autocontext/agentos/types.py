"""agentOS integration types (AC-517).

Port types that define the boundary between autocontext's session
domain and agentOS's VM runtime.

The runtime port is a Protocol — no direct dependency on
@rivet-dev/agent-os-core. Python side defines the contract;
TS side provides the primary implementation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

DEFAULT_SANDBOX_KEYWORDS = [
    "browser",
    "playwright",
    "puppeteer",
    "selenium",
    "dev server",
    "port",
    "localhost",
    "gui",
    "native build",
    "docker",
    "container",
]


@runtime_checkable
class AgentOsRuntimePort(Protocol):
    """Port interface for agentOS runtime.

    This is the ONLY surface autocontext depends on.
    Implementors can use real AgentOs or a stub.
    """

    async def create_session(self, agent_type: str) -> dict[str, Any]: ...
    async def prompt(self, session_id: str, prompt: str) -> None: ...
    async def close_session(self, session_id: str) -> None: ...
    async def dispose(self) -> None: ...


class AgentOsPermissions(BaseModel):
    """Security permissions for the agentOS VM."""

    network: bool = False
    filesystem: str = "readonly"  # "none" | "readonly" | "readwrite"
    processes: bool = False
    max_memory_mb: int = 512

    model_config = {"frozen": True}


class AgentOsConfig(BaseModel):
    """Configuration for optional agentOS integration."""

    enabled: bool = False
    agent_type: str = "pi"
    workspace_path: str = ""
    permissions: AgentOsPermissions = Field(default_factory=AgentOsPermissions)
    sandbox_escalation_keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_SANDBOX_KEYWORDS))

    model_config = {"frozen": True}

    def needs_sandbox(self, task_description: str) -> bool:
        """Heuristic: does this task need a full sandbox instead of agentOS?"""
        lower = task_description.lower()
        return any(kw in lower for kw in self.sandbox_escalation_keywords)
