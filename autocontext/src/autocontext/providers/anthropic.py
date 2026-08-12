"""Anthropic provider implementation."""

from __future__ import annotations

from typing import Any

import anthropic

from autocontext.providers.base import (
    CompletionResult,
    LLMProvider,
    OutputSchema,
    ProviderError,
    ThinkingUnsupportedError,
)
from autocontext.providers.thinking import (
    DEEP_THINK_DESCRIPTION,
    DEEP_THINK_PARAMETERS,
    DEEP_THINK_TOOL_NAME,
    deep_think_acknowledgement,
    extract_deep_thought,
    with_deep_think_instruction,
)
from autocontext.providers.token_caps import clamp_output_tokens

_DEEP_THINK_TOOL: dict[str, Any] = {
    "name": DEEP_THINK_TOOL_NAME,
    "description": DEEP_THINK_DESCRIPTION,
    "input_schema": DEEP_THINK_PARAMETERS,
}


class AnthropicProvider(LLMProvider):
    """LLM provider using the Anthropic API (Claude models)."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model_name: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._default_model = default_model_name

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
    ) -> CompletionResult:
        del output_schema  # AC-913: Anthropic structured output not wired yet; reported unconstrained
        model_id = model or self._default_model
        try:
            response = self._client.messages.create(
                model=model_id,
                max_tokens=clamp_output_tokens(max_tokens, model_id),
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            raise ProviderError(f"Anthropic API error: {exc}") from exc

        text = ""
        if response.content:
            block = response.content[0]
            if hasattr(block, "text"):
                text = block.text
        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return CompletionResult(
            text=text,
            model=model_id,
            usage=usage,
            stop_reason=getattr(response, "stop_reason", None),
        )

    def complete_with_thinking(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
        reasoning_effort: str = "medium",
        max_tool_turns: int = 8,
    ) -> CompletionResult:
        """Collect ordered scratchpad entries with Anthropic client tools."""
        del output_schema, reasoning_effort  # Neither maps to this Messages API path.
        if max_tool_turns < 1:
            raise ValueError("max_tool_turns must be at least 1")

        model_id = model or self._default_model
        messages: Any = [{"role": "user", "content": user_prompt}]
        thinking_stream: list[str] = []
        total_usage = {"input_tokens": 0, "output_tokens": 0}

        # ``max_tool_turns`` bounds tool-bearing responses, not all requests;
        # allow one final request after the last permitted scratchpad turn.
        for turn in range(max_tool_turns + 1):
            tool_choice: dict[str, Any]
            if turn == 0:
                tool_choice = {
                    "type": "tool",
                    "name": DEEP_THINK_TOOL_NAME,
                    "disable_parallel_tool_use": True,
                }
            else:
                tool_choice = {"type": "auto", "disable_parallel_tool_use": True}
            try:
                create_message: Any = self._client.messages.create
                response = create_message(
                    model=model_id,
                    max_tokens=clamp_output_tokens(max_tokens, model_id),
                    temperature=temperature,
                    system=with_deep_think_instruction(system_prompt),
                    messages=messages,
                    tools=[_DEEP_THINK_TOOL],
                    tool_choice=tool_choice,
                )
            except anthropic.APIError as exc:
                raise ProviderError(
                    f"Anthropic API error: {exc}",
                    usage=total_usage,
                ) from exc

            usage = getattr(response, "usage", None)
            if usage is not None:
                total_usage["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
                total_usage["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)

            blocks = list(getattr(response, "content", None) or [])
            tool_blocks = [block for block in blocks if getattr(block, "type", "") == "tool_use"]
            if not tool_blocks:
                if turn == 0:
                    raise ThinkingUnsupportedError(
                        "Anthropic API did not honor required deep_think tool choice",
                        usage=total_usage,
                    )
                text = "".join(
                    str(getattr(block, "text", "") or "")
                    for block in blocks
                    if getattr(block, "type", "") == "text"
                )
                return CompletionResult(
                    text=text,
                    model=str(getattr(response, "model", model_id) or model_id),
                    usage=total_usage,
                    stop_reason=getattr(response, "stop_reason", None),
                    thinking_stream=thinking_stream,
                    thinking_tool=DEEP_THINK_TOOL_NAME,
                    thinking_capture="tool",
                )

            if turn == max_tool_turns:
                raise ProviderError(
                    f"Model exceeded {max_tool_turns} deep_think tool turns",
                    usage=total_usage,
                )

            messages.append({"role": "assistant", "content": blocks})
            tool_results: list[dict[str, Any]] = []
            for block in tool_blocks:
                name = str(getattr(block, "name", "") or "")
                if name != DEEP_THINK_TOOL_NAME:
                    raise ProviderError(
                        f"Unexpected thinking tool call: {name or '<missing>'}",
                        usage=total_usage,
                    )
                try:
                    thinking_stream.append(extract_deep_thought(getattr(block, "input", None)))
                except ValueError as exc:
                    raise ProviderError(
                        f"Invalid deep_think arguments: {exc}",
                        usage=total_usage,
                    ) from exc
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": str(getattr(block, "id", "") or ""),
                        "content": deep_think_acknowledgement(len(thinking_stream)),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        raise AssertionError("deep_think loop exhausted without returning or raising")

    def default_model(self) -> str:
        return self._default_model

    @property
    def supports_thinking_stream(self) -> bool:
        return True
