"""OpenAI-compatible provider implementation.

Works with: OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM, any
server that implements the OpenAI chat completions API.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema, ProviderError
from autocontext.providers.token_caps import clamp_output_tokens

logger = logging.getLogger(__name__)

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

        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:
            if not constrained or not _is_unsupported_response_format_error(exc):
                logger.debug("providers.openai_compat: caught Exception", exc_info=True)
                raise ProviderError(f"OpenAI-compatible API error: {exc}") from exc
            # This endpoint does not understand response_format. Retry once
            # without it rather than failing the run: AC-913 requires that a
            # backend with no constrained-decoding support still works. The
            # result is then reported as unconstrained, so a caller can tell
            # "the schema was enforced" from "the schema was requested and
            # silently not applied" -- which is the distinction that makes an
            # unconstrained run visible instead of assumed.
            logger.info(
                "providers.openai_compat: %s rejected response_format; retrying unconstrained",
                model_id,
            )
            request.pop("response_format", None)
            constrained = False
            try:
                response = self._client.chat.completions.create(**request)
            except Exception as retry_exc:
                logger.debug("providers.openai_compat: caught Exception", exc_info=True)
                raise ProviderError(f"OpenAI-compatible API error: {retry_exc}") from retry_exc

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

    def default_model(self) -> str:
        return self._default_model


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
