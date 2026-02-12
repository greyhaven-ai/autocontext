"""LLM client using Claude Agent SDK with native tool use."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from mts.agents.llm_client import LanguageModelClient, ModelResponse
from mts.agents.types import RoleUsage

# Per-role tool permissions
ROLE_TOOL_CONFIG: dict[str, list[str]] = {
    "competitor": ["Read", "Glob", "Grep"],
    "analyst": ["Read", "Glob", "Grep", "Bash"],
    "coach": ["Read", "Glob", "Grep"],
    "architect": ["Read", "Glob", "Grep", "Bash"],
    "translator": [],
    "curator": ["Read", "Glob", "Grep"],
}


@dataclass(slots=True)
class AgentSdkConfig:
    """Configuration for Agent SDK client."""

    working_directory: str = ""
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
        started = time.perf_counter()
        result_text = asyncio.run(self._query(prompt, model, max_tokens, role))
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = RoleUsage(
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(result_text) // 4),
            latency_ms=elapsed,
            model=model,
        )
        return ModelResponse(text=result_text, usage=usage)

    async def _query(self, prompt: str, model: str, max_tokens: int, role: str) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

        tool_list = ROLE_TOOL_CONFIG.get(role, ROLE_TOOL_CONFIG["competitor"])
        options = ClaudeAgentOptions(
            model=model,
            max_tokens=max_tokens,
            allowed_tools=tool_list,
            permission_mode="bypassPermissions",
        )

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if hasattr(message, "result"):
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
        combined = system + "\n\n" + "\n\n".join(f"[{m['role']}]: {m['content']}" for m in messages)
        return self.generate(
            model=model,
            prompt=combined,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
        )
