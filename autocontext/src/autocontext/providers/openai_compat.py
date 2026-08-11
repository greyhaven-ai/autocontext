"""OpenAI-compatible provider implementation.

Works with: OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM, any
server that implements the OpenAI chat completions API.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema, ProviderError
from autocontext.providers.thinking import (
    DEEP_THINK_DESCRIPTION,
    DEEP_THINK_PARAMETERS,
    DEEP_THINK_TOOL_NAME,
    deep_think_acknowledgement,
    extract_deep_thought,
    with_deep_think_instruction,
)
from autocontext.providers.token_caps import clamp_output_tokens

logger = logging.getLogger(__name__)

DEEP_THINK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": DEEP_THINK_TOOL_NAME,
        "description": DEEP_THINK_DESCRIPTION,
        "parameters": DEEP_THINK_PARAMETERS,
    },
}

try:
    import openai  # type: ignore[import-not-found]

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


class OpenAICompatibleProvider(LLMProvider):
    """LLM provider for any OpenAI-compatible API endpoint.

    Supports OpenAI, Azure, vLLM, Ollama, and any server implementing
    the ``/v1/chat/completions`` endpoint.

    Args:
        api_key: API key (or ``"ollama"`` for keyless local servers).
        base_url: Base URL for the API (e.g. ``http://localhost:11434/v1``).
        default_model_name: Model to use when none is specified.
        extra_headers: Additional HTTP headers for every request.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model_name: str = "gpt-4o",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not _HAS_OPENAI:
            raise ProviderError("openai package is required for OpenAICompatibleProvider. Install with: pip install openai")

        resolved_key = api_key or os.getenv("OPENAI_API_KEY") or "no-key"
        kwargs: dict[str, Any] = {"api_key": resolved_key}
        if base_url:
            kwargs["base_url"] = base_url
        if extra_headers:
            kwargs["default_headers"] = extra_headers

        self._client = openai.OpenAI(**kwargs)
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
            "temperature": temperature,
            "max_tokens": clamp_output_tokens(max_tokens, model_id),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        constrained = False
        if output_schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.name,
                    "strict": True,
                    "schema": output_schema.schema,
                },
            }
            constrained = True

        response, constrained = self._create_chat_completion(
            request,
            model_id=model_id,
            constrained=constrained,
        )

        choice = response.choices[0] if response.choices else None
        text = choice.message.content or "" if choice else ""

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens or 0,
                "output_tokens": response.usage.completion_tokens or 0,
            }

        return CompletionResult(
            text=text,
            model=model_id,
            usage=usage,
            stop_reason=getattr(choice, "finish_reason", None) if choice else None,
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
        reasoning_effort: str = "none",
        max_tool_turns: int = 8,
    ) -> CompletionResult:
        """Collect an ordered ``deep_think`` scratchpad via function calls.

        The first turn requires the tool, later turns allow either another
        scratchpad entry or the final answer. Tool arguments stay separate from
        response text, and exceeding the bounded tool loop fails closed rather
        than returning an incomplete answer as a successful trace.
        """
        if max_tool_turns < 1:
            raise ValueError("max_tool_turns must be at least 1")

        model_id = model or self._default_model
        system_with_tool = with_deep_think_instruction(system_prompt)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_with_tool},
            {"role": "user", "content": user_prompt},
        ]
        thinking_stream: list[str] = []
        total_usage = {"input_tokens": 0, "output_tokens": 0}
        constrained = output_schema is not None
        reasoning_effort_supported = True

        for turn in range(max_tool_turns):
            request: dict[str, Any] = {
                "model": model_id,
                "temperature": temperature,
                "max_tokens": clamp_output_tokens(max_tokens, model_id),
                "messages": messages,
                "tools": [DEEP_THINK_TOOL],
                "tool_choice": "required" if turn == 0 else "auto",
                "parallel_tool_calls": False,
            }
            if reasoning_effort_supported:
                request["reasoning_effort"] = reasoning_effort
            if constrained and output_schema is not None:
                request["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": output_schema.name,
                        "strict": True,
                        "schema": output_schema.schema,
                    },
                }

            response, constrained = self._create_chat_completion(
                request,
                model_id=model_id,
                constrained=constrained,
            )
            reasoning_effort_supported = "reasoning_effort" in request
            _add_usage(total_usage, response)
            choice = response.choices[0] if response.choices else None
            if choice is None:
                raise ProviderError("OpenAI-compatible API returned no completion choices")
            message = choice.message
            tool_calls = list(getattr(message, "tool_calls", None) or [])

            if not tool_calls:
                if turn == 0:
                    raise ProviderError("OpenAI-compatible endpoint did not honor required deep_think tool choice")
                return CompletionResult(
                    text=getattr(message, "content", None) or "",
                    model=model_id,
                    usage=total_usage,
                    stop_reason=getattr(choice, "finish_reason", None),
                    constrained=constrained,
                    thinking_stream=thinking_stream,
                    thinking_tool=DEEP_THINK_TOOL_NAME,
                    thinking_capture="tool",
                )

            messages.append(_assistant_tool_message(message, tool_calls))
            for call in tool_calls:
                function = getattr(call, "function", None)
                name = getattr(function, "name", "")
                if name != DEEP_THINK_TOOL_NAME:
                    raise ProviderError(f"Unexpected thinking tool call: {name or '<missing>'}")
                arguments = str(getattr(function, "arguments", "") or "")
                thinking_stream.append(extract_deep_thought(arguments))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(getattr(call, "id", "")),
                        "content": deep_think_acknowledgement(len(thinking_stream)),
                    }
                )

        raise ProviderError(f"Model exceeded {max_tool_turns} deep_think tool turns")

    def _create_chat_completion(
        self,
        request: dict[str, Any],
        *,
        model_id: str,
        constrained: bool,
    ) -> tuple[Any, bool]:
        while True:
            try:
                return self._client.chat.completions.create(**request), constrained
            except Exception as exc:
                if constrained and _is_unsupported_response_format_error(exc):
                    # AC-913: a backend without constrained decoding should
                    # still answer, but the result must report the truth.
                    logger.info(
                        "providers.openai_compat: %s rejected response_format; retrying unconstrained",
                        model_id,
                    )
                    request.pop("response_format", None)
                    constrained = False
                    continue
                if "reasoning_effort" in request and _is_unsupported_reasoning_effort_error(exc):
                    # OpenAI-compatible servers frequently support tools but
                    # not OpenAI's native-reasoning control. Keep the required
                    # deep_think contract and negotiate away only this field.
                    logger.info(
                        "providers.openai_compat: %s rejected reasoning_effort; retrying without it",
                        model_id,
                    )
                    request.pop("reasoning_effort", None)
                    continue
                logger.debug("providers.openai_compat: caught Exception", exc_info=True)
                raise ProviderError(f"OpenAI-compatible API error: {exc}") from exc

    def default_model(self) -> str:
        return self._default_model

    @property
    def supports_thinking_stream(self) -> bool:
        return True


def _is_unsupported_response_format_error(exc: Exception) -> bool:
    """Return True only for endpoint rejections of JSON-schema formatting.

    Timeouts, rate limits, authentication failures, and transient server
    errors must propagate to the normal retry layer. Retrying those requests
    without a schema would silently turn an outage into an unconstrained run.
    """
    status_code = getattr(exc, "status_code", None)
    if status_code not in {400, 404, 422}:
        return False
    message = str(exc).lower()
    mentions_schema = "response_format" in message or "json_schema" in message
    rejection = any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "invalid")
    )
    return mentions_schema and rejection


def _is_unsupported_reasoning_effort_error(exc: Exception) -> bool:
    """Identify only endpoint rejections of the optional reasoning control."""
    status_code = getattr(exc, "status_code", None)
    if status_code not in {400, 404, 422}:
        return False
    message = str(exc).lower()
    mentions_field = "reasoning_effort" in message or "reasoning effort" in message
    rejection = any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "invalid")
    )
    return mentions_field and rejection


def _assistant_tool_message(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": getattr(message, "content", None),
        "tool_calls": [
            {
                "id": str(getattr(call, "id", "")),
                "type": str(getattr(call, "type", "function") or "function"),
                "function": {
                    "name": str(getattr(getattr(call, "function", None), "name", "")),
                    "arguments": str(getattr(getattr(call, "function", None), "arguments", "") or ""),
                },
            }
            for call in tool_calls
        ],
    }


def _add_usage(total: dict[str, int], response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    total["input_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
    total["output_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
