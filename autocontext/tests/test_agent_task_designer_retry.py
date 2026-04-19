"""AC-574 — retry-with-feedback loop when validate_intent catches designer drift."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import pytest

from autocontext.scenarios.custom.agent_task_designer import (
    SPEC_END,
    SPEC_START,
    design_validated_agent_task,
)
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

# --- Fixtures ---

_VALID_TEXT_SPEC = AgentTaskSpec(
    task_prompt="Write a haiku about distributed systems.",
    judge_rubric="Score syllable accuracy (5-7-5), relevance, and imagery 0-1 each.",
    output_format="free_text",
    judge_model="",
)

_INVALID_CODE_FOR_TEXT_DESCRIPTION = AgentTaskSpec(
    task_prompt="Implement a Python function that writes a haiku.",
    judge_rubric="Score code quality, test coverage, and documentation.",
    output_format="code",  # triggers format-mismatch against text description
    judge_model="",
)

_TEXT_DESCRIPTION = "Write a haiku about distributed systems."


def _spec_response(spec: AgentTaskSpec) -> str:
    """Build the LLM response format expected by parse_agent_task_spec."""
    data = {
        "task_prompt": spec.task_prompt,
        "judge_rubric": spec.judge_rubric,
        "output_format": spec.output_format,
        "judge_model": spec.judge_model,
    }
    return f"prefix\n{SPEC_START}\n{json.dumps(data)}\n{SPEC_END}\nsuffix"


def _scripted_llm_fn(
    responses: list[str],
) -> Callable[[str, str], str]:
    """Returns an llm_fn stub that yields each response in order and records calls."""
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


# --- Tests ---


class TestDesignValidatedAgentTask:
    def test_happy_path_no_retry_on_valid_spec(self) -> None:
        llm_fn = _scripted_llm_fn([_spec_response(_VALID_TEXT_SPEC)])

        spec = design_validated_agent_task(_TEXT_DESCRIPTION, llm_fn)

        assert spec.task_prompt == _VALID_TEXT_SPEC.task_prompt
        assert spec.output_format == "free_text"
        assert len(llm_fn.calls) == 1  # type: ignore[attr-defined]

    def test_retries_once_then_succeeds(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # First attempt: invalid (code format for a text description).
        # Second attempt: valid.
        llm_fn = _scripted_llm_fn([
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_VALID_TEXT_SPEC),
        ])

        with caplog.at_level(
            logging.WARNING, logger="autocontext.scenarios.custom.agent_task_designer"
        ):
            spec = design_validated_agent_task(_TEXT_DESCRIPTION, llm_fn)

        assert spec.output_format == "free_text"
        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, (
            f"expected one retry warning, got {[r.message for r in warnings]}"
        )
        assert "attempt 1" in warnings[0].getMessage()

    def test_raises_after_max_retries_exhausted(self) -> None:
        # All 3 attempts return the invalid spec.
        llm_fn = _scripted_llm_fn([
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
        ])

        with pytest.raises(ValueError) as excinfo:
            design_validated_agent_task(_TEXT_DESCRIPTION, llm_fn, max_retries=2)

        message = str(excinfo.value)
        assert "intent validation failed after 3 attempts" in message
        assert "format mismatch" in message  # validator's error text present
        assert len(llm_fn.calls) == 3  # type: ignore[attr-defined]

    def test_retry_correction_prompt_contains_validator_errors(self) -> None:
        llm_fn = _scripted_llm_fn([
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_VALID_TEXT_SPEC),
        ])

        design_validated_agent_task(_TEXT_DESCRIPTION, llm_fn)

        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]
        _system, retry_user_prompt = llm_fn.calls[1]  # type: ignore[attr-defined]

        assert "Please regenerate" in retry_user_prompt
        assert "Validation errors" in retry_user_prompt
        assert "format mismatch" in retry_user_prompt
        # The original description must still be present so the LLM has task context.
        assert _TEXT_DESCRIPTION in retry_user_prompt
        # Hints block should be present.
        assert "output_format='free_text'" in retry_user_prompt

    def test_max_retries_zero_makes_exactly_one_attempt(self) -> None:
        llm_fn = _scripted_llm_fn([
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
        ])

        with pytest.raises(ValueError) as excinfo:
            design_validated_agent_task(_TEXT_DESCRIPTION, llm_fn, max_retries=0)

        assert "intent validation failed after 1 attempts" in str(excinfo.value)
        assert len(llm_fn.calls) == 1  # type: ignore[attr-defined]

    def test_max_retries_three_allows_four_total_attempts(self) -> None:
        # First 3 invalid, 4th valid.
        llm_fn = _scripted_llm_fn([
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_INVALID_CODE_FOR_TEXT_DESCRIPTION),
            _spec_response(_VALID_TEXT_SPEC),
        ])

        spec = design_validated_agent_task(
            _TEXT_DESCRIPTION, llm_fn, max_retries=3
        )

        assert spec.output_format == "free_text"
        assert len(llm_fn.calls) == 4  # type: ignore[attr-defined]
