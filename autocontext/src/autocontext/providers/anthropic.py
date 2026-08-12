"""Anthropic provider implementation."""

from __future__ import annotations

import json
import logging
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
logger = logging.getLogger(__name__)


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
        model_id = model or self._default_model
        request: dict[str, Any] = {
            "model": model_id,
            "max_tokens": clamp_output_tokens(max_tokens, model_id),
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }

        # AC-928. Anthropic has no `response_format`; a forced tool call is the
        # equivalent. Declaring a tool whose input_schema is the role schema and
        # pinning tool_choice to it makes the model emit a validated object in a
        # tool_use block instead of prose.
        constrained = False
        if output_schema is not None:
            request["tools"] = [
                {
                    "name": output_schema.name,
                    "description": "Return the response as structured data matching the schema.",
                    "input_schema": output_schema.schema,
                }
            ]
            request["tool_choice"] = {"type": "tool", "name": output_schema.name}
            constrained = True

        try:
            response = self._client.messages.create(**request)
        except anthropic.APIError as exc:
            raise ProviderError(f"Anthropic API error: {exc}") from exc

        text = _text_from(response, output_schema if constrained else None)
        if constrained and text is None:
            # Forcing the tool did not produce a tool_use block. Rather than
            # claim a constrained result the caller would then hand to a strict
            # parser, fall back to whatever text came back and say it was
            # unconstrained -- the same honesty contract openai_compat keeps
            # when an endpoint rejects response_format.
            logger.warning(
                "providers.anthropic: %s returned no tool_use block for forced tool '%s'; reporting unconstrained",
                model_id,
                output_schema.name if output_schema else "",
            )
            constrained = False
            text = _first_text_block(response)

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return CompletionResult(
            text=text or "",
            model=model_id,
            usage=usage,
            stop_reason=getattr(response, "stop_reason", None),
            constrained=constrained,
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


def _first_text_block(response: Any) -> str:
    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""


def _text_from(response: Any, output_schema: OutputSchema | None) -> str | None:
    """Serialize the response into the text the rest of the loop parses.

    Unconstrained, that is the first text block, unchanged. Constrained, the
    payload arrives as an already-parsed dict on a tool_use block, so it is
    re-serialized to JSON: every caller downstream of this method reads
    ``CompletionResult.text``, and returning an object here would mean changing
    that contract for one provider. Returns None when the forced tool produced
    no tool_use block, which the caller reports as unconstrained.
    """
    if output_schema is None:
        return _first_text_block(response)
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == output_schema.name:
            return json.dumps(block.input)
    return None
