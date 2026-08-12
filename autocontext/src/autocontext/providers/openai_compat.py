"""OpenAI-compatible provider implementation.

Works with: OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM, any
server that implements the OpenAI chat completions API.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

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
    deep_think_juice,
    extract_deep_thought,
    with_deep_think_instruction,
)
from autocontext.providers.token_caps import clamp_output_tokens

logger = logging.getLogger(__name__)

def _deep_think_tool(*, strict: bool = True) -> dict[str, Any]:
    function: dict[str, Any] = {
        "name": DEEP_THINK_TOOL_NAME,
        "description": DEEP_THINK_DESCRIPTION,
        "parameters": DEEP_THINK_PARAMETERS,
    }
    if strict:
        function["strict"] = True
    return {"type": "function", "function": function}


DEEP_THINK_TOOL = _deep_think_tool()

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
        default_model_name: str = "gpt-5.6-terra",
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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        request[_output_token_field(model_id)] = clamp_output_tokens(max_tokens, model_id)
        if _supports_temperature(model_id):
            request["temperature"] = temperature
        if _is_gpt_56_plus(model_id):
            # GPT-5.6 otherwise defaults to medium hidden reasoning, consuming
            # latency, spend, and the output budget of this ordinary path.
            request["reasoning_effort"] = "none"
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
        reasoning_effort: str = "medium",
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
        juice = deep_think_juice(reasoning_effort) if _is_gpt_56_plus(model_id) else None
        system_with_tool = with_deep_think_instruction(system_prompt, juice=juice)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_with_tool},
            {"role": "user", "content": user_prompt},
        ]
        thinking_stream: list[str] = []
        total_usage = {"input_tokens": 0, "output_tokens": 0}
        constrained = output_schema is not None
        # The public option controls the external scratchpad budget. Native
        # hidden reasoning is disabled so the captured tool stream is the
        # reasoning surface; compatible gateways may negotiate ``none`` down
        # to their lowest supported level.
        wire_reasoning_effort: str | None = "none"
        strict_tools = True

        # ``max_tool_turns`` bounds tool-bearing responses. A model that uses
        # the final allowed tool turn still needs one more request in which to
        # return its answer.
        for turn in range(max_tool_turns + 1):
            request: dict[str, Any] = {
                "model": model_id,
                "messages": messages,
                "tools": [_deep_think_tool(strict=strict_tools)],
                "tool_choice": "required" if turn == 0 else "auto",
                "parallel_tool_calls": False,
            }
            request[_output_token_field(model_id)] = clamp_output_tokens(max_tokens, model_id)
            if _supports_temperature(model_id):
                request["temperature"] = temperature
            if wire_reasoning_effort is not None:
                request["reasoning_effort"] = wire_reasoning_effort
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
                partial_usage=total_usage,
            )
            effective_effort = request.get("reasoning_effort")
            wire_reasoning_effort = effective_effort if isinstance(effective_effort, str) else None
            strict_tools = _request_uses_strict_tools(request)
            _add_usage(total_usage, response)
            choice = response.choices[0] if response.choices else None
            if choice is None:
                raise ProviderError(
                    "OpenAI-compatible API returned no completion choices",
                    usage=total_usage,
                )
            message = choice.message
            tool_calls = list(getattr(message, "tool_calls", None) or [])

            if not tool_calls:
                if turn == 0:
                    raise ThinkingUnsupportedError(
                        "OpenAI-compatible endpoint did not honor required deep_think tool choice",
                        usage=total_usage,
                    )
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

            if turn == max_tool_turns:
                raise ProviderError(
                    f"Model exceeded {max_tool_turns} deep_think tool turns",
                    usage=total_usage,
                )

            messages.append(_assistant_tool_message(message, tool_calls))
            for call in tool_calls:
                function = getattr(call, "function", None)
                name = getattr(function, "name", "")
                if name != DEEP_THINK_TOOL_NAME:
                    raise ProviderError(
                        f"Unexpected thinking tool call: {name or '<missing>'}",
                        usage=total_usage,
                    )
                arguments = str(getattr(function, "arguments", "") or "")
                try:
                    thinking_stream.append(extract_deep_thought(arguments))
                except ValueError as exc:
                    raise ProviderError(
                        f"Invalid deep_think arguments: {exc}",
                        usage=total_usage,
                    ) from exc
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(getattr(call, "id", "")),
                        "content": deep_think_acknowledgement(len(thinking_stream)),
                    }
                )

        raise AssertionError("deep_think loop exhausted without returning or raising")

    def _create_chat_completion(
        self,
        request: dict[str, Any],
        *,
        model_id: str,
        constrained: bool,
        partial_usage: dict[str, int] | None = None,
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
                if _request_uses_strict_tools(request) and _is_unsupported_strict_tools_error(exc):
                    logger.info(
                        "providers.openai_compat: %s rejected strict tools; retrying with manual validation",
                        model_id,
                    )
                    _disable_strict_tools(request)
                    continue
                if "reasoning_effort" in request and _is_unsupported_reasoning_effort_error(exc):
                    # OpenAI-compatible servers frequently support tools but
                    # not OpenAI's native-reasoning control. Keep the required
                    # deep_think contract and negotiate away only this field.
                    current_effort = str(request["reasoning_effort"])
                    fallback_effort = _lowest_supported_reasoning_effort(exc, current_effort)
                    if fallback_effort is None:
                        logger.info(
                            "providers.openai_compat: %s rejected reasoning_effort; retrying without it",
                            model_id,
                        )
                        request.pop("reasoning_effort", None)
                    else:
                        logger.info(
                            "providers.openai_compat: %s rejected reasoning_effort=%s; retrying with %s",
                            model_id,
                            current_effort,
                            fallback_effort,
                        )
                        request["reasoning_effort"] = fallback_effort
                    continue
                if "max_completion_tokens" in request and _is_unsupported_max_completion_tokens_error(exc):
                    request["max_tokens"] = request.pop("max_completion_tokens")
                    continue
                if _is_unsupported_tools_error(exc):
                    raise ThinkingUnsupportedError(
                        f"OpenAI-compatible endpoint does not support thinking tools: {exc}",
                        usage=partial_usage,
                    ) from exc
                logger.debug("providers.openai_compat: caught Exception", exc_info=True)
                raise ProviderError(
                    f"OpenAI-compatible API error: {exc}",
                    usage=partial_usage,
                ) from exc

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
    mentions_field = (
        "reasoning_effort" in message
        or "reasoning effort" in message
        or re.search(r"(?:valid|supported|allowed) levels?", message) is not None
    )
    rejection = any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "invalid")
    )
    return mentions_field and rejection


def _is_unsupported_max_completion_tokens_error(exc: Exception) -> bool:
    """Identify compatible endpoints that only implement legacy max_tokens."""
    if getattr(exc, "status_code", None) not in {400, 404, 422}:
        return False
    message = str(exc).lower()
    return "max_completion_tokens" in message and any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "unexpected", "invalid")
    )


def _is_unsupported_strict_tools_error(exc: Exception) -> bool:
    """Identify compatible endpoints that reject the optional strict flag."""
    status_code = getattr(exc, "status_code", None)
    if status_code not in {400, 404, 422}:
        return False
    message = str(exc).lower()
    mentions_field = "strict" in message and ("tool" in message or "function" in message)
    rejection = any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "unexpected", "invalid")
    )
    return mentions_field and rejection


def _is_unsupported_tools_error(exc: Exception) -> bool:
    """Identify endpoints that reject function/tool calling altogether."""
    status_code = getattr(exc, "status_code", None)
    if status_code not in {400, 404, 422}:
        return False
    message = str(exc).lower()
    mentions_tools = any(
        token in message
        for token in ("tools", "tool_choice", "tool choice", "function calling", "function_call")
    )
    rejection = any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "invalid")
    )
    return mentions_tools and rejection


def _request_uses_strict_tools(request: dict[str, Any]) -> bool:
    tools = request.get("tools")
    if not isinstance(tools, list) or not tools or not isinstance(tools[0], dict):
        return False
    function = tools[0].get("function")
    return isinstance(function, dict) and function.get("strict") is True


def _disable_strict_tools(request: dict[str, Any]) -> None:
    tools = request.get("tools")
    if not isinstance(tools, list):
        return
    for tool in tools:
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
            tool["function"].pop("strict", None)


_REASONING_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def _lowest_supported_reasoning_effort(exc: Exception, current: str) -> str | None:
    """Return the lowest advertised level after a gateway rejects ``none``."""
    message = str(exc).lower()
    if re.search(r"(?:valid|supported|allowed) levels?", message) is None:
        return None
    advertised = set(re.findall(r"\b(?:none|minimal|low|medium|high|xhigh|max)\b", message))
    advertised.discard(current.lower())
    return next((effort for effort in _REASONING_EFFORT_ORDER if effort in advertised), None)


def _is_gpt_56_plus(model_id: str) -> bool:
    """Identify GPT 5.6+ ids that consume the numeric prompt budget."""
    match = re.search(r"(?:^|[/:-])gpt-(\d+)(?:\.(\d+))?", model_id.lower())
    if match is None:
        return False
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return major > 5 or (major == 5 and minor >= 6)


def _supports_temperature(model_id: str) -> bool:
    """Return False for Gemini generations that ignore sampling controls."""
    return re.search(r"(?:^|/)gemini-3\.(?:5|6)(?:$|[-:])", model_id.lower()) is None


def _output_token_field(model_id: str) -> str:
    return "max_completion_tokens" if _is_gpt_56_plus(model_id) else "max_tokens"


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
