"""Anthropic provider implementation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic

from autocontext.offline import require_online
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
        default_model_name: str = "claude-sonnet-5",
        single_dispatch: bool = False,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if single_dispatch:
            kwargs["max_retries"] = 0
        self._client = anthropic.Anthropic(**kwargs)
        self._default_model = default_model_name
        self._single_dispatch = single_dispatch

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
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        request.update(_claude_request_controls(model_id, temperature))

        # AC-928. Anthropic has no `response_format`; a strict, forced tool call
        # is the equivalent. ``strict`` is essential: forcing a non-strict tool
        # only guarantees the channel and tool name, not the input's shape.
        constrained = False
        if output_schema is not None:
            request["tools"] = [
                {
                    "name": output_schema.name,
                    "description": "Return the response as structured data matching the schema.",
                    "input_schema": _anthropic_strict_schema(output_schema.schema),
                    "strict": True,
                }
            ]
            request["tool_choice"] = {"type": "tool", "name": output_schema.name}
            constrained = True

        response, constrained = self._create_message(request, model_id=model_id, constrained=constrained)

        stop_reason = getattr(response, "stop_reason", None)
        text = _first_text_block(response)
        if constrained and output_schema is not None:
            payload = _tool_payload(response, output_schema.name)
            constrained = (
                stop_reason == "tool_use"
                and payload is not None
                and _matches_json_schema(payload, output_schema.schema)
            )
            if constrained:
                text = json.dumps(payload)
            else:
                # A refusal, truncated response, wrong/missing tool call, or
                # invalid payload must never enter the strict parser under a
                # successful constrained flag.
                logger.warning(
                    "providers.anthropic: %s did not return a complete, schema-valid tool '%s'; "
                    "reporting unconstrained (stop_reason=%s)",
                    model_id,
                    output_schema.name,
                    stop_reason,
                )

        return CompletionResult(
            text=text,
            model=model_id,
            usage=_usage_from(response),
            stop_reason=stop_reason,
            constrained=constrained,
        )

    def _create_message(
        self,
        request: dict[str, Any],
        *,
        model_id: str,
        constrained: bool,
    ) -> tuple[Any, bool]:
        """Create one message, degrading only explicit strict-schema rejection."""
        while True:
            try:
                require_online("call the Anthropic API")
                return self._client.messages.create(**request), constrained
            except anthropic.APIError as exc:
                if (
                    not getattr(self, "_single_dispatch", False)
                    and constrained
                    and _is_unsupported_strict_schema_error(exc)
                ):
                    logger.info(
                        "providers.anthropic: %s rejected strict structured output; retrying unconstrained",
                        model_id,
                    )
                    request.pop("tools", None)
                    request.pop("tool_choice", None)
                    constrained = False
                    continue
                raise ProviderError(f"Anthropic API error: {exc}") from exc

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
            request: dict[str, Any] = {
                "model": model_id,
                "max_tokens": clamp_output_tokens(max_tokens, model_id),
                "system": with_deep_think_instruction(system_prompt),
                "messages": messages,
                "tools": [_DEEP_THINK_TOOL],
                "tool_choice": tool_choice,
            }
            request.update(_claude_request_controls(model_id, temperature))
            try:
                require_online("call the Anthropic API")
                create_message: Any = self._client.messages.create
                response = create_message(**request)
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
    def supports_single_dispatch(self) -> bool:
        return getattr(self, "_single_dispatch", False)

    @property
    def supports_thinking_output_schema(self) -> bool:
        """False: ``complete_with_thinking`` discards ``output_schema`` (AC-936).

        Not an oversight. The loop forces the ``deep_think`` tool on the first
        turn, and ``tool_choice`` cannot pin two tools at once, so a forced
        schema tool has nowhere to go. Honoring both means declaring both tools
        and expecting the output tool on the final turn instead of forcing it,
        which is a design change rather than a parameter pass-through.
        """
        return False

    @property
    def supports_thinking_stream(self) -> bool:
        return True


_CLAUDE_5_MODEL = re.compile(r"(?:^|/)claude-(?:sonnet|opus|fable|mythos)-5(?:$|[-:])", re.IGNORECASE)
_CLAUDE_5_CAN_DISABLE_THINKING = re.compile(
    r"(?:^|/)claude-(?:sonnet|opus)-5(?:$|[-:])",
    re.IGNORECASE,
)


def _claude_request_controls(model_id: str, temperature: float) -> dict[str, Any]:
    """Preserve pre-Claude-5 completion semantics without invalid sampling.

    Claude 5 rejects non-default sampling values. Sonnet 5 and Opus 5 also
    enable adaptive thinking when ``thinking`` is omitted, which makes the
    existing output cap cover hidden reasoning as well as visible text. The
    provider's dedicated thinking method already exposes a bounded tool-based
    scratchpad, so both paths keep native thinking off where the model permits
    it. Fable/Mythos 5 cannot disable thinking and therefore receive neither
    the unsupported control nor a sampling field.
    """
    if _CLAUDE_5_MODEL.search(model_id):
        if _CLAUDE_5_CAN_DISABLE_THINKING.search(model_id):
            return {"thinking": {"type": "disabled"}}
        return {}
    return {"temperature": temperature}


def _first_text_block(response: Any) -> str:
    for block in getattr(response, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""


def _tool_payload(response: Any, name: str) -> Any | None:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == name:
            return getattr(block, "input", None)
    return None


def _usage_from(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _is_unsupported_strict_schema_error(exc: Exception) -> bool:
    """Identify only endpoint rejections of strict structured tool use."""
    if getattr(exc, "status_code", None) not in {400, 404, 422}:
        return False
    message = str(exc).lower()
    mentions_feature = any(
        token in message
        for token in ("strict", "input_schema", "input schema", "structured output", "structured tool")
    )
    rejection = any(
        token in message
        for token in ("unsupported", "not supported", "unknown", "unrecognized", "unexpected", "invalid")
    )
    return mentions_feature and rejection


def _anthropic_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Transform JSON Schema to Anthropic's strict structured-output subset.

    Anthropic rejects unsupported validation keywords instead of ignoring them.
    As its SDK's newer ``transform_schema`` helper does, keep those constraints
    in descriptions for generation guidance. The original schema remains the
    authority for the local validation performed before ``constrained=True``.
    This local copy keeps compatibility with the older SDK versions in CI.
    """

    def transform(current: dict[str, Any]) -> dict[str, Any]:
        remaining = dict(current)
        result: dict[str, Any] = {}

        definitions = remaining.pop("$defs", None)
        if isinstance(definitions, dict):
            result["$defs"] = {
                str(name): transform(definition)
                for name, definition in definitions.items()
                if isinstance(definition, dict)
            }

        reference = remaining.pop("$ref", None)
        if isinstance(reference, str):
            result["$ref"] = reference
            return result

        schema_type = remaining.pop("type", None)
        any_of = remaining.pop("anyOf", None)
        one_of = remaining.pop("oneOf", None)
        all_of = remaining.pop("allOf", None)
        if isinstance(any_of, list):
            result["anyOf"] = [transform(item) for item in any_of if isinstance(item, dict)]
        elif isinstance(one_of, list):
            result["anyOf"] = [transform(item) for item in one_of if isinstance(item, dict)]
        elif isinstance(all_of, list):
            result["allOf"] = [transform(item) for item in all_of if isinstance(item, dict)]
        elif isinstance(schema_type, str):
            result["type"] = schema_type
        else:
            raise ValueError("Anthropic structured-output schemas require type, anyOf, oneOf, or allOf")

        enum = remaining.pop("enum", None)
        if isinstance(enum, list):
            result["enum"] = enum
        for annotation in ("description", "title"):
            value = remaining.pop(annotation, None)
            if isinstance(value, str):
                result[annotation] = value

        if schema_type == "object":
            properties = remaining.pop("properties", {})
            if isinstance(properties, dict):
                result["properties"] = {
                    str(name): transform(value)
                    for name, value in properties.items()
                    if isinstance(value, dict)
                }
            remaining.pop("additionalProperties", None)
            result["additionalProperties"] = False
            required = remaining.pop("required", None)
            if isinstance(required, list):
                result["required"] = required
        elif schema_type == "array":
            items = remaining.pop("items", None)
            if isinstance(items, dict):
                result["items"] = transform(items)
            min_items = remaining.pop("minItems", None)
            if min_items in {0, 1}:
                result["minItems"] = min_items
            elif min_items is not None:
                remaining["minItems"] = min_items
        elif schema_type == "string":
            string_format = remaining.pop("format", None)
            if string_format in {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}:
                result["format"] = string_format
            elif string_format is not None:
                remaining["format"] = string_format

        if remaining:
            suffix = "{" + ", ".join(f"{key}: {value}" for key, value in remaining.items()) + "}"
            description = result.get("description")
            result["description"] = f"{description}\n\n{suffix}" if isinstance(description, str) else suffix
        return result

    return transform(schema)


def _matches_json_schema(value: Any, schema: dict[str, Any], *, root: dict[str, Any] | None = None) -> bool:
    """Validate the JSON Schema features emitted by autocontext role models."""
    root = schema if root is None else root
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target = _local_schema_reference(root, reference)
        return target is not None and _matches_json_schema(value, target, root=root)

    if isinstance(schema.get("allOf"), list) and not all(
        isinstance(item, dict) and _matches_json_schema(value, item, root=root) for item in schema["allOf"]
    ):
        return False
    if isinstance(schema.get("anyOf"), list) and not any(
        isinstance(item, dict) and _matches_json_schema(value, item, root=root) for item in schema["anyOf"]
    ):
        return False
    if isinstance(schema.get("oneOf"), list) and sum(
        isinstance(item, dict) and _matches_json_schema(value, item, root=root) for item in schema["oneOf"]
    ) != 1:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not any(_matches_json_schema(value, {**schema, "type": item}, root=root) for item in schema_type):
            return False
    elif isinstance(schema_type, str) and not _matches_json_type(value, schema_type):
        return False

    if schema_type == "object" and isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list) and any(key not in value for key in required):
            return False
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        for key, item in value.items():
            property_schema = properties.get(key)
            if isinstance(property_schema, dict):
                if not _matches_json_schema(item, property_schema, root=root):
                    return False
            elif schema.get("additionalProperties") is False:
                return False
            elif isinstance(schema.get("additionalProperties"), dict) and not _matches_json_schema(
                item, schema["additionalProperties"], root=root
            ):
                return False

    if schema_type == "array" and isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            return False
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            return False
        items = schema.get("items")
        if isinstance(items, dict) and any(not _matches_json_schema(item, items, root=root) for item in value):
            return False

    if schema_type == "string" and isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            return False
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            return False
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return False
    return True


def _matches_json_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def _local_schema_reference(root: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: Any = root
    for segment in reference[2:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None
