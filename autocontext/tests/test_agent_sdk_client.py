"""Tests for Agent SDK client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from autocontext.agents.agent_sdk_client import ROLE_TOOL_CONFIG, AgentSdkClient, _resolve_model


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

    with patch.object(client, "_query", new_callable=AsyncMock, return_value="test response text"):
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

    async def mock_query(prompt: str, model: str, role: str, system_prompt: str = "") -> str:
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


def test_generate_multiturn_uses_system_prompt() -> None:
    """System prompt passed separately to _query, last user message used as prompt."""
    client = AgentSdkClient()
    captured_args: list[dict[str, str]] = []

    async def mock_query(prompt: str, model: str, role: str, system_prompt: str = "") -> str:
        captured_args.append({"prompt": prompt, "system_prompt": system_prompt})
        return "result"

    with patch.object(client, "_query", side_effect=mock_query):
        client.generate_multiturn(
            model="test-model",
            system="system instructions",
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "final question"},
            ],
            max_tokens=1024,
            temperature=0.7,
            role="analyst",
        )
    assert len(captured_args) == 1
    assert captured_args[0]["system_prompt"] == "system instructions"
    assert captured_args[0]["prompt"] == "final question"


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


def test_resolve_model_full_ids() -> None:
    """Full model IDs are mapped to short names."""
    assert _resolve_model("claude-opus-4-6") == "opus"
    assert _resolve_model("claude-sonnet-4-5-20250929") == "sonnet"
    assert _resolve_model("claude-haiku-4-5-20251001") == "haiku"


def test_resolve_model_short_names() -> None:
    """Short names and substrings resolve correctly."""
    assert _resolve_model("sonnet") == "sonnet"
    assert _resolve_model("opus") == "opus"
    assert _resolve_model("haiku") == "haiku"
    # Unknown falls back to sonnet
    assert _resolve_model("unknown-model") == "sonnet"
