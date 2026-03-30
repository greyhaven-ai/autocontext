"""LLM client using Claude Agent SDK with native tool use."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from autocontext.agents.llm_client import LanguageModelClient, ModelResponse
from autocontext.agents.types import RoleUsage

# Per-role tool permissions
ROLE_TOOL_CONFIG: dict[str, list[str]] = {
    "competitor": ["Read", "Glob", "Grep"],
    "analyst": ["Read", "Glob", "Grep", "Bash"],
    "coach": ["Read", "Glob", "Grep"],
    "architect": ["Read", "Glob", "Grep", "Bash"],
    "translator": [],
    "curator": ["Read", "Glob", "Grep"],
}

# Map full model IDs to the short names the Agent SDK expects
_MODEL_SHORT_NAMES: dict[str, str] = {
    "claude-opus-4-6": "opus",
    "claude-sonnet-4-5-20250929": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
}


def _resolve_model(model: str) -> str:
    """Convert a full model ID to the short name the Agent SDK expects."""
    if model in _MODEL_SHORT_NAMES:
        return _MODEL_SHORT_NAMES[model]
    # Already a short name or unknown — pass through
    for short in ("opus", "sonnet", "haiku"):
        if short in model:
            return short
    return "sonnet"  # safe default


@dataclass(slots=True)
class AgentSdkConfig:
    """Configuration for Agent SDK client."""

    cwd: str = ""
    connect_mcp_server: bool = False


class AgentSdkClient(LanguageModelClient):
    """LLM client backed by claude_agent_sdk.query()."""

    def __init__(self, config: AgentSdkConfig | None = None) -> None:
        self._config = config or AgentSdkConfig()

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "competitor",
    ) -> ModelResponse:
        del max_tokens, temperature  # Agent SDK manages these internally
        started = time.perf_counter()
        result_text = asyncio.run(self._query(prompt, model, role))
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = RoleUsage(
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(result_text) // 4),
            latency_ms=elapsed,
            model=model,
        )
        return ModelResponse(text=result_text, usage=usage)

    async def _query(self, prompt: str, model: str, role: str, system_prompt: str = "") -> str:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        tool_list = ROLE_TOOL_CONFIG.get(role, ROLE_TOOL_CONFIG["competitor"])
        options = ClaudeAgentOptions(
            model=_resolve_model(model),
            allowed_tools=tool_list,
            permission_mode="bypassPermissions",
            max_turns=25,
        )
        if system_prompt:
            options.system_prompt = system_prompt
        if self._config.cwd:
            options.cwd = self._config.cwd

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage) and message.result:
                result_text = message.result
        return result_text.strip()

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "analyst",
    ) -> ModelResponse:
        """Agent SDK handles multi-turn natively via its tool loop."""
        del max_tokens, temperature  # Agent SDK manages these internally
        last_user_msg = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_msg = m["content"]
                break
        prompt = last_user_msg or "\n\n".join(f"[{m['role']}]: {m['content']}" for m in messages)
        started = time.perf_counter()
        result_text = asyncio.run(self._query(prompt, model, role, system_prompt=system))
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = RoleUsage(
            input_tokens=max(1, len(system + prompt) // 4),
            output_tokens=max(1, len(result_text) // 4),
            latency_ms=elapsed,
            model=model,
        )
        return ModelResponse(text=result_text, usage=usage)
