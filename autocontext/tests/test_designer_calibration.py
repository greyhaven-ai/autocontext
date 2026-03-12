"""Tests for mandatory calibration examples in agent task designer."""

from __future__ import annotations

from autocontext.scenarios.custom.agent_task_designer import (
    _EXAMPLE_SPEC,
    AGENT_TASK_DESIGNER_SYSTEM,
)


def test_example_spec_has_calibration_examples() -> None:
    """_EXAMPLE_SPEC must include calibration_examples with at least 2 items."""
    assert _EXAMPLE_SPEC["calibration_examples"] is not None
    assert isinstance(_EXAMPLE_SPEC["calibration_examples"], list)
    assert len(_EXAMPLE_SPEC["calibration_examples"]) >= 2


def test_prompt_requires_calibration() -> None:
    """System prompt must state calibration examples are mandatory."""
    assert "MUST include at least 2 calibration" in AGENT_TASK_DESIGNER_SYSTEM


def test_calibration_examples_have_required_fields() -> None:
    """Each calibration example must have human_score, human_notes, agent_output."""
    required_fields = {"human_score", "human_notes", "agent_output"}
    for example in _EXAMPLE_SPEC["calibration_examples"]:
        assert isinstance(example, dict)
        for field in required_fields:
            assert field in example, f"Missing field '{field}' in calibration example"
