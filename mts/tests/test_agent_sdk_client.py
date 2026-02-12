"""Tests for Agent SDK client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from mts.agents.agent_sdk_client import ROLE_TOOL_CONFIG, AgentSdkClient


def test_role_tool_config_complete() -> None:
    """All 6 roles have entries in ROLE_TOOL_CONFIG."""
    expected_roles = {"competitor", "analyst", "coach", "architect", "translator", "curator"}
    assert set(ROLE_TOOL_CONFIG.keys()) == expected_roles


def test_analyst_has_bash() -> None:
    """Analyst tools include Bash."""
    assert "Bash" in ROLE_TOOL_CONFIG["analyst"]


def test_translator_no_tools() -> None:
    """Translator tools list is empty."""
    assert ROLE_TOOL_CONFIG["translator"] == []


def test_generate_calls_query() -> None:
    """Mock claude_agent_sdk.query() and verify ModelResponse returned."""
    client = AgentSdkClient()

    mock_message = MagicMock()
    mock_message.result = "test response text"

    async def mock_query(**kwargs):  # type: ignore[no-untyped-def]
        yield mock_message

    with patch("mts.agents.agent_sdk_client.AgentSdkClient._query", new_callable=AsyncMock, return_value="test response text"):
        response = client.generate(
            model="claude-sonnet-4-5-20250929",
            prompt="test prompt",
            max_tokens=1024,
            temperature=0.7,
            role="competitor",
        )
    assert response.text == "test response text"
    assert response.usage.model == "claude-sonnet-4-5-20250929"


def test_generate_passes_role_tools() -> None:
    """Verify allowed_tools matches role config."""
    client = AgentSdkClient()

    captured_tools: list[str] = []

    async def mock_query(prompt: str, model: str, max_tokens: int, role: str) -> str:
        captured_tools.extend(ROLE_TOOL_CONFIG.get(role, []))
        return "result"

    with patch.object(client, "_query", side_effect=mock_query):
        client.generate(
            model="test-model",
            prompt="test",
            max_tokens=1024,
            temperature=0.7,
            role="analyst",
        )
    assert "Bash" in captured_tools
    assert "Read" in captured_tools


def test_generate_multiturn_combines() -> None:
    """System + messages combined into single prompt string."""
    client = AgentSdkClient()
    captured_prompts: list[str] = []

    async def mock_query(prompt: str, model: str, max_tokens: int, role: str) -> str:
        captured_prompts.append(prompt)
        return "result"

    with patch.object(client, "_query", side_effect=mock_query):
        client.generate_multiturn(
            model="test-model",
            system="system instructions",
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            max_tokens=1024,
            temperature=0.7,
            role="analyst",
        )
    assert len(captured_prompts) == 1
    assert "system instructions" in captured_prompts[0]
    assert "[user]: hello" in captured_prompts[0]
    assert "[assistant]: hi" in captured_prompts[0]


def test_usage_estimated() -> None:
    """RoleUsage has reasonable token estimates."""
    client = AgentSdkClient()

    with patch.object(client, "_query", new_callable=AsyncMock, return_value="short response"):
        response = client.generate(
            model="test-model",
            prompt="a" * 400,
            max_tokens=1024,
            temperature=0.7,
            role="competitor",
        )
    assert response.usage.input_tokens >= 1
    assert response.usage.output_tokens >= 1
    assert response.usage.latency_ms >= 0


def test_unknown_role_defaults_to_competitor() -> None:
    """Fallback to competitor tool config for unknown roles."""
    assert ROLE_TOOL_CONFIG.get("unknown_role", ROLE_TOOL_CONFIG["competitor"]) == ROLE_TOOL_CONFIG["competitor"]
