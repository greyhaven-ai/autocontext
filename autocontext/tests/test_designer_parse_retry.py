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

    def test_retries_once_on_json_decode_error_then_succeeds(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # First attempt returns empty (JSONDecodeError).
        # Second attempt returns valid JSON.
        llm_fn = _scripted_llm_fn(["", json.dumps({"ok": True})])

        with caplog.at_level(
            logging.WARNING, logger="autocontext.scenarios.custom.designer_retry"
        ):
            result = design_with_parse_retry(
                llm_fn=llm_fn,
                system_prompt=_SYSTEM,
                user_prompt=_USER,
                parser=_strict_dict_parser,
                delimiter_hint=_DELIMITERS,
            )

        assert result == {"ok": True}
        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "attempt 1/3" in warnings[0].getMessage()

    def test_retries_once_on_value_error_then_succeeds(self) -> None:
        # First attempt returns prose (no JSON object; ValueError).
        # Second attempt returns valid JSON.
        llm_fn = _scripted_llm_fn([
            "I am just prose with no JSON.",
            json.dumps({"ok": True}),
        ])

        result = design_with_parse_retry(
            llm_fn=llm_fn,
            system_prompt=_SYSTEM,
            user_prompt=_USER,
            parser=_strict_dict_parser,
            delimiter_hint=_DELIMITERS,
        )

        assert result == {"ok": True}
        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]

    def test_raises_after_max_retries_exhausted(self) -> None:
        llm_fn = _scripted_llm_fn(["", "", ""])

        with pytest.raises(ValueError) as excinfo:
            design_with_parse_retry(
                llm_fn=llm_fn,
                system_prompt=_SYSTEM,
                user_prompt=_USER,
                parser=_strict_dict_parser,
                delimiter_hint=_DELIMITERS,
                max_retries=2,
            )

        message = str(excinfo.value)
        assert "designer parse failed after 3 attempts" in message
        assert "JSONDecodeError" in message or "ValueError" in message
        assert len(llm_fn.calls) == 3  # type: ignore[attr-defined]

    def test_correction_prompt_contains_delimiter_hint_and_original_user_prompt(
        self,
    ) -> None:
        llm_fn = _scripted_llm_fn(["", json.dumps({"ok": True})])

        design_with_parse_retry(
            llm_fn=llm_fn,
            system_prompt=_SYSTEM,
            user_prompt=_USER,
            parser=_strict_dict_parser,
            delimiter_hint=_DELIMITERS,
        )

        _system, retry_user_prompt = llm_fn.calls[1]  # type: ignore[attr-defined]
        assert _DELIMITERS in retry_user_prompt
        assert _USER in retry_user_prompt
        assert "non-empty between the delimiters" in retry_user_prompt

    def test_max_retries_zero_makes_exactly_one_attempt(self) -> None:
        llm_fn = _scripted_llm_fn([""])

        with pytest.raises(ValueError) as excinfo:
            design_with_parse_retry(
                llm_fn=llm_fn,
                system_prompt=_SYSTEM,
                user_prompt=_USER,
                parser=_strict_dict_parser,
                delimiter_hint=_DELIMITERS,
                max_retries=0,
            )

        assert "designer parse failed after 1 attempts" in str(excinfo.value)
        assert len(llm_fn.calls) == 1  # type: ignore[attr-defined]
