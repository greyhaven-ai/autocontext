"""AC-575 — shared parse-retry helper for custom scenario designers."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest

from autocontext.scenarios.custom.designer_retry import design_with_parse_retry


# --- Shared fixtures ---

def _scripted_llm_fn(responses: list[str]) -> Callable[[str, str], str]:
    calls: list[tuple[str, str]] = []

    def fn(system: str, user: str) -> str:
        if not responses:
            raise AssertionError(
                f"llm_fn called more times than responses available; "
                f"previous calls: {len(calls)}"
            )
        calls.append((system, user))
        return responses.pop(0)

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


_SYSTEM = "You are a test designer."
_USER = "User description:\nWrite something."
_DELIMITERS = "<!-- TEST_SPEC_START --> ... <!-- TEST_SPEC_END -->"


def _strict_dict_parser(text: str) -> dict[str, Any]:
    """Parser that expects a JSON dict; raises on empty or malformed input.

    Mirrors the failure shape of real parse_X_spec: ValueError on missing
    delimiter, JSONDecodeError on empty/malformed JSON body.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty response")
    if not text.startswith("{"):
        raise ValueError("response does not start with JSON object")
    return json.loads(text)


# --- Tests ---


class TestDesignWithParseRetry:
    def test_happy_path_returns_parser_value_on_first_attempt(self) -> None:
        llm_fn = _scripted_llm_fn([json.dumps({"ok": True})])

        result = design_with_parse_retry(
            llm_fn=llm_fn,
            system_prompt=_SYSTEM,
            user_prompt=_USER,
            parser=_strict_dict_parser,
            delimiter_hint=_DELIMITERS,
        )

        assert result == {"ok": True}
        assert len(llm_fn.calls) == 1  # type: ignore[attr-defined]
