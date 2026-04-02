"""Tests for agentOS adapter types and protocol (AC-517 Python parity).

Python side defines the port interface and config. The actual
AgentOs integration is TS-first, but Python needs the contract
for cross-language orchestration and config management.
"""

from __future__ import annotations

import pytest


class TestAgentOsPermissions:
    def test_defaults(self) -> None:
        from autocontext.agentos.types import AgentOsPermissions

        p = AgentOsPermissions()
        assert p.network is False
        assert p.filesystem == "readonly"
        assert p.max_memory_mb == 512

    def test_overrides(self) -> None:
        from autocontext.agentos.types import AgentOsPermissions

        p = AgentOsPermissions(network=True, filesystem="readwrite", max_memory_mb=1024)
        assert p.network is True
        assert p.filesystem == "readwrite"


class TestAgentOsConfig:
    def test_defaults(self) -> None:
        from autocontext.agentos.types import AgentOsConfig

        c = AgentOsConfig()
        assert c.enabled is False
        assert c.agent_type == "pi"
        assert c.workspace_path == ""

    def test_enabled_config(self) -> None:
        from autocontext.agentos.types import AgentOsConfig, AgentOsPermissions

        c = AgentOsConfig(
            enabled=True,
            agent_type="claude-code",
            workspace_path="/home/user/project",
            permissions=AgentOsPermissions(network=True),
        )
        assert c.enabled is True
        assert c.agent_type == "claude-code"
        assert c.permissions.network is True

    def test_sandbox_escalation_defaults(self) -> None:
        from autocontext.agentos.types import AgentOsConfig

        c = AgentOsConfig()
        assert "browser" in c.sandbox_escalation_keywords
        assert "playwright" in c.sandbox_escalation_keywords

    def test_needs_sandbox(self) -> None:
        from autocontext.agentos.types import AgentOsConfig

        c = AgentOsConfig()
        assert c.needs_sandbox("Run browser tests with Playwright") is True
        assert c.needs_sandbox("Write a utility function") is False
        assert c.needs_sandbox("Start a dev server on port 3000") is True


class TestAgentOsRuntimePort:
    def test_protocol_structural_check(self) -> None:
        from autocontext.agentos.types import AgentOsRuntimePort

        # Verify the protocol is runtime-checkable
        class StubRuntime:
            async def create_session(self, agent_type: str) -> dict:
                return {"session_id": "test"}

            async def prompt(self, session_id: str, prompt: str) -> None:
                pass

            async def close_session(self, session_id: str) -> None:
                pass

            async def dispose(self) -> None:
                pass

        assert isinstance(StubRuntime(), AgentOsRuntimePort)


class TestAgentOsConfigSerde:
    def test_round_trip(self) -> None:
        from autocontext.agentos.types import AgentOsConfig, AgentOsPermissions

        original = AgentOsConfig(
            enabled=True,
            agent_type="pi",
            permissions=AgentOsPermissions(network=True),
        )
        data = original.model_dump()
        restored = AgentOsConfig.model_validate(data)
        assert restored.enabled == original.enabled
        assert restored.permissions.network == original.permissions.network
