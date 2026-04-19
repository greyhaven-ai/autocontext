"""AC-575 — end-to-end: design_simulation and design_artifact_editing recover from empty LLM body."""
from __future__ import annotations

import json
from collections.abc import Callable

from autocontext.scenarios.custom.artifact_editing_designer import (
    ARTIFACT_SPEC_END,
    ARTIFACT_SPEC_START,
    design_artifact_editing,
)
from autocontext.scenarios.custom.simulation_designer import (
    SIM_SPEC_END,
    SIM_SPEC_START,
    design_simulation,
)


def _scripted_llm_fn(responses: list[str]) -> Callable[[str, str], str]:
    calls: list[tuple[str, str]] = []

    def fn(system: str, user: str) -> str:
        if not responses:
            raise AssertionError("llm_fn called more times than responses available")
        calls.append((system, user))
        return responses.pop(0)

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


_VALID_SIMULATION_JSON = {
    "description": "A test simulation",
    "environment_description": "An environment with two variables",
    "initial_state_description": "Both start at zero",
    "success_criteria": ["Variable A reaches 10"],
    "failure_modes": ["Variable A goes negative"],
    "actions": [
        {
            "name": "increment_a",
            "description": "Add 1 to variable A",
            "parameters": {},
            "preconditions": [],
            "effects": ["Variable A increases by 1"],
        }
    ],
    "max_steps": 5,
}

_VALID_ARTIFACT_EDITING_JSON = {
    "task_description": "Edit a config file to enable debug mode.",
    "rubric": "Score correctness and minimal side effects.",
    "validation_rules": ["debug = true is set"],
    "artifacts": [
        {
            "path": "app.yaml",
            "content": "debug: false\nport: 8080\n",
            "content_type": "yaml",
            "metadata": {},
        }
    ],
}


def _empty_sim_response() -> str:
    return f"prefix\n{SIM_SPEC_START}\n{SIM_SPEC_END}\nsuffix"


def _valid_sim_response() -> str:
    return (
        f"prefix\n{SIM_SPEC_START}\n{json.dumps(_VALID_SIMULATION_JSON)}\n{SIM_SPEC_END}\nsuffix"
    )


def _empty_artifact_response() -> str:
    return f"prefix\n{ARTIFACT_SPEC_START}\n{ARTIFACT_SPEC_END}\nsuffix"


def _valid_artifact_response() -> str:
    return (
        f"prefix\n{ARTIFACT_SPEC_START}\n{json.dumps(_VALID_ARTIFACT_EDITING_JSON)}\n{ARTIFACT_SPEC_END}\nsuffix"
    )


class TestDesignerParseRetryIntegration:
    def test_design_simulation_retries_on_empty_spec_block(self) -> None:
        """AC-276 repro: first response has empty content between SIM delimiters,
        second response has valid JSON. design_simulation must succeed."""
        llm_fn = _scripted_llm_fn([_empty_sim_response(), _valid_sim_response()])

        spec = design_simulation("A test description.", llm_fn)

        assert spec.description == _VALID_SIMULATION_JSON["description"]
        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]

    def test_design_artifact_editing_retries_on_empty_spec_block(self) -> None:
        """AC-269 repro: first response has empty content between ARTIFACT delimiters,
        second response has valid JSON. design_artifact_editing must succeed."""
        llm_fn = _scripted_llm_fn([_empty_artifact_response(), _valid_artifact_response()])

        spec = design_artifact_editing("A test description.", llm_fn)

        assert spec.task_description == _VALID_ARTIFACT_EDITING_JSON["task_description"]
        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]
