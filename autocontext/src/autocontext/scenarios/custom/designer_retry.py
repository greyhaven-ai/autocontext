"""AC-575 — shared parse-retry helper for custom scenario designers.

All ``design_X`` entry points under ``autocontext.scenarios.custom`` share the
same shape: call ``llm_fn(system, user)``, then pass the response through
``parse_X_spec``. When the LLM emits a response with an empty or malformed
JSON body between the expected delimiters, or syntactically valid JSON that is
missing required schema fields, the parser raises a parse/schema exception and
the solve job dies.

This helper wraps the call/parse pair with a bounded retry loop. On parse
failure it regenerates with a correction prompt naming the validator error
and the expected delimiter shape.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TypeVar

from autocontext.agents.types import LlmFn

logger = logging.getLogger(__name__)

T = TypeVar("T")
_PARSER_RETRY_EXCEPTIONS = (json.JSONDecodeError, ValueError, KeyError, TypeError)


def design_with_parse_retry(
    *,
    llm_fn: LlmFn,
    system_prompt: str,
    user_prompt: str,
    parser: Callable[[str], T],
    delimiter_hint: str,
    max_retries: int = 2,
) -> T:
    """Call ``llm_fn`` and ``parser``, retrying on parse failures.

    On each attempt:
    - Call ``llm_fn(system_prompt, effective_user_prompt)``
    - Call ``parser(response)``
    - If parser returns a value → return it
    - If parser raises a parse/schema exception → build correction prompt, loop
    - If exhausted → raise ``ValueError`` with all attempts' errors

    Total attempts = ``max_retries + 1``. Default ``max_retries=2`` (3 attempts).

    ``delimiter_hint`` is embedded verbatim in the correction prompt so the LLM
    sees which token pair to wrap its JSON in.

    Raises:
        ValueError: when parse still fails after ``max_retries + 1`` attempts.
    """
    total_attempts = max_retries + 1
    errors: list[str] = []
    effective_user_prompt = user_prompt

    for attempt in range(total_attempts):
        response = llm_fn(system_prompt, effective_user_prompt)
        try:
            return parser(response)
        except _PARSER_RETRY_EXCEPTIONS as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            errors.append(error_text)

            if attempt < total_attempts - 1:
                logger.warning(
                    "designer parse failed on attempt %d/%d: %s; retrying with correction prompt",
                    attempt + 1,
                    total_attempts,
                    error_text,
                )
                effective_user_prompt = _build_correction_prompt(
                    original_user_prompt=user_prompt,
                    error_message=error_text,
                    delimiter_hint=delimiter_hint,
                )

    raise ValueError(
        f"designer parse failed after {total_attempts} attempts. "
        f"Errors per attempt: {errors}"
    )


def _build_correction_prompt(
    *,
    original_user_prompt: str,
    error_message: str,
    delimiter_hint: str,
) -> str:
    """Build the retry user prompt after a parse failure."""
    return (
        "Your previous response could not be parsed as valid JSON.\n\n"
        "Original request:\n"
        f"{original_user_prompt}\n\n"
        f"Parse error: {error_message}\n\n"
        "Please regenerate your response. The JSON block MUST be:\n"
        f"- wrapped in the specified delimiters: {delimiter_hint}\n"
        "- non-empty between the delimiters\n"
        "- valid JSON (no trailing commas, properly quoted keys, escaped newlines in strings)\n"
        "- match the schema from the system prompt exactly"
    )
